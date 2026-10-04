"""LLM-assisted opening detection (teaser / song / intro / content start).

One call per episode reads the opening window, min(12 min, 20% of the
episode), rendered as numbered lines with raw speaker ids and inline audio
events. Where the repeat matcher (``intro.find_fragments``) found a line again
later in the body, the line carries ``>> recurs later at mm:ss`` as evidence.

The model returns word ranges for teaser / song / intro and the first word of
the real conversation. Code validates the answer (integers, inside the
window, ordered, non-overlapping, everything before ``content_start_word``)
and turns it into ``IntroRange``s; the repeat fragments inside the LLM
teaser are kept so ``teaser_body_copy()`` still works.

Merge policy with the rules detector:
- The LLM decides boundaries. Speech before ``content_start_word`` that is
  not teaser / song becomes ``intro``.
- If the LLM says "no teaser" but the rules detector found a montage of >= 3
  excerpts that recur in >= 2 body places, the rules teaser is kept
  (verbatim repeats are near-certain) and the disagreement is recorded.
- Any failure (no key, network, spend cap, invalid answer) falls back to the
  rules detector; the reason is in ``Segmentation.opening["fallback_reason"]``.

Spend: every call goes through ``shotvi.llm`` (GeminiProvider -> project
rupee ledger and cap; responses cached under ``runs/cache``, so reruns are free).
Short transcripts (< ``MIN_EPISODE_S``) never call the LLM.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

from .intro import MIN_FRAG_INFO, WEAK_FRAG_INFO, IntroRange, TeaserFragment, _end_seconds, find_fragments
from .loader import Transcript
from .pauses import PauseStats
from .render import EVENT_TAGS, fmt_ts

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "prompts" / "opening.md"
WINDOW_MAX_S = 720.0
WINDOW_FRAC = 0.20
MIN_EPISODE_S = 600.0        # shorter transcripts (Shorts, clips) use the rules detector only
LINE_MAX_WORDS = 25
# One small call per episode, so a cheap model is enough. Override with SHOTVI_OPENING_MODEL.
DEFAULT_MODEL = {"provider": "gemini", "model": "gemini-3.5-flash-lite",
                 "params": {"thinking_level": "low", "max_output_tokens": 4096}}


def default_model() -> dict:
    m = os.environ.get("SHOTVI_OPENING_MODEL", "").strip()
    return {**DEFAULT_MODEL, "model": m} if m else DEFAULT_MODEL
RULES_KEEP_MIN_FRAGMENTS = 3
RULES_KEEP_MIN_BODY_LOCS = 2

SCHEMA = {
    "type": "object",
    "properties": {
        "teaser_start_word": {"type": "integer"}, "teaser_end_word": {"type": "integer"},
        "song_start_word": {"type": "integer"}, "song_end_word": {"type": "integer"},
        "intro_start_word": {"type": "integer"}, "intro_end_word": {"type": "integer"},
        "content_start_word": {"type": "integer"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["teaser_start_word", "teaser_end_word", "song_start_word", "song_end_word",
                 "intro_start_word", "intro_end_word", "content_start_word", "confidence", "reason"],
}


class OpeningLLMError(RuntimeError):
    """The LLM answer could not be obtained or did not validate."""


# ------------------------------------------------------------------ prompt
@dataclass(frozen=True)
class OpeningPrompt:
    version: str
    system: str
    user: str
    sha8: str

    @property
    def tag(self) -> str:
        return f"{self.version}+{self.sha8}"


def load_prompt(path: Path = PROMPT_PATH) -> OpeningPrompt:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    if not m:
        raise ValueError(f"{path}: missing front matter")
    meta = {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in m.group(1).strip().splitlines())}
    sm = re.search(r"### SYSTEM\n(.*?)\n### USER\n(.*)", m.group(2), re.S)
    if not sm:
        raise ValueError(f"{path}: needs '### SYSTEM' and '### USER' sections")
    return OpeningPrompt(meta["version"], sm.group(1).strip(), sm.group(2).strip(),
                         hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8])


def window_end_index(tr: Transcript) -> int:
    """Last word index of the opening window."""
    end_s = min(WINDOW_MAX_S, WINDOW_FRAC * tr.duration)
    last = 0
    for w in tr.words:
        if w.start > end_s:
            break
        last = w.i
    return last


def render_opening(tr: Transcript, ps: PauseStats, fragments: list[TeaserFragment],
                   last_word: int | None = None) -> tuple[str, list[tuple[int, int]]]:
    """Numbered lines of the opening window: a new line at a speaker change, a
    pause >= the video's major pause, an audio event, or LINE_MAX_WORDS."""
    ws = tr.words
    last = window_end_index(tr) if last_word is None else last_word
    evs = sorted(tr.events, key=lambda e: e.start)
    lines: list[tuple[int, int]] = []
    s = 0
    for i in range(1, last + 2):
        if i > last:
            lines.append((s, last))
            break
        brk = (ws[i].speaker != ws[i - 1].speaker or ws[i].start - ws[i - 1].end >= ps.major
               or i - s >= LINE_MAX_WORDS
               or any(ws[i - 1].end - 0.05 <= e.start <= ws[i].start and e.kind != "laughter" for e in evs))
        if brk:
            lines.append((s, i - 1))
            s = i
    out = []
    ek = 0
    for a, b in lines:
        parts = []
        for i in range(a, b + 1):
            while ek < len(evs) and evs[ek].start <= ws[i].start:
                tag = EVENT_TAGS.get(evs[ek].kind)
                if tag and (not parts or parts[-1] != tag):
                    parts.append(tag)
                ek += 1
            parts.append(ws[i].text)
        while ek < len(evs) and evs[ek].start <= ws[b].end + 0.3:
            tag = EVENT_TAGS.get(evs[ek].kind)
            if tag and (not parts or parts[-1] != tag):
                parts.append(tag)
            ek += 1
        line = f"[w{a}-{b} | {fmt_ts(ws[a].start)}] {ws[a].speaker}: " + " ".join(parts)
        rec = [f for f in fragments if f.info >= MIN_FRAG_INFO and f.i0 <= b and f.i1 >= a]
        if rec:
            line += " >> recurs later at " + ", ".join(sorted({fmt_ts(f.body_start) for f in rec}))
        out.append(line)
    return "\n".join(out), lines


# --------------------------------------------------------------- validate
def validate(ans: dict, first: int, last: int) -> dict:
    """Check and normalise an answer. Returns {"teaser": (s, e) | None, "song": ...,
    "intro": ..., "content_start_word": int, "confidence": float, "reason": str}."""
    def rng(k):
        s, e = ans.get(f"{k}_start_word"), ans.get(f"{k}_end_word")
        if not isinstance(s, int) or not isinstance(e, int) or isinstance(s, bool) or isinstance(e, bool):
            raise OpeningLLMError(f"{k}: indices must be integers, got {s!r}, {e!r}")
        if s < 0 and e < 0:
            return None
        if s < 0 or e < 0:
            raise OpeningLLMError(f"{k}: only one end is -1 ({s}, {e})")
        if not (first <= s <= e <= last):
            raise OpeningLLMError(f"{k}: range {s}-{e} outside the window {first}-{last} or reversed")
        return (s, e)

    out = {k: rng(k) for k in ("teaser", "song", "intro")}
    cs = ans.get("content_start_word")
    if not isinstance(cs, int) or isinstance(cs, bool) or not (first <= cs <= last + 1):
        raise OpeningLLMError(f"content_start_word {cs!r} outside {first}-{last + 1}")
    present = sorted((r for r in out.values() if r), key=lambda r: r[0])
    for a, b in zip(present, present[1:]):
        if b[0] <= a[1]:
            raise OpeningLLMError(f"ranges overlap: {a} and {b}")
    if present and cs <= present[-1][1]:
        raise OpeningLLMError(f"content_start_word {cs} is not after the last range {present[-1]}")
    conf = ans.get("confidence")
    try:
        conf = min(max(float(conf), 0.0), 1.0)
    except (TypeError, ValueError) as e:
        raise OpeningLLMError(f"confidence {conf!r} is not a number") from e
    out["content_start_word"] = cs
    out["confidence"] = round(conf, 2)
    out["reason"] = str(ans.get("reason") or "")[:400]
    return out


# ------------------------------------------------------------------- call
class OpeningLLM:
    """Callable: (tr, ps, fragments) -> validated answer dict (plus "meta").

    ``client`` / ``spec`` default to shotvi.llm's Gemini client with the
    response cache under runs/cache. Inject a fake ``generate_json`` in tests.
    """

    def __init__(self, client=None, spec=None, min_duration_s: float = MIN_EPISODE_S,
                 cache_dir: str | Path | None = None, prompt_path: Path = PROMPT_PATH):
        self._client = client
        self._spec = spec
        self.min_duration_s = min_duration_s
        self.cache_dir = cache_dir if cache_dir is not None else ROOT / "runs" / "cache"
        self.prompt_path = prompt_path

    def _ensure_client(self):
        if self._client is not None:
            return self._client, self._spec
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        from shotvi.llm import Ledger, LLMClient, ModelSpec
        if not os.environ.get("GEMINI_API_KEY"):
            raise OpeningLLMError("GEMINI_API_KEY is not set")
        self._client = LLMClient(Ledger(max_usd=0.10), cache_dir=self.cache_dir, max_retries=2, schema_retries=1)
        self._spec = self._spec or ModelSpec.from_dict(default_model())
        return self._client, self._spec

    def build(self, tr: Transcript, ps: PauseStats, fragments: list[TeaserFragment]) -> tuple[str, str, int, int]:
        p = load_prompt(self.prompt_path)
        last = window_end_index(tr)
        text, _ = render_opening(tr, ps, fragments, last)
        ws = tr.words
        user = Template(p.user).substitute(
            duration=fmt_ts(tr.duration), window_start=fmt_ts(ws[0].start), window_end=fmt_ts(ws[last].end),
            n_words=last + 1, first_word=0, last_word=last,
            speakers=", ".join(sorted(tr.talk_time)), opening=text)
        return p.system, user, 0, last

    def __call__(self, tr: Transcript, ps: PauseStats, fragments: list[TeaserFragment]) -> dict:
        if tr.duration < self.min_duration_s:
            raise OpeningLLMError(f"transcript shorter than {self.min_duration_s:.0f}s: rules only")
        system, user, first, last = self.build(tr, ps, fragments)
        client, spec = self._ensure_client()
        src_set = "SHOTVI_SPEND_SOURCE" not in os.environ
        if src_set:
            os.environ["SHOTVI_SPEND_SOURCE"] = f"seg-opening-{tr.video_id or 'unknown'}"
        try:
            ans = client.generate_json("seg_opening", spec, system, user, SCHEMA,
                                       meta={"video_id": tr.video_id, "prompt": load_prompt(self.prompt_path).tag})
        except OpeningLLMError:
            raise
        except Exception as e:  # LLMError, BudgetExceeded, network, provider init
            raise OpeningLLMError(f"{type(e).__name__}: {e}") from e
        finally:
            if src_set:
                os.environ.pop("SHOTVI_SPEND_SOURCE", None)
        out = validate(ans, first, last)
        out["meta"] = {"model": getattr(spec, "model", None), "prompt": load_prompt(self.prompt_path).tag,
                       "window_words": [first, last], "raw": ans}
        led = getattr(client, "ledger", None)
        if led is not None and hasattr(led, "summary"):
            s = led.summary()
            out["meta"]["usd"] = s.get("usd")
            out["meta"]["usd_billed"] = s.get("usd_billed")
            out["meta"]["cache_hits"] = s.get("n_cache_hits")
        return out


# ------------------------------------------------------------------ merge
def ranges_from_answer(tr: Transcript, ans: dict, rules: list[IntroRange],
                       fragments: list[TeaserFragment]) -> list[IntroRange]:
    """IntroRanges (ids unset) from a validated LLM answer, merged with the
    rules detector's ranges as described in the module docstring."""
    ws = tr.words
    reason = ans["reason"]
    conf = ans["confidence"]
    out: list[IntroRange] = []
    cs = ans["content_start_word"]
    rules_teasers = [r for r in rules if r.kind == "teaser"]

    def mk(kind, s, e, evidence, frs=()):
        return IntroRange(0, kind, s, e, ws[s].start, _end_seconds(tr, e), conf,
                          {"decided_by": "llm", "llm_reason": reason, **evidence}, list(frs))

    if ans["teaser"]:
        s, e = ans["teaser"]
        frs = [f for f in fragments if s <= f.i0 <= e and f.body_start >= ws[e].end]
        rt = next((r for r in rules_teasers if r.i0 <= e and r.i1 >= s), None)
        ev = {"n_fragments": len(frs), "rules_teaser_words": [rt.i0, rt.i1] if rt else None,
              "rules_confidence": rt.confidence if rt else None}
        out.append(mk("teaser", s, e, ev, frs))
    else:
        for r in rules_teasers:
            ev = r.evidence
            if (len(r.fragments) >= RULES_KEEP_MIN_FRAGMENTS
                    and ev.get("n_body_locations", 0) >= RULES_KEEP_MIN_BODY_LOCS):
                r.evidence = {**ev, "decided_by": "rules", "llm_disagrees": True, "llm_reason": reason}
                out.append(r)
    if ans["song"]:
        s, e = ans["song"]
        out.append(mk("music", s, e, {}))
    if ans["intro"]:
        s, e = ans["intro"]
        if not any(r.i0 <= e and r.i1 >= s for r in out):
            out.append(mk("intro", s, e, {}))
    # rules music outside LLM ranges but before the content start
    for r in rules:
        if r.kind == "music" and r.i1 < cs and not any(o.i0 <= r.i1 and o.i1 >= r.i0 for o in out):
            r.evidence = {**r.evidence, "decided_by": "rules"}
            out.append(r)
    # content start: never inside a kept range
    cs = max([cs] + [r.i1 + 1 for r in out])
    # everything before the content start not covered yet is intro
    covered = sorted((r.i0, r.i1) for r in out)
    k = 0
    for a, b in covered + [(cs, cs)]:
        if a > k and k < cs:
            e = min(a, cs) - 1
            if e >= k:
                out.append(mk("intro", k, e, {"note": "before content start"}))
        k = max(k, b + 1)
    out.sort(key=lambda r: r.i0)
    # merge adjacent intro pieces
    merged: list[IntroRange] = []
    for r in out:
        if merged and r.kind == "intro" == merged[-1].kind and r.i0 == merged[-1].i1 + 1:
            merged[-1].i1, merged[-1].end = r.i1, r.end
            continue
        merged.append(r)
    return merged


def find_all_fragments(tr: Transcript) -> list[TeaserFragment]:
    """Repeat fragments inside the opening window (evidence for the prompt)."""
    last = window_end_index(tr)
    return find_fragments(tr, search_end_s=tr.words[last].start, min_info=WEAK_FRAG_INFO)
