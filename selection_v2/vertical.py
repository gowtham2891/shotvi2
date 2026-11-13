"""Vertical 9:16 clips for Reels / Shorts (founder, 2026-11-10): every clip in TWO styles, the user selects one.

  fit    the whole 16:9 picture in the middle, a blurred, zoomed copy of the same video above and below. Nothing is ever
         cut off; no decision is made.
  crop   a 9:16 slice of the frame that follows the person who matters ("approach B", founder, 2026-11-10: no
         "biggest face" fallback). The clip's played pieces are split at the shot changes (runs/video/<id>/shots.json)
         and each shot gets ONE fixed slice position (a moving slice inside a shot looks shaky). Who matters comes from
         what the video step (step 5) already answered for every line: who TALKS and who is ON SCREEN. Faces found in a
         few frames of the shot (OpenCV YuNet, free, on our computer) are matched to the episode's people by face
         signature (OpenCV SFace) against reference faces taken from lines where that person is alone on screen. The
         slice centres on the talker's face; when the talker is off screen (a reaction shot), on the person on screen;
         when every matched face fits, on all of them. A shot where no face matches a known person is UNDECIDED: the
         slice centres on all faces found (or the frame's middle) and the plan says so, so it can be checked.

Every shot's choice and reason are kept in ``<clip>_crop_plan.json`` and drawn on ``<clip>_crop_debug.mp4`` (the
16:9 video with the chosen slice boxed). Both styles keep the clip's pieces exactly as played (cold open first, cuts
left out) with the same small pads as ``eval/naming/render_clips.py`` so no word is clipped. Output 1080x1920; the
episode sources are 720p today, so the crop style is upscaled from a 405-px-wide slice (soft); 1080p sources fix that.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .config import ROOT

OUT_W, OUT_H = 1080, 1920
PAD_IN, PAD_OUT = 0.1, 0.3
MODELS = ROOT / "models"
DET_MODEL = MODELS / "face_detection_yunet_2023mar.onnx"
REC_MODEL = MODELS / "face_recognition_sface_2021dec.onnx"
MODEL_URLS = {
    DET_MODEL: "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    REC_MODEL: "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
}
MIN_SCORE = 0.6                   # face detector confidence
SAMPLES = (0.25, 0.5, 0.75)       # where in each shot the faces are looked for
SHORT_SHOT_S = 0.6                # a shot shorter than this keeps the previous shot's slice (no jump for a flash)
TRACK = False                     # founder, 2026-11-10: tracking inside a shot made no visible difference; one fixed slice per shot
TRACK_FPS = 4                     # frames looked at per second inside a shot (tracking a moving person, when TRACK)
DEAD_ZONE = 0.12                  # the slice only moves when the face leaves the middle 2*12% of it
MAX_SPEED = 0.6                   # the slice moves at most this fraction of its width per second (a glide, no jump)
MATCH_MIN = 0.45                  # cosine similarity to a person's reference faces to count as that person
MATCH_MARGIN = 0.08               # ... and at least this much above the next best person (else undecided)
REFS_PER_PERSON = 8
REF_MIN_LINE_S = 0.8


def ensure_models() -> None:
    """Download the two small OpenCV models (free, ~39 MB) when missing (models/*.onnx are not in git)."""
    import urllib.request
    MODELS.mkdir(exist_ok=True)
    for p, url in MODEL_URLS.items():
        if not p.exists() or p.stat().st_size < 10_000:
            urllib.request.urlretrieve(url, p)


def probe(src: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", str(src)], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


PICTURE_SAMPLES = 10              # moments sampled across the episode to find the picture area
PICTURE_MIN_BAR = 0.02            # bars thinner than 2% of the frame are ignored (no trim)


def choose_box(boxes: list[tuple[int, int, int, int]], W: int, H: int) -> tuple[int, int, int, int]:
    """The picture area from per-sample ``cropdetect`` boxes (x, y, w, h): the most common box (dark scenes make the
    detector cut into the picture, so a rare smaller box is ignored); ties go to the larger box. Bars thinner than
    PICTURE_MIN_BAR are ignored; a box smaller than half the frame is not trusted (full frame)."""
    if not boxes:
        return 0, 0, W, H
    from collections import Counter
    count = Counter(boxes)
    box = max(count, key=lambda b: (count[b], b[2] * b[3]))
    x, y, w, h = box
    if w * h < 0.5 * W * H:
        return 0, 0, W, H
    if (W - w) < PICTURE_MIN_BAR * W:
        x, w = 0, W
    if (H - h) < PICTURE_MIN_BAR * H:
        y, h = 0, H
    return x, y, w, h


def _cropdetect(src: Path, t: float) -> tuple[int, int, int, int] | None:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{t:.2f}", "-i", str(src), "-t", "2", "-vf",
                          "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"], capture_output=True, text=True).stderr
    found = [m for m in out.split("crop=")[1:]]
    if not found:
        return None
    w, h, x, y = (int(v) for v in found[-1].split()[0].split(":")[:4])
    return x, y, w, h


def picture_box(vid: str, src: Path) -> tuple[int, int, int, int]:
    """The real picture inside the frame (x, y, w, h in ``src`` pixels), without black bars baked into the video
    (web series are often shot wider than 16:9). Measured once per episode on PICTURE_SAMPLES moments and saved as
    fractions in runs/video/<id>/picture.json, so the 720p and 1080p files share it."""
    W, H = probe(src)
    p = ROOT / "runs" / "video" / vid / "picture.json"
    if p.exists():
        f = json.loads(p.read_text(encoding="utf-8"))
    else:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(src)], capture_output=True, text=True).stdout.strip() or 0)
        times = [dur * (i + 1) / (PICTURE_SAMPLES + 1) for i in range(PICTURE_SAMPLES)] if dur else []
        boxes = [b for b in (_cropdetect(src, t) for t in times) if b]
        x, y, w, h = choose_box(boxes, W, H)
        f = {"x": x / W, "y": y / H, "w": w / W, "h": h / H, "measured_on": f"{src.name} {W}x{H}",
             "samples": [list(b) for b in boxes]}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(f, indent=1), encoding="utf-8")
    even = lambda v: int(round(v / 2)) * 2
    x, y, w, h = even(f["x"] * W), even(f["y"] * H), even(f["w"] * W), even(f["h"] * H)
    return x, y, min(w, W - x), min(h, H - y)


def shot_cuts(vid: str) -> list[float]:
    p = ROOT / "runs" / "video" / vid / "shots.json"
    try:
        return sorted(float(x) for x in json.loads(p.read_text(encoding="utf-8")).get("cuts") or [])
    except (OSError, ValueError):
        return []


def padded(pieces: list[dict]) -> list[tuple[float, float]]:
    return [(max(0.0, float(p["start"]) - PAD_IN), float(p["end"]) + PAD_OUT) for p in pieces]


def shots_in(a: float, b: float, cuts: list[float]) -> list[tuple[float, float]]:
    """[a, b] split at the shot changes inside it."""
    inner = [c for c in cuts if a < c < b]
    edges = [a] + inner + [b]
    return list(zip(edges[:-1], edges[1:]))


# ----------------------------------------------------------------------------------------------- faces
class Faces:
    """YuNet detection + SFace signatures, on our computer."""

    def __init__(self):
        import cv2
        ensure_models()
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(str(DET_MODEL), "", (320, 320), MIN_SCORE)
        self.rec = cv2.FaceRecognizerSF.create(str(REC_MODEL), "")

    def frame(self, src: Path, t: float):
        with tempfile.TemporaryDirectory() as td:
            png = Path(td) / "f.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", str(png)], check=True)
            return self.cv2.imread(str(png))

    def along(self, src: Path, t0: float, t1: float, fps: float = TRACK_FPS) -> list[tuple[float, list[dict]]]:
        """Faces in frames every 1/fps s between t0 and t1 (one ffmpeg pass): [(t, faces)]."""
        out = []
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", str(src),
                            "-vf", f"fps={fps}", "-q:v", "3", f"{td}/%05d.jpg"], check=True)
            for i, jpg in enumerate(sorted(Path(td).glob("*.jpg"))):
                img = self.cv2.imread(str(jpg))
                if img is None:
                    continue
                h, w = img.shape[:2]
                self.det.setInputSize((w, h))
                _, rows = self.det.detect(img)
                fs = []
                for r in ([] if rows is None else rows):
                    fs.append({"x": float(r[0]), "y": float(r[1]), "w": float(r[2]), "h": float(r[3]), "score": float(r[-1]),
                               "sig": self.rec.feature(self.rec.alignCrop(img, r))})
                out.append((t0 + i / fps, fs))
        return out

    def at(self, src: Path, t: float) -> list[dict]:
        """Faces in the frame at time t: {x, y, w, h, score, sig} in source pixels."""
        img = self.frame(src, t)
        if img is None:
            return []
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, rows = self.det.detect(img)
        out = []
        for r in ([] if rows is None else rows):
            sig = self.rec.feature(self.rec.alignCrop(img, r))
            out.append({"x": float(r[0]), "y": float(r[1]), "w": float(r[2]), "h": float(r[3]), "score": float(r[-1]), "sig": sig})
        return out

    def similarity(self, a, b) -> float:
        return float(self.rec.match(a, b, self.cv2.FaceRecognizerSF_FR_COSINE))


class People:
    """Reference face signatures per person, from lines where that person is alone on screen (the video step's
    ``see``), preferring lines where they also talk. Saved in <run_dir>/face_refs.json (signatures as lists)."""

    def __init__(self, faces: Faces, refs: dict[str, list]):
        self.faces, self.refs = faces, refs

    @classmethod
    def build(cls, faces: Faces, facts: dict, src: Path, log=None) -> "People":
        import numpy as np
        log = log or (lambda *a: None)
        ip = facts["identity_pass"]
        times = {r["id"]: (float(r["start"]), float(r.get("end", r["start"]))) for r in facts["lines"]}
        cands: dict[str, list[tuple[int, float]]] = {}
        for lid, ans in ip["lines"].items():
            see = ans.get("see") or []
            if len(see) != 1 or lid not in times:
                continue
            a, b = times[lid]
            if b - a < REF_MIN_LINE_S:
                continue
            k = see[0]
            cands.setdefault(k, []).append((0 if (ans.get("talk") or []) == [k] else 1, (a + b) / 2))
        refs = {}
        for k, lst in cands.items():
            lst.sort()
            best = [t for _, t in lst]
            step = max(1, len(best) // (REFS_PER_PERSON * 2))
            sigs = []
            for t in best[::step]:
                fs = faces.at(src, t)
                if len(fs) == 1:
                    sigs.append(fs[0]["sig"].flatten().tolist())
                if len(sigs) >= REFS_PER_PERSON:
                    break
            if sigs:
                refs[k] = sigs
            log(f"faces: person {k}: {len(sigs)} reference faces from {len(lst)} alone-on-screen lines")
        return cls(faces, refs)

    def clean(self, log=None) -> "People":
        """Drop reference faces that do not belong: a face closer to another person's typical face than to its own
        (the video step's "alone on screen" label was wrong for that line), or far from its own person's typical face.
        The typical face = the reference most similar to the others of that person."""
        import numpy as np
        log = log or (lambda *a: None)
        arr = {k: [np.asarray(v, dtype="float32").reshape(1, -1) for v in vs] for k, vs in self.refs.items()}
        typical = {}
        for k, S in arr.items():
            if len(S) == 1:
                typical[k] = S[0]; continue
            means = [np.mean([self.faces.similarity(S[i], S[j]) for j in range(len(S)) if j != i]) for i in range(len(S))]
            typical[k] = S[int(np.argmax(means))]
        kept = {}
        for k, S in arr.items():
            good = []
            for v in S:
                own = self.faces.similarity(v, typical[k])
                other = max([self.faces.similarity(v, typical[o]) for o in arr if o != k] or [0.0])
                if own >= MATCH_MIN and own > other:
                    good.append(v.flatten().tolist())
            if len(good) < len(S):
                log(f"faces: person {k}: dropped {len(S) - len(good)} of {len(S)} reference faces that look like someone else")
            if good:
                kept[k] = good
        self.refs = kept
        return self

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.refs), encoding="utf-8")

    @classmethod
    def load(cls, faces: Faces, path: Path) -> "People":
        return cls(faces, json.loads(path.read_text(encoding="utf-8")))

    def who(self, sig) -> tuple[str | None, float, float]:
        """(person key or None, best similarity, margin over the next person)."""
        import numpy as np
        scores = []
        for k, sigs in self.refs.items():
            s = max(self.faces.similarity(sig, np.asarray(v, dtype="float32").reshape(1, -1)) for v in sigs)
            scores.append((s, k))
        if not scores:
            return None, 0.0, 0.0
        scores.sort(reverse=True)
        best, k = scores[0]
        margin = best - (scores[1][0] if len(scores) > 1 else 0.0)
        return (k if best >= MATCH_MIN and margin >= MATCH_MARGIN else None), best, margin


# ----------------------------------------------------------------------------------------------- the plan
def _span(fs: list[dict]) -> tuple[float, float]:
    return min(f["x"] for f in fs), max(f["x"] + f["w"] for f in fs)


def _centre(fs: list[dict], crop_w: int, W: int) -> tuple[float, bool]:
    """Centre x on all these faces when they fit in the slice; else on the biggest-area one. Returns (x, all_fit)."""
    left, right = _span(fs)
    if right - left <= crop_w:
        return (left + right) / 2, True
    big = max(fs, key=lambda f: f["w"] * f["h"])
    return big["x"] + big["w"] / 2, False


def decide(found: list[dict], talkers: list[str], seen: list[str], names: dict, crop_w: int, W: int,
           mid: float | None = None) -> tuple[list[str], float, str, bool]:
    """One shot's choice (approach B): the persons to follow (keys), a first slice centre (source px), the reason and
    whether it was decided. ``found`` = faces of the whole shot (every sampled frame), each with its matched person.
    ``mid`` = the picture's middle (default W / 2)."""
    nm = lambda k: names.get(k, k)
    mid = W / 2 if mid is None else mid
    known = {}
    for f in found:
        if f.get("person"):
            k = f["person"]
            if k not in known or f["w"] * f["h"] > known[k]["w"] * known[k]["h"]:
                known[k] = f
    if not found:
        return [], mid, "undecided: no face found, the middle", False
    for group, label in ((talkers, "talking"), (seen, "on screen while someone else talks")):
        on = [k for k in group if k in known]
        if on:
            x, fit = _centre([known[k] for k in on], crop_w, W)
            who = ", ".join(nm(k) for k in on)
            if not fit:
                on = [max(on, key=lambda k: known[k]["w"] * known[k]["h"])]
            return on, x, (f"{label}: {who}" if fit else f"{label}: {who} (too far apart: the larger face)"), True
    if known:
        on = list(known)
        x, fit = _centre(list(known.values()), crop_w, W)
        if not fit:
            on = [max(on, key=lambda k: known[k]["w"] * known[k]["h"])]
        return on, x, f"known person not listed on these lines: {', '.join(nm(k) for k in known)}", True
    x, fit = _centre(found, crop_w, W)
    if fit:
        return [], x, f"undecided: {len(found)} face(s) matched nobody, centred on all", False
    return [], mid, f"undecided: {len(found)} faces matched nobody and do not fit, the middle", False


def path(samples: list[tuple[float, float | None]], x0: float, crop_w: int, W: int, fps: float = TRACK_FPS) -> list[list[float]]:
    """The slice centre over a shot as keyframes [[t, x], ...] (linear glide between them) from the followed person's
    face centre per sample (None = not seen: hold). The slice moves only when the face leaves the dead zone in its
    middle, and never faster than MAX_SPEED slice-widths a second; runs of no movement are collapsed."""
    lo, hi = crop_w / 2, W - crop_w / 2
    dead, step = DEAD_ZONE * crop_w, MAX_SPEED * crop_w / fps
    c = min(max(x0, lo), hi)
    pts = []
    for t, p in samples:
        if p is not None:
            d = p - c
            if abs(d) > dead:
                want = p - dead if d > 0 else p + dead
                c = c + max(-step, min(step, want - c))
                c = min(max(c, lo), hi)
        pts.append([round(t, 3), round(c, 1)])
    keys = []
    for i, k in enumerate(pts):
        prev_same = i > 0 and pts[i - 1][1] == k[1]
        next_same = i + 1 < len(pts) and pts[i + 1][1] == k[1]
        if not (prev_same and next_same):
            keys.append(k)
    return keys or [[samples[0][0] if samples else 0.0, c]]


def crop_plan(vid: str, facts: dict, src: Path, pieces: list[dict], people: People, log=None, track: bool = TRACK) -> list[dict]:
    """One entry per shot of each played piece: {piece, start, end, x, why, decided, faces}."""
    log = log or (lambda *a: None)
    px, py, pw, ph = picture_box(vid, src)
    crop_w = int(round(ph * 9 / 16)) // 2 * 2
    lo, hi = px + crop_w / 2, px + pw - crop_w / 2          # slice centre limits: inside the picture, not the bars
    W = pw
    cuts = shot_cuts(vid)
    ip = facts["identity_pass"]
    names = {s["key"]: s.get("name") or s["key"] for s in facts["episode_pass"]["speakers"]}
    lines = [(r["id"], float(r["start"]), float(r.get("end", r["start"]))) for r in facts["lines"] if r["kind"] == "speech"]
    plan, prev = [], None
    for k, (a, b) in enumerate(padded(pieces)):
        for s0, s1 in shots_in(a, b, cuts):
            if s1 - s0 < SHORT_SHOT_S and prev is not None:
                plan.append({"piece": k, "start": s0, "end": s1, "x": prev, "keys": [[s0, prev]], "why": "very short shot: keeps the slice before",
                             "decided": True, "follow": [], "faces": []})
                continue
            over = [(lid, min(e, s1) - max(st, s0)) for lid, st, e in lines if st < s1 and e > s0]
            talk_t, seen_t = {}, {}
            for lid, d in over:
                ans = ip["lines"].get(lid) or {}
                for p in ans.get("talk") or []:
                    talk_t[p] = talk_t.get(p, 0.0) + d
                for p in ans.get("see") or []:
                    seen_t[p] = seen_t.get(p, 0.0) + d
            talkers = [p for p, _ in sorted(talk_t.items(), key=lambda x: -x[1])]
            seen = [p for p, _ in sorted(seen_t.items(), key=lambda x: -x[1])]
            frames = []
            looked = people.faces.along(src, s0, s1) if track else [(s0 + q * (s1 - s0), people.faces.at(src, s0 + q * (s1 - s0))) for q in SAMPLES]
            for t, fs in looked:
                row = []
                for f in fs:
                    who, best, margin = people.who(f["sig"])
                    row.append({**{a_: f[a_] for a_ in ("x", "y", "w", "h", "score")}, "person": who, "sim": round(best, 3), "margin": round(margin, 3)})
                frames.append((t, row))
            found = [f for _, fs in frames for f in fs]
            follow, x, why, decided = decide(found, talkers, seen, names, crop_w, W, mid=px + pw / 2)
            x = min(max(x, lo), hi)
            if follow and track:
                # the followed person's face centre per sampled frame (the group's centre when following several)
                samples = []
                for t, fs in frames:
                    mine = [f for f in fs if f["person"] in follow]
                    samples.append((t, (sum(f["x"] + f["w"] / 2 for f in mine) / len(mine)) if mine else None))
                first = next((p for _, p in samples if p is not None), x)
                keys = [[t_, x_ + px] for t_, x_ in path([(t_, None if c_ is None else c_ - px) for t_, c_ in samples],
                                                              first - px, crop_w, W)]
                x = keys[0][1]
                moved = max(v for _, v in keys) - min(v for _, v in keys)
                if moved > 2:
                    why += f"; follows them {moved:.0f} px across the shot"
            else:
                keys = [[round(s0, 3), round(x, 1)]]
            plan.append({"piece": k, "start": round(s0, 3), "end": round(s1, 3), "x": round(x, 1), "keys": keys, "why": why, "decided": decided,
                         "follow": [names.get(p, p) for p in follow], "talkers": [names.get(p, p) for p in talkers], "seen": [names.get(p, p) for p in seen],
                         "faces": [{**f, "person": names.get(f["person"], f["person"]) if f["person"] else None} for f in found]})
            prev = keys[-1][1]
            log(f"  shot {s0:.1f}-{s1:.1f}: {why}")
    return plan


# ----------------------------------------------------------------------------------------------- rendering
def _encode(args: list[str], graph: str, n: int, dst: Path, crf: int = 21) -> None:
    graph += ";" + "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", str(dst)], check=True)


def render_fit(src: Path, pieces: list[dict], dst: Path, box: tuple[int, int, int, int] | None = None) -> Path:
    """The whole picture in the middle, a blurred copy above and below. ``box`` = the picture area (bars trimmed)."""
    W, H = probe(src)
    px, py, pw, ph = box or (0, 0, W, H)
    trim = f"crop={pw}:{ph}:{px}:{py}," if (pw, ph) != (W, H) else ""
    args, parts = [], []
    for i, (a, b) in enumerate(padded(pieces)):
        args += ["-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src)]
        parts.append(f"[{i}:v:0]{trim}split[s{i}][t{i}];"
                     f"[s{i}]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},boxblur=24:2[b{i}];"
                     f"[t{i}]scale={OUT_W}:-2[f{i}];[b{i}][f{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v{i}];"
                     f"[{i}:a:0]aresample=48000[a{i}]")
    _encode(args, ";".join(parts), len(parts), dst)
    return dst


def _x_expr(shots: list[dict], a: float, crop_w: int, scale: float = 1.0) -> str:
    """The slice's left edge as an ffmpeg time expression (t = seconds from the piece's start): a linear glide
    between each shot's keyframes, held after the last one, switching at the shot changes."""
    def seg(keys):
        last = f"{(keys[-1][1] - crop_w / 2) * scale:.1f}"
        e = last
        for (t0, x0), (t1, x1) in reversed(list(zip(keys[:-1], keys[1:]))):
            if t1 <= t0:
                continue
            r0, r1 = t0 - a, t1 - a
            lx0, lx1 = (x0 - crop_w / 2) * scale, (x1 - crop_w / 2) * scale
            e = f"if(lt(t\\,{r1:.3f})\\,{lx0:.1f}+({lx1 - lx0:.1f})*(t-{r0:.3f})/{r1 - r0:.3f}\\,{e})"
        return e
    expr = seg(shots[-1].get("keys") or [[shots[-1]["start"], shots[-1]["x"]]])
    for s_ in reversed(shots[:-1]):
        expr = f"if(lt(t\\,{s_['end'] - a:.3f})\\,{seg(s_.get('keys') or [[s_['start'], s_['x']]])}\\,{expr})"
    return expr


def render_crop(src: Path, pieces: list[dict], plan: list[dict], dst: Path,
                box: tuple[int, int, int, int] | None = None) -> Path:
    """Each piece in one go (so the sound never breaks); the slice switches position at the shot changes.
    ``box`` = the picture area: the slice is cut from it, so baked-in black bars never reach the 9:16 frame."""
    W, H = probe(src)
    px, py, pw, H = box or (0, 0, W, H)
    crop_w = int(round(H * 9 / 16)) // 2 * 2
    args, parts = [], []
    for i, (a, b) in enumerate(padded(pieces)):
        args += ["-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src)]
        shots = [s for s in plan if s["piece"] == i] or [{"start": a, "end": b, "x": W / 2, "keys": [[a, W / 2]]}]
        parts.append(f"[{i}:v:0]crop={crop_w}:{H}:x='{_x_expr(shots, a, crop_w)}':y={py},scale={OUT_W}:{OUT_H},setsar=1,fps=30[v{i}];"
                     f"[{i}:a:0]aresample=48000[a{i}]")
    _encode(args, ";".join(parts), len(parts), dst)
    return dst


def render_debug(src: Path, pieces: list[dict], plan: list[dict], dst: Path,
                 box: tuple[int, int, int, int] | None = None) -> Path:
    """The 16:9 clip with the chosen slice drawn (green when decided, red when undecided), to check the choices."""
    W, H0 = probe(src)
    px, py, pw, H = box or (0, 0, W, H0)
    crop_w = int(round(H * 9 / 16)) // 2 * 2
    sc = 960 / W
    args, parts = [], []
    for i, (a, b) in enumerate(padded(pieces)):
        args += ["-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src)]
        shots = [s for s in plan if s["piece"] == i] or [{"start": a, "end": b, "x": W / 2, "keys": [[a, W / 2]]}]
        boxes = "".join(f",drawbox=x='{_x_expr([s_], a, crop_w, sc)}':y={py * sc:.0f}:w={crop_w * sc:.0f}:h={H * sc:.0f}"
                        f":color={'lime' if s_.get('decided', True) else 'red'}@0.9:t=6:enable='between(t\\,{s_['start'] - a:.3f}\\,{s_['end'] - a:.3f})'"
                        for s_ in shots)
        parts.append(f"[{i}:v:0]scale=960:-2,setsar=1{boxes},fps=30[v{i}];[{i}:a:0]aresample=48000[a{i}]")
    _encode(args, ";".join(parts), len(parts), dst, crf=26)
    return dst


def render_both(vid: str, facts: dict, src: Path, clip: dict, out_dir: Path, people: People, log=None, track: bool = TRACK,
                debug: bool = True) -> dict:
    """``<out_dir>/<clip id>_fit.mp4``, ``_crop.mp4``, ``_crop_plan.json`` and (``debug``, our check page only)
    ``_crop_debug.mp4``."""
    out_dir = Path(out_dir)
    box = picture_box(vid, src)
    fit = render_fit(src, clip["pieces"], out_dir / f"{clip['id']}_fit.mp4", box)
    plan = crop_plan(vid, facts, src, clip["pieces"], people, log, track=track)
    crop = render_crop(src, clip["pieces"], plan, out_dir / f"{clip['id']}_crop.mp4", box)
    dbg = render_debug(src, clip["pieces"], plan, out_dir / f"{clip['id']}_crop_debug.mp4", box) if debug else None
    (out_dir / f"{clip['id']}_crop_plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"fit": str(fit), "crop": str(crop), "debug": str(dbg) if dbg else None, "plan": plan}


def source_for(vid: str) -> Path:
    """The episode video for rendering (1080p when downloaded; see media.source_video)."""
    from .media import source_video
    return source_video(vid)


def people_for(vid: str, facts: dict, src: Path, run_dir: Path, faces: Faces | None = None, log=None) -> People:
    """The episode's reference faces: built once and saved in <run_dir>/face_refs.json."""
    faces = faces or Faces()
    p = Path(run_dir) / "face_refs.json"
    if p.exists():
        return People.load(faces, p)
    people = People.build(faces, facts, src, log).clean(log)
    people.save(p)
    return people
