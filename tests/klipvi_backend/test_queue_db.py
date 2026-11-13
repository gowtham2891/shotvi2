"""backend.queue against the real Supabase database, inside one transaction that is rolled back (nothing kept).
Opt-in (network):  KLIPVI_DB_TESTS=1 venv/bin/python -m pytest tests/klipvi_backend"""
import os

import pytest

from backend import queue as Q

pytestmark = pytest.mark.skipif(os.environ.get("KLIPVI_DB_TESTS") != "1", reason="set KLIPVI_DB_TESTS=1 (network)")


@pytest.fixture
def conn():
    from backend.db import connect
    c = connect(autocommit=False)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def ws(conn, minutes=100, plan="trial"):
    return conn.execute("insert into public.workspaces (name, kind, plan, minutes_left) values ('test ws', 'team', %s, %s) "
                        "returning id", (plan, minutes)).fetchone()["id"]


def minutes(conn, w):
    r = conn.execute("select minutes_left, minutes_held from public.workspaces where id = %s", (w,)).fetchone()
    return float(r["minutes_left"]), float(r["minutes_held"])


def due(conn, job_id):
    conn.execute("update public.jobs set run_after = now() - interval '1 day' where id = %s", (job_id,))


def test_submit_claim_steps_succeed(conn):
    w = ws(conn)
    out = Q.submit(conn, w, "https://youtu.be/abcdefghijk", "abcdefghijk", 600)
    assert out["episode"]["status"] == "waiting" and out["job"]["status"] == "waiting"
    conn.execute("update public.jobs set run_after = now() - interval '1 day' where id = %s", (out["job"]["id"],))
    job = Q.claim(conn, "test-worker")
    assert job["id"] == out["job"]["id"] and job["attempts"] == 1 and job["status"] == "running"
    assert Q.claim(conn, "test-worker-2") is None or Q.claim(conn, "x")["id"] != job["id"]
    Q.set_step(conn, job, "find", "Finding the clips (9 of 11)")
    Q.add_cost(conn, job["id"], 12.345)
    n = Q.save_clips(conn, job["episode_id"], [{"number": "c01", "rank": 1, "post_first": True, "title": "t",
                                                 "hook": "h", "posting_text": None, "why": "w", "start_s": 1,
                                                 "end_s": 31, "length_s": 30, "pieces": [], "strength": "good"}],
                     {"verdict": "thin", "length_s": 600})
    assert n == 1
    Q.succeed(conn, job)
    ep = conn.execute("select status, step, verdict from public.episodes where id = %s", (job["episode_id"],)).fetchone()
    assert ep == {"status": "done", "step": "Ready", "verdict": "thin"}
    u = conn.execute("select minutes_charged, cost_inr from public.usage where episode_id = %s", (job["episode_id"],)).fetchone()
    assert float(u["minutes_charged"]) == 10.0 and float(u["cost_inr"]) == 12.35
    assert minutes(conn, w) == (90.0, 0.0)              # 10 min held at submit, spent at the end


def test_fail_retries_then_fails(conn):
    w = ws(conn)
    out = Q.submit(conn, w, "https://youtu.be/abcdefghijk", "abcdefghijk", 600)
    jid = out["job"]["id"]
    for attempt in (1, 2):
        conn.execute("update public.jobs set run_after = now() - interval '1 day' where id = %s", (jid,))
        job = Q.claim(conn, "w")
        assert job["id"] == jid and job["attempts"] == attempt
        assert Q.fail(conn, job, "boom") == "retry"
        ep = conn.execute("select status, message from public.episodes where id = %s", (job["episode_id"],)).fetchone()
        assert ep["message"] == Q.MSG_RETRYING
    conn.execute("update public.jobs set run_after = now() - interval '1 day' where id = %s", (jid,))
    job = Q.claim(conn, "w")
    assert job["attempts"] == 3 and Q.fail(conn, job, "boom") == "failed"
    ep = conn.execute("select status, message from public.episodes where id = %s", (job["episode_id"],)).fetchone()
    assert ep == {"status": "failed", "message": Q.MSG_FAILED}


def test_stale_job_is_requeued(conn):
    w = ws(conn)
    out = Q.submit(conn, w, "https://youtu.be/abcdefghijk", "abcdefghijk", 600)
    conn.execute("update public.jobs set run_after = now() - interval '1 day' where id = %s", (out["job"]["id"],))
    job = Q.claim(conn, "w")
    conn.execute("update public.jobs set heartbeat_at = now() - interval '1 hour' where id = %s", (job["id"],))
    assert Q.requeue_stale(conn) >= 1
    assert conn.execute("select status from public.jobs where id = %s", (job["id"],)).fetchone()["status"] == "waiting"


def test_minutes_hold_spend_refund_and_refuse(conn):
    w = ws(conn, minutes=60)
    a = Q.submit(conn, w, "https://youtu.be/aaaaaaaaaaa", "aaaaaaaaaaa", 25 * 60)          # 25 min
    assert minutes(conn, w) == (60.0, 25.0) and a["episode"]["minutes_state"] == "held"
    b = Q.submit(conn, w, "https://youtu.be/bbbbbbbbbbb", "bbbbbbbbbbb", 19 * 60 + 10)     # 19.2 -> 20 min
    assert minutes(conn, w) == (60.0, 45.0)
    with pytest.raises(Q.NotEnoughMinutes) as e:                                          # 22 > 15 free
        Q.submit(conn, w, "https://youtu.be/ccccccccccc", "ccccccccccc", 22 * 60)
    assert e.value.needs == 22 and e.value.free == 15
    assert minutes(conn, w) == (60.0, 45.0)                                               # nothing created
    for x in (a, b):
        due(conn, x["job"]["id"])
    ja = Q.claim(conn, "w"); jb = Q.claim(conn, "w")
    Q.succeed(conn, ja if ja["episode_id"] == a["episode"]["id"] else jb)                 # A: 25 spent
    assert minutes(conn, w) == (35.0, 20.0)
    jb_ = jb if jb["episode_id"] == b["episode"]["id"] else ja
    conn.execute("update public.jobs set attempts = max_attempts where id = %s", (jb_["id"],))
    assert Q.fail(conn, jb_, "boom") == "failed"                                          # B: 20 refunded
    assert minutes(conn, w) == (35.0, 0.0)
    states = {r["video_id"]: r["minutes_state"] for r in conn.execute(
        "select video_id, minutes_state from public.episodes where workspace_id = %s", (w,)).fetchall()}
    assert states == {"aaaaaaaaaaa": "spent", "bbbbbbbbbbb": "refunded"}


def test_internal_plan_is_not_counted(conn):
    w = ws(conn, minutes=0, plan="internal")
    out = Q.submit(conn, w, "https://youtu.be/abcdefghijk", "abcdefghijk", 3600)
    assert out["episode"]["minutes_state"] is None and minutes(conn, w) == (0.0, 0.0)


def test_runaway_cost_stops_and_refunds(conn):
    from backend import worker as W
    w = ws(conn, minutes=60)
    out = Q.submit(conn, w, "https://youtu.be/abcdefghijk", "abcdefghijk", 10 * 60)      # limit max(50, 10 x 12)
    due(conn, out["job"]["id"])
    job = Q.claim(conn, "w")

    def fake_run(ep, on_step, add_cost, save, log, **kw):
        add_cost(100.0)
        add_cost(30.0)                     # 130 > 120: stops here
        raise AssertionError("must not get here")
    assert W.handle(conn, job, log=lambda *a: None, run=fake_run) == "failed"
    j = conn.execute("select status, error, cost_inr from public.jobs where id = %s", (job["id"],)).fetchone()
    assert j["status"] == "failed" and j["error"].startswith("RunawayCost: RUNAWAY") and float(j["cost_inr"]) == 130
    assert minutes(conn, w) == (60.0, 0.0)
