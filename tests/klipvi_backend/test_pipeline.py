"""backend.pipeline: clip mapping and step order / budget / cost handling, with the real steps stubbed out."""
import pytest

from backend import pipeline as P


def flow():
    clip = lambda cid, t0, t1: {"id": cid, "title": f"T {cid}", "hook": "h", "why": "w", "strength": "good",
                                "start_time": t0, "end_time": t1, "duration": t1 - t0,
                                "pieces": [{"start": t0, "end": t1, "lines": ["L0001"]}]}
    return {"clips": {"clips": [clip("c01", 10, 40), clip("c02", 100, 150), clip("c03", 200, 230)]},
            "ranking": {"order": ["c02", "c03", "c01"], "post_first": ["c02"], "verdict": "strong", "verdict_why": "x"}}


def test_clip_rows_follow_posting_order():
    rows = P.clip_rows(flow())
    assert [r["number"] for r in rows] == ["c02", "c03", "c01"]
    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert rows[0]["post_first"] and not rows[1]["post_first"]
    assert rows[0]["length_s"] == 50 and rows[0]["pieces"] == [{"start": 100, "end": 150, "lines": ["L0001"]}]
    assert rows[2]["strength"] == "good"


def test_clip_rows_without_ranking_keeps_found_order():
    f = flow(); f.pop("ranking")
    assert [r["number"] for r in P.clip_rows(f)] == ["c01", "c02", "c03"]


def _stub(monkeypatch, todo, flow_doc):
    from selection_v2 import flow_v2 as FV
    calls = []
    monkeypatch.setattr(P, "pending", lambda vid, url="": list(todo))
    monkeypatch.setattr(P, "length_s", lambda vid: 600.0)
    monkeypatch.setattr(P, "_ctx", lambda vid, url: None)
    monkeypatch.setattr(FV, "run", lambda vid, step, cfg=None, log=None: calls.append(step) or {"cost_inr": 2.5, "warnings": []})
    led = iter([0.0, 2.5, 2.5, 5.0, 5.0, 5.0, 5.0, 5.0])
    monkeypatch.setattr(P.RE, "ledger_inr", lambda: next(led))
    monkeypatch.setattr(FV, "load", lambda vid, cfg: flow_doc)
    monkeypatch.setattr(FV, "cfg_for", lambda vid: {})
    return calls


def test_run_only_pending_steps_and_sums_costs(monkeypatch):
    calls = _stub(monkeypatch, ["find", "rank", "save"], flow())
    steps, costs, saved = [], [], {}
    out = P.run({"video_id": "abcdefghijk", "source_url": None, "title": "given"},
                on_step=lambda i, k, l: steps.append((i, k)), add_cost=costs.append,
                save=lambda rows, upd: saved.update(rows=rows, upd=upd) or len(rows), log=lambda *a: None)
    assert calls == ["find", "rank"]
    assert [k for _, k in steps] == ["find", "rank", "save"] and steps[0][0] == 9
    assert sum(costs) == pytest.approx(5.0)
    assert [r["number"] for r in saved["rows"]] == ["c02", "c03", "c01"]
    assert saved["upd"]["verdict"] == "strong" and "title" not in saved["upd"]   # a given title is kept
    assert out["ran"] == ["find", "rank", "save"] and out["minutes"] == 10


def test_charge_minutes_rounds_up():
    from backend import queue as Q
    assert [Q.charge_minutes(x) for x in (1, 59.9, 60, 60.2, 759, 1311.89)] == [1, 1, 1, 2, 13, 22]


def test_test_mode_swaps_every_gemini_model():
    cfg = {"a": {"provider": "gemini", "model": "gemini-3.8-flash"},
           "b": {"clips_model": {"provider": "gemini", "model": "gemini-3.8-flash", "params": {}}},
           "c": [{"provider": "sarvam", "model": "sarvam-105b"}], "d": {"model": "not-a-spec"}}
    P._swap_models(cfg, "gemini-3.5-flash-lite")
    assert cfg["a"]["model"] == cfg["b"]["clips_model"]["model"] == "gemini-3.5-flash-lite"
    assert cfg["c"][0]["model"] == "sarvam-105b" and cfg["d"]["model"] == "not-a-spec"
