"""Every distinct Scribe audio-event tag across scribe_dataset/, with counts, episodes and the class it maps to under
config/pipeline.yaml ``tags`` (for reviewing the regexes in the debug app).

    python -m selection_v2.tags_review            # prints the table
"""
from __future__ import annotations

import json
import unicodedata
from collections import defaultdict

from .config import load_config
from .paths import SCRIBE_DIR
from .stage1_facts import classify_tag


def tag_tally(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load_config()
    counts: dict[str, int] = defaultdict(int)
    eps: dict[str, set] = defaultdict(set)
    examples: dict[str, str] = {}
    for d in sorted(SCRIBE_DIR.iterdir()) if SCRIBE_DIR.exists() else []:
        p = d / f"{d.name}_scribe_transcript.json"
        if not p.exists():
            continue
        try:
            tr = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            counts[f"<unreadable {d.name}: {e}>"] += 1
            continue
        for e in tr.get("audio_events") or []:
            t = unicodedata.normalize("NFC", e.get("text") or "").strip()
            counts[t] += 1
            eps[t].add(d.name)
            examples.setdefault(t, f"{d.name} @ {e.get('start')}")
    rows = [{"tag": t, "class": classify_tag(t, cfg["tags"]), "count": n, "n_episodes": len(eps[t]),
             "episodes": ", ".join(sorted(eps[t])), "first_seen": examples.get(t, "")} for t, n in counts.items()]
    rows.sort(key=lambda r: (-r["count"], r["tag"]))
    return rows


if __name__ == "__main__":
    for r in tag_tally():
        print(f"{r['count']:5d}  {r['class']:9s}  {r['tag']}   ({r['n_episodes']} ep)")
