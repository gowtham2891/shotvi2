"""Critic calibration (founder, 2026-10-30): the Stage 5 cold-viewer critic, UNCHANGED (same system prompt, questions,
schema, model, 3 independent runs, validation and swipe limit), run on the channels' own posted Shorts of SCRIPTED-SERIES
episodes, as posted. Does the critic pass what the channels post, and do its verdicts track views?

Scope: only episodes whose Stage 1B content_type is ``scripted_series`` (podcasts, vodcasts and talking heads are never
used). Shorts: ``data/channel_shorts/<episode>.json`` accepted entries whose ``local_file`` exists.

A posted Short has no expected payoff line, so the Q3 "moment off" check does not apply: a run fails on ``confusion`` only
through Q7 missing context (not none / minor), and on ``pace`` through a swipe stretch over ``stage5.compare.swipe_max_s``.
Q3's best moment is recorded. Output: runs/selection_v2/_calibration/critic_shorts.json.

    python -m selection_v2.eval.critic_calibration --estimate
    python -m selection_v2.eval.critic_calibration --run
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

from .. import critic_class as CC
from .. import stage5_critic as S5
from ..config import ROOT, inr_per_usd, load_config
from ..media import content_key, media_part, upload
from ..paths import write_json
from ..runlog import append_log
from ..stage1_episode_pass import load_prompt

OUT = ROOT / "runs" / "selection_v2" / "_calibration" / "critic_shorts.json"


def scripted_shorts(cfg: dict, root: Path = ROOT) -> tuple[list[dict], list[str]]:
    """[{episode, short_id, channel, path, duration, views, views_vs_median, ...}] for scripted-series episodes; plus notes
    on what was left out and why."""
    rows, notes = [], []
    for p in sorted((root / "data" / "channel_shorts").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        vid = d.get("episode_id") or p.stem
        fp = root / cfg["paths"]["runs_dir"] / vid / "episode_facts.json"
        ct = (json.loads(fp.read_text(encoding="utf-8")).get("episode_pass") or {}).get("content_type") if fp.exists() else None
        acc = d.get("accepted") or []
        if ct != "scripted_series":
            if acc:
                notes.append(f"{vid}: content_type {ct!r}, {len(acc)} Short(s) left out")
            continue
        for s in acc:
            lf = root / (s.get("local_file") or "")
            if not s.get("local_file") or not lf.exists():
                notes.append(f"{vid} {s['short_id']}: video not on disk, left out")
                continue
            rows.append({"episode": vid, "short_id": s["short_id"], "channel": str(s.get("channel") or "").strip(), "path": str(lf),
                         "duration": float(s.get("duration") or 0), "views": s.get("views"), "channel_median_views": s.get("channel_median_views"),
                         "views_vs_median": s.get("views_vs_median"), "title": s.get("title") or "", "url": s.get("url")})
    return rows, notes


def estimate(cfg: dict, rows: list[dict]) -> dict:
    c5, r, rate = cfg["stage5"], cfg["rates"], inr_per_usd(cfg)
    n, tps = int(c5["runs_per_clip"]), float(r["tokens_per_s_video_low"])
    per = []
    for x in rows:
        in_tok = (int(x["duration"] * tps) + int(c5["prompt_tokens"])) * n
        out_tok = int(c5["expected_output_tokens"]) * n
        per.append(round((in_tok * r["usd_per_1m_input"] + out_tok * r["usd_per_1m_output"]) / 1e6 * rate, 2))
    return {"n_shorts": len(rows), "runs_per_short": n, "per_short_inr": per, "total_inr": round(sum(per), 2)}


def judge(ans: dict, clip_len: float, cfg5: dict) -> dict:
    """One run, calibration rules: Q7 missing context -> confusion; a swipe over the limit -> pace."""
    cmp = cfg5["compare"]
    swipe_max = float(cmp["swipe_max_s"])
    ok_missing = set(cmp.get("missing_ok") or ["none", "minor"])
    reasons = []
    long_swipes = [s for s in ans.get("swipe") or [] if float(s["to_s"]) - float(s["from_s"]) > swipe_max]
    if ans["confusion"]["missing"] not in ok_missing:
        reasons.append(f"missing context: {ans['confusion']['missing']} ({(ans['confusion'].get('what') or '')[:80]})")
    if long_swipes:
        reasons.append("swipe stretch over the limit: " + "; ".join(f"{float(s['from_s']):.0f}-{float(s['to_s']):.0f} s" for s in long_swipes))
    ok = not reasons
    return {"pass": ok, "reasons": reasons, "long_swipes": long_swipes, "located_s": float(ans["best_moment"]["second"]),
            "fail_classes": [] if ok else sorted({k for k in (CC.classify_reason(x) for x in reasons) if k})}


def run(cfg: dict, rows: list[dict], client=None, uploader=None, deleter=None, log=print) -> dict:
    c5 = cfg["stage5"]
    spec = ModelSpec.from_dict(cfg["models"].get("stage5") or cfg["models"]["default"])
    sections = load_prompt(S5.PROMPT_PATH)
    schema = json.loads(S5.SCHEMA_PATH.read_text(encoding="utf-8"))
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient, get_provider
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(c5.get("http_retries", 1)))
        try:
            get_provider(spec.provider).timeout = float(c5.get("http_timeout_s", 600))
        except Exception:
            pass
    uploader = uploader or upload
    r, rate = cfg["rates"], inr_per_usd(cfg)
    n_runs = int(c5["runs_per_clip"])
    out = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"shorts": {}}
    for x in rows:
        key = f"{x['episode']}:{x['short_id']}"
        if key in out["shorts"] and len(out["shorts"][key].get("runs") or []) == n_runs:
            log(f"{x['short_id']}: already judged, kept"); continue
        ts = time.perf_counter()
        clip_len = x["duration"]
        up = uploader(Path(x["path"]), "video")
        part = media_part(up, "video", c5.get("media_fps"), key=f"sha:{content_key(Path(x['path']))}")
        runs, usage = [], {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
        for n in range(1, n_runs + 1):
            errors, ans = [], None
            for attempt in range(int(c5.get("retries", 1)) + 1):
                prompt = (sections["questions"] + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) if errors else "")
                          + "\n\n" + sections["output_format"] + f"\n\n(clip length {clip_len:.1f} s; answer sheet {n} of {n_runs})"
                          + (f" (attempt {attempt + 1}: every second you name must lie between 0 and {clip_len:.1f})" if attempt else ""))
                obj, raw = client.generate_json(S5.ROLE, spec, sections["system"], prompt, schema,
                                                meta={"video_id": x["episode"], "stage": "5-calibration", "clip": x["short_id"], "run": n}, want_raw=True, media=[part])
                for k in usage:
                    usage[k] += int(getattr(raw, k) or 0)
                errors = S5.validate(obj, clip_len)
                if not errors:
                    ans = obj; break
            if ans is None:
                raise S5.CriticError(f"{x['short_id']} run {n}: invalid after retries: " + " | ".join(errors[:4]))
            runs.append({"run": n, "answers": ans, **judge(ans, clip_len, c5)})
        if c5.get("delete_upload_after_runs", True):
            try:
                from ..media import delete_upload
                (deleter or delete_upload)(up)
            except Exception:  # noqa: BLE001
                pass
        cost = round(((usage["input_tokens"] - min(usage["cached_tokens"], usage["input_tokens"])) * r["usd_per_1m_input"]
                      + min(usage["cached_tokens"], usage["input_tokens"]) * r["usd_per_1m_cached_input"]
                      + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * rate, 3)
        n_pass = sum(1 for z in runs if z["pass"])
        out["shorts"][key] = {**{k: v for k, v in x.items() if k != "path"}, "verdict": S5.verdict_of(n_pass, n_runs, c5), "n_pass": n_pass, "n_runs": n_runs,
                              "reason_class": CC.reason_class(runs), "pace_stretches": CC.pace_stretches(runs),
                              "best_moment_s": [z["located_s"] for z in runs], "runs": runs, "usage": {**usage, "cost_inr": cost}}
        out["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S"); out["model"] = spec.to_dict(); out["prompt_file"] = S5.PROMPT_PATH.name
        write_json(OUT, out)
        append_log(cfg, {"episode": x["episode"], "stage": f"5-calibration:{x['short_id']}", "status": "ok", "duration_s": round(time.perf_counter() - ts, 1),
                         "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cost_inr_est": cost,
                         "note": f"{out['shorts'][key]['verdict']} {n_pass}/{n_runs}"})
        log(f"{x['short_id']}: {out['shorts'][key]['verdict']} {n_pass}/{n_runs}, ₹{cost:.2f}")
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i]); rk = [0.0] * len(v); i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[order[k]] = (i + j) / 2
            i = j + 1
        return rk
    a, b = ranks(xs), ranks(ys)
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((p - ma) * (q - mb) for p, q in zip(a, b))
    den = (sum((p - ma) ** 2 for p in a) * sum((q - mb) ** 2 for q in b)) ** 0.5
    return round(num / den, 2) if den else None


def report(out: dict) -> dict:
    by_ch: dict[str, list[dict]] = {}
    for z in out["shorts"].values():
        by_ch.setdefault(z["channel"], []).append(z)
    chans = {}
    for ch, zs in by_ch.items():
        vm = [z["views_vs_median"] for z in zs if z.get("views_vs_median") is not None]
        chans[ch] = {"n": len(zs), "pass": sum(1 for z in zs if z["verdict"] == "PASS"), "borderline": sum(1 for z in zs if z["verdict"] == "BORDERLINE"),
                     "fail": sum(1 for z in zs if z["verdict"] == "FAIL"),
                     "median_views_vs_median_pass": statistics.median([z["views_vs_median"] for z in zs if z["verdict"] != "FAIL" and z.get("views_vs_median") is not None] or [0]) if any(z["verdict"] != "FAIL" for z in zs) else None,
                     "median_views_vs_median_fail": statistics.median([z["views_vs_median"] for z in zs if z["verdict"] == "FAIL" and z.get("views_vs_median") is not None] or [0]) if any(z["verdict"] == "FAIL" for z in zs) else None,
                     "spearman_npass_vs_views": spearman([z["n_pass"] for z in zs if z.get("views_vs_median") is not None], vm)}
    return chans


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m selection_v2.eval.critic_calibration")
    ap.add_argument("--estimate", action="store_true"); ap.add_argument("--run", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config()
    rows, notes = scripted_shorts(cfg)
    for n in notes:
        print("note:", n)
    if a.estimate or not a.run:
        e = estimate(cfg, rows); print(f"{e['n_shorts']} Shorts x {e['runs_per_short']} runs: ₹{e['total_inr']}")
        return 0
    out = run(cfg, rows)
    print(json.dumps(report(out), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
