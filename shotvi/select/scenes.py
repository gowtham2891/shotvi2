"""Scene splitter for chunked web-series discovery (no LLM, no network): free signals only.

    from shotvi.select.scenes import load_zones, excluded_of, split_scenes, scene_windows
    zones = load_zones(video_id, scribe_dict, cuts)               # shotvi.segment.episode_zones + YouTube meta
    ex = excluded_of(zones)                                       # opening zone + sponsor segments
    scenes = split_scenes(ep, ex)                                 # [{no, first_line, last_line, p0, p1, ...}]
    wins = scene_windows(ep, scenes, ex)                          # + read-only context lines before / after

What is excluded from discovery (never in a scene, never shown as context):
  * everything before ``opening.story_start`` (lead, title cards, recap, teaser; when the detector finds no story
    start, only the ``opening.hard`` ranges), plus any ``opening.hard`` range;
  * every sponsor segment (``sponsors``), wherever it sits.
The spoken lines left form STRETCHES: maximal runs of consecutive spoken lines with no excluded line between them.
A scene never crosses a stretch edge (a clip must not straddle a sponsor read).

Inside a stretch, a BOUNDARY candidate is the silence between two consecutive spoken lines:
  strength = silence (s) + MUSIC_BONUS_S if a music / song audio event lies in it.
  It may break a scene only when the picture changes inside it (a shot cut in the gap): a silence held on one shot
  is an acted pause, not a scene change. With no shot data, every gap may break.
Steps:
  1. break at every boundary with strength >= BREAK_S;
  2. merge: while a scene is shorter than MIN_S, merge the shortest one into the neighbour across its WEAKER
     boundary (preferring a neighbour that keeps the merged scene <= MAX_S); a stretch's only scene stays as it is;
  3. split: a scene longer than MAX_S splits at its strongest internal boundary (shot-cut gaps first) such that
     both parts last >= MIN_S, again until none is longer or no such boundary exists.
Durations are speech spans: first word of the first line to the last word of the last line.

Thresholds (format-level, chosen from principle, not fitted to clips; measured 2026-10-06 on 3 web-series episodes,
Couple Goals Ep1/Ep2 and Hello Madam Ep6):
  BREAK_S 6.0       in-scene silences between lines: median 0.8-1.0 s, p90 3.5-4.0 s, p95 6.2-7.3 s; title cards,
                    montages and transitions leave >= 7 s (episode_zones). ~1.5x the in-scene p90.
  MUSIC_BONUS_S 2.0 a music sting or song bridge in a 4-6 s silence marks a transition; music alone cannot, because
                    one episode tags background score in 34 of its 69 silences >= 2 s (in-scene underscore).
  MIN_S 60          a scene must hold at least one typical clip (20-75 s) with its setup; each call also repeats the
                    ~2k-token system prompt, so tiny calls are waste.
  MAX_S 240         the 1-4 min target: long enough for a 2-minute clip, short enough that the model reads it all.
  Sensitivity (scene counts Ep1 / Ep2 / Hello Madam; default 7 / 8 / 12): MAX_S 240-300 changes nothing (180: 8 / 9
  / 12); BREAK_S 5-8 leaves Ep1 unchanged, BREAK_S 5 splits two Ep2 scenes (10), BREAK_S 7 / 8 merge one / three
  Hello Madam pairs (11 / 9); MUSIC_BONUS_S 0 merges one Hello Madam pair (11) and moves one Ep2 edge, 4 splits Ep1
  to 9 and Ep2 to 9; MIN_S 45 gives 8 / 8 / 15, MIN_S 90 gives 6 / 7 / 9.
Context, each side, same stretch only (never an excluded line), audio events among the lines included: the
CONTEXT_MIN_LINES nearest spoken lines always (what leads into / out of the scene, even across a long transition),
then up to CONTEXT_LINES in total while they lie within CONTEXT_S of the scene edge. A clip setup or a payoff
reaction that crosses a scene edge is ~5-30 s of short lines (median 3 words, ~2 s each), so 8 lines / 30 s cover
it without re-reading the neighbouring scene.
"""
from __future__ import annotations

import json
from pathlib import Path

BREAK_S = 6.0
MUSIC_BONUS_S = 2.0
MIN_S = 60.0
MAX_S = 240.0
CONTEXT_MIN_LINES = 3
CONTEXT_LINES = 8
CONTEXT_S = 30.0
STORY_EPS = 0.05
PARAMS = {"break_s": BREAK_S, "music_bonus_s": MUSIC_BONUS_S, "min_s": MIN_S, "max_s": MAX_S,
          "context_min_lines": CONTEXT_MIN_LINES, "context_lines": CONTEXT_LINES, "context_s": CONTEXT_S}

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------------------------- zones (free signals)
def _series_key(meta: dict | None):
    """(channel, series name) from YouTube metadata: the title's first '|'-separated part."""
    if not meta or not meta.get("title"):
        return None
    name = " ".join(meta["title"].split("|")[0].lower().split())
    return (meta.get("channel") or "", name) if name else None


def load_zones(video_id: str, transcript: dict, cuts, root: Path = ROOT) -> dict:
    """``episode_zones`` on the episode with its YouTube metadata and the transcripts of sibling episodes of the same
    series found in scribe_dataset/ (recap detection), the same inputs the web-series eval uses."""
    from shotvi.segment.episode_zones import episode_zones
    base = Path(root) / "scribe_dataset"
    mp = base / video_id / "youtube_meta.json"
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
    key, refs = _series_key(meta), []
    if key is not None:
        for p in sorted(base.glob("*/youtube_meta.json")):
            vid = p.parent.name
            tp = p.parent / f"{vid}_scribe_transcript.json"
            if vid == video_id or not tp.exists():
                continue
            if _series_key(json.loads(p.read_text(encoding="utf-8"))) == key:
                refs.append(json.loads(tp.read_text(encoding="utf-8")))
    return episode_zones(transcript, list(cuts or []) or None, meta, refs, video_id)


def excluded_of(zones: dict) -> dict:
    """Excluded ranges for discovery: {"before": story start (s) or None, "ranges": [(start, end)], "why": [...]}."""
    op = zones.get("opening") or {}
    ranges, why = [], []
    for a, b in op.get("hard") or []:
        ranges.append((float(a), float(b)))
        why.append(f"opening (hard) {a:.1f}-{b:.1f} s")
    for s in zones.get("sponsors") or []:
        ranges.append((float(s["start"]), float(s["end"])))
        why.append(f"sponsor {s['start']:.1f}-{s['end']:.1f} s")
    ss = op.get("story_start")
    if ss is not None:
        why.append(f"before the story start {float(ss):.1f} s")
    return {"before": None if ss is None else float(ss), "ranges": sorted(ranges), "why": why}


def _is_excluded_line(ep, r, ex: dict) -> bool:
    a, b = ep.w0(r), ep.w1(r)
    if ex.get("before") is not None and a < ex["before"] - STORY_EPS:
        return True
    return any(a < y and b > x for x, y in ex.get("ranges") or [])


def _is_excluded_time(t0: float, t1: float, ex: dict) -> bool:
    if ex.get("before") is not None and t0 < ex["before"] - STORY_EPS:
        return True
    return any(t0 < y and t1 > x for x, y in ex.get("ranges") or [])


# ---------------------------------------------------------------------------------------------- splitter
def stretches(ep, ex: dict) -> list[list[dict]]:
    """Maximal runs of consecutive spoken lines (ep.speech order) none of which is excluded."""
    out, cur = [], []
    for r in ep.speech:
        if _is_excluded_line(ep, r, ex):
            if cur:
                out.append(cur)
            cur = []
            continue
        cur.append(r)
    if cur:
        out.append(cur)
    return out


def boundary(ep, a, b, p: dict) -> dict:
    """The silence between spoken lines a and b: {gap_s, music, cut, strength}."""
    g0, g1 = ep.w1(a), ep.w0(b)
    from shotvi.select.webseries import _is_music
    mus = [ev["text"] for ev in ep.events if _is_music(ev["text"]) and ev["start"] < g1 and ev["end"] > g0]
    cut = bool(ep.cuts_in(g0, g1)) if ep.cuts else True
    gap = max(0.0, g1 - g0)
    return {"gap_s": round(gap, 2), "music": mus[:2], "cut": cut,
            "strength": round(gap + (p["music_bonus_s"] if mus else 0.0), 2)}


def split_scenes(ep, ex: dict | None = None, params: dict | None = None) -> list[dict]:
    """Scenes in time order (see the module doc). Each: {no, first_line, last_line, p0, p1 (positions in ep.lines,
    events between included), start, end, duration_s, n_spoken, stretch, opened_by (boundary or reason),
    note (a scene left outside MIN_S..MAX_S, and why)}."""
    p = {**PARAMS, **(params or {})}
    ex = ex or {"before": None, "ranges": []}
    out = []
    for si, s in enumerate(stretches(ep, ex)):
        bnd = {k: boundary(ep, s[k - 1], s[k], p) for k in range(1, len(s))}

        def dur(a, b):
            return ep.w1(s[b - 1]) - ep.w0(s[a])

        edges = [0] + [k for k in range(1, len(s)) if bnd[k]["cut"] and bnd[k]["strength"] >= p["break_s"]] \
            + [len(s)]
        sc = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
        # 2. merge tiny scenes across their weaker boundary
        while len(sc) > 1:
            small = [i for i, (a, b) in enumerate(sc) if dur(a, b) < p["min_s"]]
            if not small:
                break
            i = min(small, key=lambda i: (dur(*sc[i]), i))
            opts = []
            if i > 0:
                opts.append((bnd[sc[i][0]]["strength"], i - 1))
            if i < len(sc) - 1:
                opts.append((bnd[sc[i + 1][0]]["strength"], i + 1))
            fit = [o for o in opts if dur(min(sc[i][0], sc[o[1]][0]), max(sc[i][1], sc[o[1]][1])) <= p["max_s"]]
            j = min(fit or opts)[1]
            lo, hi = min(i, j), max(i, j)
            sc[lo:hi + 1] = [(sc[lo][0], sc[hi][1])]
        # 3. split long scenes at their strongest internal boundary (both parts >= min_s)
        done, todo = [], list(sc)
        while todo:
            a, b = todo.pop(0)
            if dur(a, b) <= p["max_s"]:
                done.append((a, b))
                continue
            ks = [k for k in range(a + 1, b) if dur(a, k) >= p["min_s"] and dur(k, b) >= p["min_s"]]
            if not ks:
                done.append((a, b))
                continue
            k = max(ks, key=lambda k: (bnd[k]["cut"], bnd[k]["strength"], -k))
            todo[:0] = [(a, k), (k, b)]
        for a, b in sorted(done):
            first, last = s[a], s[b - 1]
            d = dur(a, b)
            note = None
            if d < p["min_s"]:
                note = f"shorter than {p['min_s']:.0f} s: the whole stretch between excluded zones"
            elif d > p["max_s"]:
                note = f"longer than {p['max_s']:.0f} s: no internal boundary leaves both parts >= {p['min_s']:.0f} s"
            out.append({"first_line": first["id"], "last_line": last["id"],
                        "p0": ep.pos[first["id"]], "p1": ep.pos[last["id"]],
                        "start": round(ep.w0(first), 3), "end": round(ep.w1(last), 3), "duration_s": round(d, 1),
                        "n_spoken": b - a, "stretch": si,
                        "opened_by": bnd[a] if a else ("story start" if si == 0 else "after an excluded zone"),
                        **({"note": note} if note else {})})
    for k, x in enumerate(out):
        x["no"] = k + 1
    return out


def scene_windows(ep, scenes: list[dict], ex: dict | None = None, params: dict | None = None) -> list[dict]:
    """Per scene: the read-only context positions before / after it (ep.lines positions, events included):
    the context_min_lines nearest spoken lines, then up to context_lines while within context_s seconds of the scene
    edge; same stretch only (never an excluded line).
    After the last spoken line of a stretch, audio events within context_s that are not excluded are added
    (a reaction laugh or a sting after the payoff). {"scene": scene, "before": [p...], "after": [p...]}."""
    p = {**PARAMS, **(params or {})}
    ex = ex or {"before": None, "ranges": []}
    st = stretches(ep, ex)
    sid = {r["id"]: k for k, s in enumerate(st) for r in s}
    out = []
    for sc in scenes:
        s = st[sid[sc["first_line"]]]
        ids = [r["id"] for r in s]
        i0, i1 = ids.index(sc["first_line"]), ids.index(sc["last_line"])
        t0, t1 = sc["start"], sc["end"]
        def take(cand, ok):      # cand nearest first: the nearest few always, then while ok
            out = []
            for k, r in enumerate(cand):
                if k >= p["context_min_lines"] and not ok(r):
                    break
                out.append(r)
            return out
        pre = take(s[max(0, i0 - p["context_lines"]):i0][::-1], lambda r: ep.w0(r) >= t0 - p["context_s"])[::-1]
        post = take(s[i1 + 1:i1 + 1 + p["context_lines"]], lambda r: ep.w1(r) <= t1 + p["context_s"])
        before = list(range(ep.pos[pre[0]["id"]], sc["p0"])) if pre else []
        if post:
            after = list(range(sc["p1"] + 1, ep.pos[post[-1]["id"]] + 1))
        else:
            after = []
            q = sc["p1"] + 1
            while q < len(ep.lines) and not ep.is_speech(ep.lines[q]):
                r = ep.lines[q]
                if r["start"] > t1 + p["context_s"] or _is_excluded_time(r["start"], r["end"], ex):
                    break
                after.append(q)
                q += 1
        # never show an excluded line, even an event, as context
        before = [q for q in before if not _excl_pos(ep, q, ex)]
        after = [q for q in after if not _excl_pos(ep, q, ex)]
        out.append({"scene": sc, "before": before, "after": after})
    return out


def _excl_pos(ep, q: int, ex: dict) -> bool:
    r = ep.lines[q]
    if ep.is_speech(r):
        return _is_excluded_line(ep, r, ex)
    return _is_excluded_time(r["start"], r["end"], ex)


def window_ids(ep, win: dict) -> dict:
    """{"scene": [ids], "before": [ids], "after": [ids], "all": set(ids)}."""
    sc = win["scene"]
    scene = [ep.lines[q]["id"] for q in range(sc["p0"], sc["p1"] + 1)]
    before = [ep.lines[q]["id"] for q in win["before"]]
    after = [ep.lines[q]["id"] for q in win["after"]]
    return {"scene": scene, "before": before, "after": after, "all": set(scene) | set(before) | set(after)}


def check_moment(ep, m: dict, win: dict) -> str | None:
    """Why a moment nominated in a scene call is not taken (None = taken):
      S1  its start or end line is not among the lines this call showed;
      S2  it lies wholly in the context lines (that stretch is the neighbouring call's scene)."""
    from shotvi.select.webseries import norm_id
    ids = window_ids(ep, win)
    sid, eid = norm_id(m.get("start_line")), norm_id(m.get("end_line"))
    if sid not in ids["all"] or eid not in ids["all"]:
        return (f"S1: start/end line {m.get('start_line')}..{m.get('end_line')} is not among the lines scene "
                f"{win['scene']['no']}'s call showed")
    a, b = sorted((ep.pos[sid], ep.pos[eid]))
    sc = win["scene"]
    if not any(ep.is_speech(ep.lines[q]) for q in range(max(a, sc["p0"]), min(b, sc["p1"]) + 1)):
        return (f"S2: {sid}..{eid} lies wholly in the context lines of scene {sc['no']}'s call "
                f"(scene {sc['first_line']}..{sc['last_line']})")
    return None


def scene_table(ep, scenes: list[dict]) -> str:
    from shotvi.select.webseries import fmt
    rows = [f"{len(scenes)} scenes, durations {[round(s['duration_s']) for s in scenes]} s"]
    for s in scenes:
        ob = s["opened_by"]
        why = ob if isinstance(ob, str) else (f"gap {ob['gap_s']} s" + (" + music" if ob["music"] else ""))
        rows.append(f"  scene {s['no']:2d} {fmt(s['start'])}-{fmt(s['end'])} {s['duration_s']:6.1f} s "
                    f"{s['first_line']}-{s['last_line']} ({s['n_spoken']} spoken lines; opened by {why})"
                    + (f" [{s['note']}]" if s.get("note") else ""))
    return "\n".join(rows)
