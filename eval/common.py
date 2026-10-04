"""Shared helpers for eval: loading clip files (proposals / gold / anchors), transcripts, kept words, text.

All three clip kinds share the clip core of docs/spec/output_schema.md, so every metric here works on
`pieces` (inclusive word ranges in playback order) and nothing else.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# spec numbers (docs/spec/clip_spec.md). product-lead owns them; keep in sync.
HARD_MIN_S = 15.0
SOFT_MAX_S = 120.0
HARD_MAX_S = 150.0
from .schema_check import SKIP_S  # noqa: E402  single source for the skip threshold (spec 0.2 S4)
LONG_RANGE_S = 300.0     # S5
BOUNDARY_GAP_S = 0.5
T_SHOW, T_REC = 3.5, 4.0
CENSOR_TAG = "[సెన్సార్డ్]"
SONG_TAG = "[పాట]"
LAUGH_TAG = "[నవ్వు]"
END_PUNCT = re.compile(r"[.?!।]['\"”’)\]]*$")


def utf8_stdout():
    """Telugu output on Windows consoles (cp1252 by default)."""
    import sys
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def resolve(p):
    p = Path(p)
    return p if p.is_absolute() else ROOT / p


class Episode:
    """Words and audio events of one episode transcript."""

    def __init__(self, path):
        d = load_json(path)
        self.path = str(path)
        self.W = d["word_timestamps"]
        self.n = len(self.W)
        self.duration = float(d.get("audio_duration_secs") or (self.W[-1]["end"] if self.W else 0.0))
        self.events = d.get("audio_events") or []
        self.censored = [(e["start"], e["end"]) for e in self.events if e["text"] == CENSOR_TAG]
        self.songs = [(e["start"], e["end"]) for e in self.events if e["text"] == SONG_TAG]
        self.laughs = [(e["start"], e["end"]) for e in self.events if e["text"] == LAUGH_TAG]

    def natural_after(self, i):
        """clip_spec §0: natural boundary between word i and i+1 (end punct, speaker change, gap >= 0.5s)."""
        if i < 0 or i >= self.n - 1:
            return True
        a, b = self.W[i], self.W[i + 1]
        return (bool(END_PUNCT.search(a["word"])) or a.get("speaker") != b.get("speaker")
                or b["start"] - a["end"] >= BOUNDARY_GAP_S)

    def t(self, i):
        return self.W[i]["start"]


@lru_cache(maxsize=8)
def episode(path):
    return Episode(resolve(path))


def episode_for(doc):
    return episode(str(resolve(doc["transcript"]["path"])))


# ---- clip files ---------------------------------------------------------------------------------------

def clips_of(doc):
    """Ranked clips of a file. Proposals: by `rank`. Gold: file order (the strategist ranks by order).
    Anchors: file order (no rank meaning)."""
    if doc.get("kind") == "proposals":
        return sorted(doc.get("proposals") or [], key=lambda c: c["rank"])
    return list(doc.get("clips") or [])


def kept_words(clip):
    """Kept episode word indices in playback order (a replayed cold open appears twice)."""
    return [j for p in clip["pieces"] for j in range(p["start_word"], p["end_word"] + 1)]


def kept_set(clip):
    return set(kept_words(clip))


def region(clip):
    lo = min(p["start_word"] for p in clip["pieces"])
    hi = max(p["end_word"] for p in clip["pieces"])
    return lo, hi


def edited_duration(clip, ep):
    return sum(ep.W[p["end_word"]]["end"] - ep.W[p["start_word"]]["start"] for p in clip["pieces"])


def region_span_s(clip, ep):
    lo, hi = region(clip)
    return ep.W[hi]["end"] - ep.W[lo]["start"]


def jumps(clip, ep):
    """List of (after_piece_index, kind) with kind in {'backward', 'skip'} (clip_spec §0)."""
    out = []
    P = clip["pieces"]
    for k, (a, b) in enumerate(zip(P, P[1:])):
        if b["start_word"] <= a["end_word"]:
            out.append((k, "backward"))
        elif ep.W[b["start_word"]]["start"] - ep.W[a["end_word"]]["end"] > SKIP_S:
            out.append((k, "skip"))
    return out


def fmt_t(t):
    t = int(round(t))
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def excerpt(words, n=14):
    words = list(words)
    if len(words) <= 2 * n:
        return " ".join(words)
    return " ".join(words[:n]) + " … " + " ".join(words[-n:])


def clip_excerpt(clip, ep, n=12):
    return excerpt((ep.W[j]["word"] for j in kept_words(clip)), n)


def render_for_judge(clip, ep):
    """Kept text as a viewer hears it: speaker turns on their own lines, cuts marked.
    '/' = a tightening cut (words removed, same moment); '//' = a jump (skip > 20s or backward).
    Speaker labels are anonymous letters in order of appearance (no host/guest guess)."""
    labels = {}
    J = {k for k, _ in jumps(clip, ep)}
    lines, cur_spk, cur = [], None, []
    t0 = 0.0
    for k, p in enumerate(clip["pieces"]):
        for j in range(p["start_word"], p["end_word"] + 1):
            w = ep.W[j]
            spk = labels.setdefault(w.get("speaker"), "ABCDEFGH"[min(len(labels), 7)])
            if spk != cur_spk:
                if cur:
                    lines.append((cur_spk, cur))
                cur_spk, cur = spk, []
            cur.append(w["word"])
        if k < len(clip["pieces"]) - 1:
            cur.append("//" if k in J else "/")
    if cur:
        lines.append((cur_spk, cur))
    out = []
    for spk, ws in lines:
        # a turn that starts with a cut marker reads better with the marker at the end of the previous turn
        out.append(f"{spk}: " + " ".join(ws))
    return "\n".join(out)


# Telugu/English negation tokens (general grammar, not episode-specific). Telugu negation often sits in the
# sentence-final verb (spec §2), so a cut right before it can flip a claim.
NEGATION_WORDS = {"కాదు", "లేదు", "కాదండి", "లేదండి", "కాదండీ", "లేదండీ", "లేరు", "లేవు", "రాదు", "వద్దు",
                  "కూడదు", "కాకూడదు", "చేయకూడదు", "చేయొద్దు", "వద్దండి", "not", "no", "never", "don't",
                  "doesn't", "didn't", "won't", "can't", "cannot", "nothing", "nobody", "neither", "nor"}


def is_negation(word):
    w = re.sub(r"[\s.,?!।:;\"'“”‘’()\[\]…-]+", "", word.lower())
    return w in NEGATION_WORDS or w.endswith(("కూడదు", "లేదు", "కాదు", "వద్దు"))


def render_cut_context(clip, ep, n=5, mark_negation=False):
    """spec 0.2 T2: for every cut edge, the n source words beyond it (words the viewer does NOT hear).
    Removed stretches of <= 2n words are shown in full. mark_negation (judge-v2+) appends a warning when the
    words shown just beyond a cut edge include a negation word."""
    W, P = ep.W, clip["pieces"]
    ws = lambda a, b: " ".join(W[j]["word"] for j in range(max(0, a), min(ep.n, b + 1)))  # noqa: E731

    def neg(a, b):
        if not mark_negation:
            return ""
        hits = [W[j]["word"] for j in range(max(0, a), min(ep.n, b + 1)) if is_negation(W[j]["word"])]
        return f"  <-- NOT HEARD, contains negation: {' '.join(hits)}" if hits else ""

    lines = []
    s = P[0]["start_word"]
    lines.append(f"before start: [{ws(s - n, s - 1)}] >> {ws(s, s + 3)}")
    J = dict(jumps(clip, ep))
    for k, (a, b) in enumerate(zip(P, P[1:]), 1):
        ae, bs = a["end_word"], b["start_word"]
        head = f"cut {k}: {ws(ae - 3, ae)} <<"
        tailtxt = f">> {ws(bs, bs + 3)}"
        if J.get(k - 1) == "backward":
            mid = f"[then: {ws(ae + 1, ae + n)}] ... JUMPS BACK ... [before: {ws(bs - n, bs - 1)}]"
            warn = neg(ae + 1, ae + n)
        elif bs - ae - 1 <= 2 * n:
            mid = f"[removed: {ws(ae + 1, bs - 1)}]"
            warn = neg(ae + 1, bs - 1)
        else:
            gap = W[bs]["start"] - W[ae]["end"]
            mid = f"[removed {bs - ae - 1} words / {gap:.0f}s: {ws(ae + 1, ae + n)} ... {ws(bs - n, bs - 1)}]"
            warn = neg(ae + 1, ae + n)
        lines.append(f"{head} {mid} {tailtxt}{warn}")
    e = P[-1]["end_word"]
    lines.append(f"after end: {ws(e - 3, e)} << [{ws(e + 1, e + n)}]{neg(e + 1, e + 3)}")
    return chr(10).join(lines)


def first_seconds_text(clip, ep, secs=3.0):
    """Words spoken in the first `secs` seconds of the edited clip."""
    out, t = [], 0.0
    for p in clip["pieces"]:
        base = ep.W[p["start_word"]]["start"]
        for j in range(p["start_word"], p["end_word"] + 1):
            if t + (ep.W[j]["start"] - base) > secs:
                return " ".join(out)
            out.append(ep.W[j]["word"])
        t += ep.W[p["end_word"]]["end"] - base
    return " ".join(out)


def find_run_files(run_dir):
    """Proposal files of a run: every *.json under the run dir whose kind is 'proposals'."""
    out = []
    for p in sorted(Path(run_dir).rglob("*.json")):
        if p.name in ("cost.json",) or "debug" in p.parts or p.stat().st_size > 50_000_000:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                head = f.read(4000)
            if '"proposals"' not in head and '"kind"' not in head:
                continue
            d = load_json(p)
        except (ValueError, OSError):
            continue
        if isinstance(d, dict) and d.get("kind") == "proposals":
            out.append((p, d))
    return out


def reference_files(video_id):
    """(anchors_doc or None, gold_doc or None) for a video."""
    a = ROOT / "data" / "anchors" / f"{video_id}.json"
    g = ROOT / "data" / "gold" / f"{video_id}.json"
    return (load_json(a) if a.exists() else None), (load_json(g) if g.exists() else None)
