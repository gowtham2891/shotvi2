"""CLI: python -m shotvi.select <transcript.json> [...] --out runs/<run_id>/ [--config shotvi/select/configs/flash.json]

Writes, per transcript, ``<video_id>.json`` (kind: proposals) and ``debug/<video_id>.json``; plus ``clips.md`` and
``cost.json`` covering all transcripts of the run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from shotvi.llm import BudgetExceeded, Ledger, LLMClient

from .config import load_config
from .output import clips_md, cost_block, run_doc, write_debug
from .pipeline import run_video


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m shotvi.select")
    ap.add_argument("transcripts", nargs="+")
    ap.add_argument("--out", required=True, help="run directory, e.g. runs/<run_id>/")
    ap.add_argument("--config", default=None, help="JSON config merged over the defaults")
    ap.add_argument("--run-id", default=None, help="defaults to the --out directory name")
    ap.add_argument("--budget", type=float, default=None, help="USD spend cap for this run (billed, not cached)")
    ap.add_argument("--max-moments", type=int, default=None, help="dev only: edit at most N moments per video")
    ap.add_argument("--moments", default=None, help="dev only: comma-separated moment ids (e.g. m004,m014)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="cache only: never call the LLM; report what would be billed")
    a = ap.parse_args(argv)
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    over = {}
    if a.budget is not None:
        over["budget_usd"] = a.budget
    if a.moments:
        over["only_moments"] = [x.strip() for x in a.moments.split(",") if x.strip()]
    if a.max_moments:
        over["max_moments"] = a.max_moments
    cfg = load_config(a.config, over)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    run_id = a.run_id or out.resolve().name
    ledger = Ledger(max_usd=cfg["budget_usd"], price_overrides=cfg.get("pricing"))
    total = {"run_id": run_id, "config": cfg["name"], "videos": {}}
    md = []
    for t in a.transcripts:
        vid_ledger = Ledger(max_usd=None, price_overrides=cfg.get("pricing"))
        llm = _TeeClient(ledger, vid_ledger, cfg, use_cache=not a.no_cache)
        llm.cache_only = a.dry_run
        try:
            res = run_video(t, cfg, llm)
        except BudgetExceeded as e:
            print(f"STOPPED: {e}. Spent ${ledger.billed_usd():.3f}.", file=sys.stderr)
            (out / "cost.json").write_text(json.dumps({**total, "stopped": str(e), **ledger.summary()},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
            return 2
        audio_s = res["ctx"].seg.transcript.duration
        cost = cost_block(vid_ledger.summary(), audio_s, cfg)
        doc = run_doc(res, cfg, run_id, cost)
        vid = res["ctx"].video_id
        (out / f"{vid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        write_debug(out, vid, res["debug"])
        md.append(clips_md(res, doc))
        total["videos"][vid] = cost
        if a.dry_run:
            import collections
            by = collections.Counter(m["role"] for m in llm.misses)
            print(f"[{vid}] DRY RUN cache misses by role: {dict(by)}; est input tokens "
                  f"{sum(m['est_input_tokens'] for m in llm.misses)}")
        print(f"[{vid}] shown {len(doc['proposals'])} | ${cost['usd']:.3f} full, "
              f"${cost['usd_billed_this_run']:.3f} billed | {cost['n_llm_calls']} calls")
    total["total"] = ledger.summary()
    (out / "cost.json").write_text(json.dumps(total, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "clips.md").write_text("\n\n".join(md), encoding="utf-8")
    print(f"wrote {out}")
    return 0


class _TeeClient(LLMClient):
    """LLMClient that records every call in both the run ledger (budget cap) and a per-video ledger."""

    def __init__(self, run_ledger: Ledger, vid_ledger: Ledger, cfg: dict, use_cache: bool = True):
        super().__init__(run_ledger, cache_dir=cfg["cache_dir"], use_cache=use_cache)
        self.vid_ledger = vid_ledger
        orig = run_ledger.record

        def record(*args, **kw):
            orig(*args, **kw)
            vid_ledger.record(*args, **kw)
        self.ledger = _LedgerProxy(run_ledger, record)


class _LedgerProxy:
    def __init__(self, base: Ledger, record):
        self._base, self.record = base, record

    def __getattr__(self, k):
        return getattr(self._base, k)


if __name__ == "__main__":
    sys.exit(main())
