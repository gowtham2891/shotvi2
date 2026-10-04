"""LLM judge: rate a clip's kept text on the labeling-guide rubric (docs/content/labeling_guide.md).

usage: python -m eval.judge <clip file.json> [--ids a,b] [--limit N] [--model M]

Independence from the scorer (shotvi/select): own prompt (written from the labeling guide, not from the
scorer's prompt), own model (default JUDGE_MODEL, a Pro model; the scorer is expected to be a Flash model),
and the judge never sees the proposal's title, why_it_works or scores, only the words the viewer hears.

The prompt deliberately contains NO examples from the sample episodes: the labeling guide's anchors quote
FS1DI5D_hUk / TBlm6wcij94, and putting them in the judge would leak gold labels into calibration.

Responses are cached in eval/cache/judge.jsonl (key = model, params, prompt version, rendered clip, rep), and
spend is tracked in eval/cache/judge_spend.json with a hard cap (JUDGE_BUDGET_USD env, default 3.0).
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from .common import (HARD_MAX_S, HARD_MIN_S, ROOT, clips_of, edited_duration, episode_for,
                     first_seconds_text, load_json, render_cut_context, render_for_judge)

JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gemini-2.5-pro")
JUDGE_PARAMS = {"thinking_level": "low", "temperature": 0.0, "max_output_tokens": 4096}
CACHE = ROOT / "eval" / "cache" / "judge.jsonl"
SPEND = ROOT / "eval" / "cache" / "judge_spend.json"
BUDGET = float(os.environ.get("JUDGE_BUDGET_USD", "3.0"))

SYSTEM_V1 = """You are an independent reviewer of Telugu short-form video (Instagram Reels, YouTube Shorts).
An editing tool cut a clip out of a long Telugu podcast or interview. Telugu-English code-mixing is normal.
You see only the words the viewer will hear, in playback order. Decide what an experienced Telugu Shorts
editor at the creator's channel would decide. Be strict and calibrated: most stretches of a podcast are not
worth posting; a publishable clip needs a hook, a setup a stranger can follow, and a landing.

How to read the transcript:
- It is an automatic transcript; ignore small spelling or recognition errors.
- A:, B:, C: are different speakers (usually host and guest), in order of appearance.
- "/" marks a tightening cut (a few words, a filler or a pause were removed). This is normal editing.
- "//" marks a larger jump: a skipped part of the conversation, or a line moved earlier or later.
- After the transcript, a CUT LOG shows the source words just beyond every cut edge (in [brackets]). The viewer
  does NOT hear them. Use them only to check the edit: does a cut drop a negation (కాదు, లేదు, -కూడదు, not),
  a qualifier, or the question an answer belongs to, so that the kept text says something the speaker did not
  mean? Does the clip start in the middle of a thought, or stop before the thought is complete?

Score each part from 1 to 5 (halves allowed).

HOOK: will a scrolling Telugu viewer stop in the first ~3 seconds? (The first-3-seconds words are given.)
5 works on its own as a caption: bold claim, counter-intuitive question, a named celebrity plus a tease, a
  punchline, a raw emotional line; understood with zero setup.
4 clear, specific, intriguing topic or strong setup, but it takes a sentence to bite.
3 topic is clear but flat: a plain question, a generic opener, a host wind-up of 5-10 s before the answer.
2 slow or confusing: filler, a greeting, "so as I said", a reference the viewer cannot place.
1 nothing to stop for: starts mid-sentence, crosstalk, an intro or a sponsor line.
A line moved to the front (cold open) is the hook; it must intrigue and be resolved by the end of the clip,
otherwise hook <= 3 and standalone <= 2.

STANDALONE: does a first-time viewer understand who, what and why?
5 no context needed; every "ఆయన / అది / అప్పుడు / వాడు / he / that" resolves inside the clip.
4 clear, with a small gap the viewer fills easily (who the guest is, a film or product they may not know).
3 understandable but leans on outside knowledge, or one noticeable loose end.
2 a key referent or setup is missing: an answer without its question, a person never named, a joke that
  depends on something said earlier in the episode.
1 incomprehensible alone.
An unresolved referent or inside reference in the first five words caps standalone at 2.

PAYOFF: does it end on something?
5 ends on the punchline, reveal, emotional peak or a quotable conclusion, and stops right there.
4 a clear conclusion, slightly soft (a line of wrap-up after the peak).
3 a complete thought but anticlimactic; trails into general talk.
2 ends before the point lands, or on filler; the payoff is missing.
1 cut mid-sentence; the viewer feels robbed.

VALUE_EMOTION: does the viewer laugh, feel, learn, or want to argue/share? Rate the strongest one.
5 strong and shareable: a real laugh, a lump in the throat, a tip people save, a claim people fight over.
4 clearly useful, funny or moving, but a known idea or a smaller moment.
3 mildly interesting; people nod and scroll on.
2 generic ("health is important") or a story with no turn.
1 nothing: logistics, promo, small talk.

OVERALL: would the creator post this after at most a 1-2 second boundary nudge? It is a judgement, not an
average: start from the weakest of hook, standalone and payoff, then adjust for value.
5 post it first; one of the best Shorts this episode could give.
4 strong and publishable as is.
3 publishable filler: fine for a slow week or a niche audience.
2 not publishable without real re-editing (missing setup, bad ending, long ramble), even if the moment is good.
1 reject: no moment, or a hard problem.
Hard rules: if hook, standalone or payoff is 2 or less, overall is at most 2. An episode intro, guest
introduction, sign-off, sponsor read or channel promo, or an edit that changes what someone meant (drops a
negation such as కాదు / లేదు, attaches an answer to a question it did not answer) makes overall 1.

Also report:
- starts_mid_thought: true if the first kept words do not sound like the start of a thought.
- ends_mid_thought: true if the last thought is not complete.
- padded: true if the clip could lose 20% or more of its length and keep its hook, context and payoff.
- meaning_changed: true if a cut changes what a speaker meant.
- garbled_join: true if a cut produces broken, unnatural speech.
- problem: "intro_outro", "promo", "none".

Write the text fields first (what the moment is, what the viewer hears first and last, your critique as a
first-time viewer), then the numbers. Keep each text field to one or two short sentences, in English."""


# v2 (after calibrating v1): v1 lowered overall for long clips ("too long for a short") even when every part
# scored >= 3.5, contradicting the spec (no target length; creators post 98-115s Shorts) and its own
# weakest-part rule, and caught only 1/8 seeded negation drops. v2 states the length rule and the weakest-part
# anchoring, adds a cut_check field, and the cut log marks negation words just beyond a cut edge. The negation
# example is invented (not from the sample episodes).
SYSTEM_V2 = SYSTEM_V1.replace(
    """OVERALL: would the creator post this""",
    """LENGTH: there is no target length. Length follows the moment: Telugu creators publish 40-120 second
Shorts, and a story may need 90-120 seconds. Never lower a score because a clip is long. If it could lose 20% or
more of its length and keep its hook, context and payoff, set padded = true and lower overall by at most 1.
Listener reactions left in by the editor ("ఊ", "అవును", "okay", laughter) are normal in Telugu podcasts.

OVERALL: would the creator post this""").replace(
    """It is a judgement, not an
average: start from the weakest of hook, standalone and payoff, then adjust for value.""",
    """It is a judgement, not an
average: start from the weakest of hook, standalone and payoff, then adjust for value by at most one point
either way (a hard problem below overrides this).""")
SYSTEM_V2 = SYSTEM_V2.replace(
    """- meaning_changed: true if a cut changes what a speaker meant.""",
    """- cut_check: before meaning_changed, go through the CUT LOG. For every cut marked "contains negation", and
  every cut that removes a qualifier or the question an answer belongs to, read the kept sentence around the
  cut and say in a few words whether it now claims something the speaker did not say. Telugu negation often
  sits in the sentence-final verb (కాదు, లేదు, -కూడదు): "ఇది మంచిది కాదు" (this is not good) cut to
  "ఇది మంచిది" says the opposite. Dropping a repeated or filler negation ("లేదు లేదు" -> "లేదు") is fine.
- meaning_changed: true if a cut changes what a speaker meant (use your cut_check).""")
assert SYSTEM_V2 != SYSTEM_V1

PROMPTS = {"judge-v1": SYSTEM_V1, "judge-v2": SYSTEM_V2}
PROMPT_VERSION = os.environ.get("JUDGE_PROMPT", "judge-v2")
SYSTEM = PROMPTS[PROMPT_VERSION]

SCHEMA = {
    "type": "object",
    "properties": {
        "moment": {"type": "string"},
        "hook_heard": {"type": "string"},
        "ending_heard": {"type": "string"},
        "critique": {"type": "string"},
        "hook": {"type": "number"},
        "standalone": {"type": "number"},
        "payoff": {"type": "number"},
        "value_emotion": {"type": "number"},
        "overall": {"type": "number"},
        "starts_mid_thought": {"type": "boolean"},
        "ends_mid_thought": {"type": "boolean"},
        "padded": {"type": "boolean"},
        "cut_check": {"type": "string"},
        "meaning_changed": {"type": "boolean"},
        "garbled_join": {"type": "boolean"},
        "problem": {"type": "string", "enum": ["intro_outro", "promo", "none"]},
    },
    "required": ["moment", "hook_heard", "ending_heard", "critique", "hook", "standalone", "payoff",
                 "value_emotion", "overall", "starts_mid_thought", "ends_mid_thought", "padded", "cut_check", "meaning_changed", "garbled_join", "problem"],
}

SCHEMA_V2 = SCHEMA
SCHEMA_V1 = {"type": "object",
             "properties": {k: v for k, v in SCHEMA["properties"].items() if k != "cut_check"},
             "required": [k for k in SCHEMA["required"] if k != "cut_check"]}
SCHEMAS = {"judge-v1": SCHEMA_V1, "judge-v2": SCHEMA_V2}

DIMS = ("hook", "standalone", "payoff", "value_emotion", "overall")


def build_prompt(clip, ep, version="judge-v2"):
    ed = edited_duration(clip, ep)
    mark = version != "judge-v1"
    return (f"Clip length after editing: {ed:.0f} seconds, {len(clip['pieces'])} kept piece(s).\n"
            f"First ~3 seconds: \"{first_seconds_text(clip, ep)}\"\n\n"
            f"TRANSCRIPT (what the viewer hears, playback order):\n{render_for_judge(clip, ep)}\n\n"
            f"CUT LOG (not heard; source words beyond each cut edge):\n{render_cut_context(clip, ep, mark_negation=mark)}\n\n"
            "Rate this clip.")


def publishable(r, ed):
    """labeling_guide.md 'Publishable', computed from the judge's scores and flags."""
    return (r["overall"] >= 3 and min(r["hook"], r["standalone"], r["payoff"]) >= 3
            and not r.get("meaning_changed") and r.get("problem", "none") == "none"
            and HARD_MIN_S <= ed <= HARD_MAX_S)


def _key(model, params, prompt, rep, version):
    s = json.dumps([model, params, version, PROMPTS[version], prompt, rep], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


_cache = None


def _load_cache():
    global _cache
    if _cache is None:
        _cache = {}
        if CACHE.exists():
            for line in CACHE.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    _cache[d["key"]] = d
    return _cache


def spent():
    return json.loads(SPEND.read_text())["usd"] if SPEND.exists() else 0.0


def _add_spend(usd, calls=1):
    d = json.loads(SPEND.read_text()) if SPEND.exists() else {"usd": 0.0, "calls": 0}
    d["usd"] = round(d["usd"] + usd, 6)
    d["calls"] += calls
    SPEND.parent.mkdir(parents=True, exist_ok=True)
    SPEND.write_text(json.dumps(d))


class _Client:
    def __init__(self):
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        sys.path.insert(0, str(ROOT))
        from shotvi.llm.base import ModelSpec, RetryableError
        from shotvi.llm.gemini import GeminiProvider
        from shotvi.llm.pricing import cost_usd
        self.ModelSpec, self.Retryable, self.cost_usd = ModelSpec, RetryableError, cost_usd
        self.provider = GeminiProvider()

    def call(self, model, params, system, prompt, schema):
        spec = self.ModelSpec("gemini", model, dict(params))
        last = None
        for attempt in range(4):
            try:
                t0 = time.time()
                r = self.provider.generate(spec, system, prompt, schema)
                return r, time.time() - t0
            except self.Retryable as e:
                last = e
                resp = getattr(e, "response", None)
                if resp is not None:   # billed even though unusable
                    _add_spend(self.cost_usd(model, resp.input_tokens, resp.output_tokens + resp.thought_tokens) or 0)
                time.sleep(2 + 4 * attempt)
        raise last


_client = None


def judge_clip(clip, ep, model=JUDGE_MODEL, params=None, rep=0, offline=False, version=None):
    """Returns the judge record (cached). offline=True never calls the API (returns None on cache miss)."""
    global _client
    params = dict(JUDGE_PARAMS if params is None else params)
    if rep:
        params.setdefault("temperature", 0.0)
        params["temperature"] = 1.0   # repeats sample at default temperature to measure judge noise
    version = version or PROMPT_VERSION
    prompt = build_prompt(clip, ep, version)
    key = _key(model, params, prompt, rep, version)
    cache = _load_cache()
    if key in cache:
        return cache[key]
    if offline:
        return None
    if spent() >= BUDGET:
        raise RuntimeError(f"judge budget reached: ${spent():.2f} >= ${BUDGET:.2f} (JUDGE_BUDGET_USD)")
    if _client is None:
        _client = _Client()
    last_err = None
    for _ in range(2):
        r, secs = _client.call(model, params, PROMPTS[version], prompt, SCHEMAS[version])
        out_tok = r.output_tokens + r.thought_tokens
        usd = _client.cost_usd(model, r.input_tokens, out_tok) or 0.0
        _add_spend(usd)
        try:
            res = json.loads(r.text)
            for d in DIMS:
                res[d] = max(1.0, min(5.0, float(res[d])))
            break
        except (ValueError, KeyError, TypeError) as e:
            last_err = e
    else:
        raise RuntimeError(f"judge output unparsable: {last_err}")
    ed = edited_duration(clip, ep)
    res["publishable"] = publishable(res, ed)
    rec = {"key": key, "model": model, "params": params, "prompt_version": version, "rep": rep,
           "clip_id": clip.get("id"), "video_id": None, "result": res,
           "usage": {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                     "thought_tokens": r.thought_tokens, "usd": round(usd, 6), "secs": round(secs, 2),
                     "model_version": r.model_version}}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    cache[key] = rec
    return rec


def judge_many(items, workers=4, **kw):
    """items: list of (clip, ep). Returns list of records in the same order (None where failed)."""
    from concurrent.futures import ThreadPoolExecutor
    out = [None] * len(items)

    def one(i):
        c, ep = items[i]
        try:
            out[i] = judge_clip(c, ep, **kw)
        except Exception as e:  # noqa: BLE001 - report and continue; the caller counts missing judgements
            print(f"  judge failed for {c.get('id')}: {str(e)[:200]}", file=sys.stderr)
    # cache hits first, serially (cheap), then misses in parallel
    miss = []
    for i, (c, ep) in enumerate(items):
        r = judge_clip(c, ep, offline=True, **{k: v for k, v in kw.items() if k != "offline"})
        if r is not None:
            out[i] = r
        else:
            miss.append(i)
    if miss and not kw.get("offline"):
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, miss))
    return out


def main(argv=None):
    from .common import utf8_stdout
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--ids", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default=JUDGE_MODEL)
    a = ap.parse_args(argv)
    doc = load_json(a.file)
    ep = episode_for(doc)
    clips = clips_of(doc)
    if a.ids:
        want = set(a.ids.split(","))
        clips = [c for c in clips if c["id"] in want]
    if a.limit:
        clips = clips[:a.limit]
    recs = judge_many([(c, ep) for c in clips], model=a.model)
    for c, r in zip(clips, recs):
        if r is None:
            print(c["id"], "FAILED")
            continue
        s = r["result"]
        print(f"{c['id']}: " + " ".join(f"{d}={s[d]:g}" for d in DIMS) + f" pub={s['publishable']} "
              f"${r['usage']['usd']:.4f} | {s['critique'][:160]}")
    print(f"judge spend so far: ${spent():.3f} of ${BUDGET:.2f}")


if __name__ == "__main__":
    main()
