"""The Klipvi API the web app talks to.

    uvicorn backend.api:app --reload --port 8000

Every route needs a signed-in user (Supabase token, backend/auth.py) and checks that the user is a member of the
workspace the data belongs to. Writes go through here (the database lets customers read only). Internal fields
(our cost, the AI's own strength rating, job errors) are never returned.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import queue as Q
from . import storage
from .auth import User, current_user
from .db import connect

app = FastAPI(title="Klipvi API", version="0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("KLIPVI_WEB_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",") if o.strip()],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def db():
    with connect() as conn:
        yield conn


def youtube_id(url: str) -> str | None:
    """The 11-character video id of a youtube.com / youtu.be link (None if it is not one)."""
    url = (url or "").strip()
    if VIDEO_ID.match(url):
        return url
    p = urlparse(url if "://" in url else f"https://{url}")
    host = (p.netloc or "").lower().removeprefix("www.").removeprefix("m.")
    vid = ""
    if host == "youtu.be":
        vid = p.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        vid = (parse_qs(p.query).get("v") or [""])[0]
        if not vid:
            parts = p.path.strip("/").split("/")
            vid = parts[1] if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v") else ""
    return vid if VIDEO_ID.match(vid or "") else None


def role_in(conn, workspace_id, user: User) -> str:
    """The user's role in the workspace, or 404 (never reveal that a workspace exists to non-members)."""
    r = conn.execute("select role from public.members where workspace_id = %s and user_id = %s",
                     (workspace_id, user.id)).fetchone()
    if not r:
        raise HTTPException(404, "Workspace not found.")
    return r["role"]


def episode_for(conn, episode_id, user: User) -> dict:
    ep = conn.execute("select e.* from public.episodes e join public.members m on m.workspace_id = e.workspace_id "
                      "where e.id = %s and m.user_id = %s", (episode_id, user.id)).fetchone()
    if not ep:
        raise HTTPException(404, "Episode not found.")
    return ep


def _num(v):
    return None if v is None else float(v)


def episode_out(ep: dict, clip_count: int | None = None) -> dict:
    return {"id": str(ep["id"]), "workspace_id": str(ep["workspace_id"]), "title": ep["title"],
            "video_id": ep["video_id"], "source_url": ep["source_url"], "status": ep["status"], "step": ep["step"],
            "message": ep["message"], "verdict": ep["verdict"], "verdict_why": ep["verdict_why"],
            "length_s": _num(ep["length_s"]), "minutes": _num(ep["minutes"]), "minutes_state": ep["minutes_state"],
            "created_at": ep["created_at"].isoformat(), "finished_at": ep["finished_at"].isoformat() if ep["finished_at"] else None,
            **({"clip_count": clip_count} if clip_count is not None else {})}


# ----------------------------------------------------------------------------------------------- routes
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/me")
def me(user: User = Depends(current_user), conn=Depends(db)):
    rows = conn.execute(
        "select w.id, w.name, w.kind, w.plan, w.minutes_left, w.minutes_held, w.default_template_id, m.role "
        "from public.members m join public.workspaces w on w.id = m.workspace_id where m.user_id = %s "
        "order by (w.kind = 'personal') desc, w.created_at", (user.id,)).fetchall()
    return {"user": {"id": user.id, "email": user.email},
            "workspaces": [{"id": str(r["id"]), "name": r["name"], "kind": r["kind"], "plan": r["plan"], "role": r["role"],
                            "minutes_left": float(r["minutes_left"]), "minutes_held": float(r["minutes_held"]),
                            "minutes_free": float(r["minutes_left"]) - float(r["minutes_held"]),
                            "default_template_id": str(r["default_template_id"]) if r["default_template_id"] else None}
                           for r in rows]}


@app.get("/workspaces/{workspace_id}/episodes")
def list_episodes(workspace_id: str, user: User = Depends(current_user), conn=Depends(db)):
    role_in(conn, workspace_id, user)
    rows = conn.execute(
        "select e.*, (select count(*) from public.clips c where c.episode_id = e.id) as clip_count "
        "from public.episodes e where e.workspace_id = %s order by e.created_at desc limit 200", (workspace_id,)).fetchall()
    return [episode_out(r, r["clip_count"]) for r in rows]


class NewEpisode(BaseModel):
    source_url: str


@app.post("/workspaces/{workspace_id}/episodes", status_code=201)
def submit_episode(workspace_id: str, body: NewEpisode, user: User = Depends(current_user), conn=Depends(db)):
    role_in(conn, workspace_id, user)
    vid = youtube_id(body.source_url)
    if not vid:
        raise HTTPException(422, "Paste a YouTube link to one episode, like https://www.youtube.com/watch?v=…")
    url = f"https://www.youtube.com/watch?v={vid}"
    from . import pipeline as P
    try:
        secs = P.source_length_s(vid, url)
    except Exception:
        raise HTTPException(422, "We couldn't open that YouTube video. Check that the link works and the video is public.")
    try:
        out = Q.submit(conn, workspace_id, url, vid, secs, submitted_by=user.id)
    except Q.NotEnoughMinutes as e:
        raise HTTPException(402, {"message": str(e), "needs": e.needs, "free": max(e.free, 0)})
    return episode_out(out["episode"], 0)


@app.get("/episodes/{episode_id}")
def get_episode(episode_id: str, user: User = Depends(current_user), conn=Depends(db)):
    ep = episode_for(conn, episode_id, user)
    clips = conn.execute(
        "select id, number, rank, post_first, title, hook, posting_text, why, start_s, end_s, length_s, draft "
        "from public.clips where episode_id = %s order by rank nulls last, number", (episode_id,)).fetchall()
    renders = conn.execute(
        "select r.id, r.clip_id, r.style, r.template_id, r.settings, r.status, r.size_mb from public.renders r "
        "join public.clips c on c.id = r.clip_id where c.episode_id = %s", (episode_id,)).fetchall()
    by_clip: dict = {}
    for r in renders:
        by_clip.setdefault(str(r["clip_id"]), []).append(
            {"id": str(r["id"]), "style": r["style"], "template_id": str(r["template_id"]) if r["template_id"] else None,
             "settings": r["settings"], "status": r["status"], "size_mb": _num(r["size_mb"])})
    return {**episode_out(ep, len(clips)),
            "clips": [{"id": str(c["id"]), "number": c["number"], "rank": c["rank"], "post_first": c["post_first"],
                       "title": c["title"], "hook": c["hook"], "posting_text": c["posting_text"], "why": c["why"],
                       "start_s": float(c["start_s"]), "end_s": float(c["end_s"]), "length_s": float(c["length_s"]),
                       "draft": c["draft"], "renders": by_clip.get(str(c["id"]), [])} for c in clips]}


class RenderRequest(BaseModel):
    style: str
    template_id: str | None = None
    settings: dict | None = None
    words: list | None = None        # edited caption words [{tok, start, end}] (clip clock), from the caption editor
    breaks: list | None = None       # word positions where the editor forces a new caption line


def clip_for(conn, clip_id, user: User) -> dict:
    c = conn.execute("select c.*, e.workspace_id, e.video_id, e.id as episode_id from public.clips c "
                     "join public.episodes e on e.id = c.episode_id join public.members m on m.workspace_id = e.workspace_id "
                     "where c.id = %s and m.user_id = %s", (clip_id, user.id)).fetchone()
    if not c:
        raise HTTPException(404, "Clip not found.")
    return c


def _edits(body, clip: dict):
    """(edited words or None, line splits) checked against the clip, or 422."""
    from selection_v2 import captions as C
    try:
        words = C.clean_words(body.words, float(clip["length_s"]) + 3.0) if body.words is not None else None
        n = len(words) if words is not None else 10 ** 6
        breaks = C.clean_breaks(body.breaks, n) if body.breaks else []
    except ValueError as e:
        raise HTTPException(422, str(e))
    return words, breaks


@app.get("/clips/{clip_id}/editor")
def clip_editor(clip_id: str, user: User = Depends(current_user), conn=Depends(db)):
    """Everything the caption editor needs: the clip, its spoken words on the clip's own clock (what the captions are
    made from), the saved draft, and its plain fit/crop videos."""
    from selection_v2 import captions as C
    from selection_v2 import flow_v2 as FV
    from selection_v2.inputs import load_inputs
    c = clip_for(conn, clip_id, user)
    cfg = FV.cfg_for(c["video_id"])
    flow = FV.load(c["video_id"], cfg) or {}
    fc = next((x for x in (flow.get("clips") or {}).get("clips") or [] if x["id"] == c["number"]), None)
    words = [{"tok": w["tok"], "start": w["start"], "end": w["end"]}
             for w in C.clip_words(flow, load_inputs(c["video_id"], cfg).words, fc)] if fc else []
    plain = conn.execute("select id, style from public.renders where clip_id = %s and settings_hash = %s and status = 'ready'",
                         (clip_id, Q.PLAIN)).fetchall()
    return {"clip": {"id": str(c["id"]), "episode_id": str(c["episode_id"]), "workspace_id": str(c["workspace_id"]),
                     "number": c["number"], "rank": c["rank"], "post_first": c["post_first"], "title": c["title"],
                     "why": c["why"], "length_s": float(c["length_s"])},
            "words": words, "draft": c["draft"], "videos": {r["style"]: str(r["id"]) for r in plain},
            "defaults": C.DEFAULTS, "default_pos_y": C.DEFAULT_POS_Y}


class Draft(BaseModel):
    style: str = "crop"
    template_id: str | None = None
    settings: dict
    words: list | None = None
    breaks: list | None = None


@app.put("/clips/{clip_id}/draft")
def save_draft(clip_id: str, body: Draft, user: User = Depends(current_user), conn=Depends(db)):
    """The editor's current state for this clip, kept so reopening the clip shows the edits."""
    import json as _json
    from selection_v2 import captions as C
    c = clip_for(conn, clip_id, user)
    if body.style not in ("crop", "fit"):
        raise HTTPException(422, "Choose crop or fit.")
    try:
        settings = C.clean_settings(body.settings)
    except ValueError as e:
        raise HTTPException(422, str(e))
    words, breaks = _edits(body, c)
    draft = {"style": body.style, "template_id": body.template_id, "settings": settings, "words": words, "breaks": breaks}
    conn.execute("update public.clips set draft = %s where id = %s", (_json.dumps(draft, ensure_ascii=False), clip_id))
    return draft


@app.post("/clips/{clip_id}/renders")
def request_render(clip_id: str, body: RenderRequest, user: User = Depends(current_user), conn=Depends(db)):
    """A captioned version of a clip: a template, the editor's settings, or a template with changes. Returns the
    render (ready at once when the same version was made before; else 'waiting', poll GET /renders/{id})."""
    from selection_v2 import captions as C
    clip = conn.execute("select c.id, e.workspace_id from public.clips c join public.episodes e on e.id = c.episode_id "
                        "join public.members m on m.workspace_id = e.workspace_id where c.id = %s and m.user_id = %s",
                        (clip_id, user.id)).fetchone()
    if not clip:
        raise HTTPException(404, "Clip not found.")
    if body.style not in ("crop", "fit"):
        raise HTTPException(422, "Choose crop or fit.")
    raw: dict = {}
    if body.template_id:
        t = conn.execute("select settings from public.caption_templates where id = %s and "
                         "(workspace_id is null or workspace_id = %s)", (body.template_id, clip["workspace_id"])).fetchone()
        if not t:
            raise HTTPException(404, "Caption style not found.")
        raw.update(t["settings"])
    raw.update(body.settings or {})
    if not raw:
        raise HTTPException(422, "Choose a caption style.")
    try:
        settings = C.clean_settings(raw)
    except ValueError as e:
        raise HTTPException(422, str(e))
    plain = conn.execute("select 1 from public.renders where clip_id = %s and style = %s and settings_hash = %s and "
                         "status = 'ready'", (clip_id, body.style, Q.PLAIN)).fetchone()
    if not plain:
        raise HTTPException(409, "This clip's video is still being made. Try again in a few minutes.")
    words, breaks = _edits(body, {"length_s": conn.execute("select length_s from public.clips where id = %s",
                                                         (clip_id,)).fetchone()["length_s"]})
    captions = {"words": words, "breaks": breaks} if (words is not None or breaks) else None
    r = Q.request_render(conn, clip_id, body.style, body.template_id, settings,
                         C.settings_hash(settings, words, breaks), captions)
    return render_out(r)


def render_out(r: dict) -> dict:
    return {"id": str(r["id"]), "clip_id": str(r["clip_id"]), "style": r["style"],
            "template_id": str(r["template_id"]) if r["template_id"] else None, "settings": r["settings"],
            "status": r["status"], "size_mb": _num(r["size_mb"])}


@app.get("/renders/{render_id}")
def get_render(render_id: str, user: User = Depends(current_user), conn=Depends(db)):
    r = conn.execute("select r.* from public.renders r join public.clips c on c.id = r.clip_id join public.episodes e "
                     "on e.id = c.episode_id join public.members m on m.workspace_id = e.workspace_id "
                     "where r.id = %s and m.user_id = %s", (render_id, user.id)).fetchone()
    if not r:
        raise HTTPException(404, "Video not found.")
    return render_out(r)


class TransliterateRequest(BaseModel):
    text: str


@app.post("/transliterate")
def transliterate(body: TransliterateRequest, user: User = Depends(current_user)):
    """Telugu spellings for a word typed in English letters ("kalpavruksham" -> ["కల్పవృక్షం", ...]), for typing
    Telugu in the caption editor. Asks the IndicXlit service (services/indicxlit, AI4Bharat, free, on our server;
    INDICXLIT_URL). Never fails the editor: if the service is down or slow, the answer is no suggestions."""
    import httpx
    word = (body.text or "").strip()
    if not word or len(word) > 40 or not re.fullmatch(r"[A-Za-z]+", word):
        return {"suggestions": []}
    url = os.environ.get("INDICXLIT_URL", "http://localhost:8010").rstrip("/") + "/transliterate"
    try:
        r = httpx.post(url, json={"text": word, "lang": "te"}, timeout=httpx.Timeout(4.0, connect=1.5))
        r.raise_for_status()
        raw = r.json().get("suggestions", [])
        out = [x for x in raw if isinstance(x, str) and x.strip()] if isinstance(raw, list) else []
    except Exception:
        out = []
    return {"suggestions": out[:5]}


@app.get("/caption-templates")
def caption_templates(workspace_id: str | None = None, user: User = Depends(current_user), conn=Depends(db)):
    if workspace_id:
        role_in(conn, workspace_id, user)
    rows = conn.execute("select id, workspace_id, name, label, settings from public.caption_templates "
                        "where workspace_id is null or workspace_id = %s order by workspace_id nulls first, sort, name",
                        (workspace_id,)).fetchall()
    return [{"id": str(r["id"]), "name": r["name"], "label": r["label"], "settings": r["settings"],
             "shared": r["workspace_id"] is None} for r in rows]


LINK_TTL_S = 3600


def _file_secret() -> bytes:
    from .db import ROOT
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    sec = os.environ.get("KLIPVI_FILE_SECRET", "")
    if len(sec) < 32:
        raise RuntimeError("KLIPVI_FILE_SECRET is not set (.env)")
    return sec.encode()


def _sign(render_id: str, exp: int, dl: int, kind: str = "video") -> str:
    return hmac.new(_file_secret(), f"{render_id}|{exp}|{dl}|{kind}".encode(), hashlib.sha256).hexdigest()


def _signed(render_id: str, exp: int, dl: int, kind: str) -> str:
    q = {"exp": exp, "dl": dl, "sig": _sign(render_id, exp, dl, kind)}
    if kind != "video":
        q["kind"] = kind
    return f"/files/{render_id}?" + urlencode(q)


@app.get("/renders/{render_id}/link")
def render_link(render_id: str, download: bool = False, user: User = Depends(current_user), conn=Depends(db)):
    """A link to play (or download) one video for the next hour; a video tag cannot send the sign-in token itself.
    (With Cloudflare R2 this becomes R2's own signed link.)"""
    r = conn.execute(
        "select r.status, r.file from public.renders r join public.clips c on c.id = r.clip_id "
        "join public.episodes e on e.id = c.episode_id join public.members m on m.workspace_id = e.workspace_id "
        "where r.id = %s and m.user_id = %s", (render_id, user.id)).fetchone()
    if not r:
        raise HTTPException(404, "Video not found.")
    if r["status"] != "ready" or not r["file"] or not storage.exists(r["file"]):
        raise HTTPException(409, "This video is not ready yet.")
    exp, dl = int(time.time()) + LINK_TTL_S, int(download)
    from .pipeline import poster_key
    poster = _signed(render_id, exp, 0, "poster") if storage.exists(poster_key(r["file"])) else None
    return {"url": _signed(render_id, exp, dl, "video"), "poster": poster, "expires_at": exp}


@app.get("/files/{render_id}")
def render_file(render_id: str, exp: int, dl: int, sig: str, kind: str = "video", conn=Depends(db)):
    if kind not in ("video", "poster") or exp < time.time() or not hmac.compare_digest(sig, _sign(render_id, exp, dl, kind)):
        raise HTTPException(403, "This link has expired. Open the clip again.")
    r = conn.execute(
        "select r.file, r.status, r.style, c.rank, c.title, e.title as ep_title from public.renders r "
        "join public.clips c on c.id = r.clip_id join public.episodes e on e.id = c.episode_id where r.id = %s",
        (render_id,)).fetchone()
    if not r or r["status"] != "ready" or not r["file"] or not storage.exists(r["file"]):
        raise HTTPException(404, "Video not found.")
    if kind == "poster":
        from .pipeline import poster_key
        if not storage.exists(poster_key(r["file"])):
            raise HTTPException(404, "No picture for this video.")
        return FileResponse(storage.path(poster_key(r["file"])), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})
    name = re.sub(r"[^\w\s.-]", "", f"{r['ep_title'] or 'Episode'} - {r['rank']}. {r['title'] or 'clip'}", flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()[:120] + f" ({r['style']}).mp4"
    return FileResponse(storage.path(r["file"]), media_type="video/mp4", filename=name,
                        content_disposition_type="attachment" if dl else "inline")
