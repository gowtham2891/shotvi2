"""Reason classes of the cold-viewer critic's verdicts (founder, 2026-10-29).

Every failing run's reasons are sorted into two classes:
- ``confusion``: the viewer did not get it: Q7 missing context, or the Q3 moment off / cut out of the clip. A hook-rule
  failure (does not want the next part, a resolving punchline at the end) counts here too: it blocks like confusion.
- ``pace``: swipe stretches only.

A record's class: None when no run failed; ``confusion`` when any failing run has a confusion reason; else ``pace``.
Stage 6 never ships a confusion FAIL; a pace FAIL ships with a note, and Stage 3 builds a critic_trimmed sibling that
shortens the flagged stretches. Shared by Stages 3, 5 and 6 (no stage imports another for this).
"""
from __future__ import annotations

import json
import re

PACE_PREFIX = "swipe stretch over the limit"
NOTE_PREFIXES = ("moment found by text", "major missing context (noted, not failing a hook)")


def classify_reason(text: str) -> str | None:
    """'pace' | 'confusion' | None (a note that does not fail the run)."""
    t = (text or "").strip()
    if not t or t.startswith(NOTE_PREFIXES):
        return None
    if t.startswith(PACE_PREFIX):
        return "pace"
    return "confusion"


def run_classes(run: dict) -> list[str]:
    """The classes of one run's failure reasons (empty for a passing run)."""
    if run.get("pass"):
        return []
    if run.get("fail_classes") is not None:
        return list(run["fail_classes"])
    return sorted({c for c in (classify_reason(r) for r in run.get("reasons") or []) if c})


def reason_class(runs: list[dict]) -> str | None:
    failing = [r for r in runs or [] if not r.get("pass")]
    if not failing:
        return None
    classes = {c for r in failing for c in run_classes(r)}
    return "confusion" if "confusion" in classes or not classes else "pace"


def pace_stretches(runs: list[dict]) -> list[tuple[float, float]]:
    """The swipe stretches over the limit named by the failing runs (clip seconds), merged where they overlap."""
    spans = []
    for r in runs or []:
        if r.get("pass"):
            continue
        if r.get("long_swipes"):
            spans += [(float(s["from_s"]), float(s["to_s"])) for s in r["long_swipes"]]
            continue
        for reason in r.get("reasons") or []:                      # older records: the stretches are only in the reason text
            if reason.startswith(PACE_PREFIX):
                spans += [(float(a), float(b)) for a, b in re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?) s", reason)]
    spans.sort()
    out: list[list[float]] = []
    for a, b in spans:
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def record_key(z: dict) -> str:
    """The critic record's content key (type, variant, kept segments) as a string, the way Stage 5 stores it."""
    ck = z.get("content_key")
    if ck is None:
        segs = [[round(float(m["ep_start"]), 3), round(float(m["ep_end"]), 3)] for m in z.get("time_map") or []]
        ck = [z.get("type"), z.get("variant"), segs]
    return json.dumps(ck)
