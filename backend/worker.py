"""The worker: claims a job, runs the episode's pipeline, saves the clips, repeats.

    python -m backend.worker            # loop forever (Ctrl+C to stop after the current poll)
    python -m backend.worker --once     # handle at most one job, then exit

A heartbeat thread (own connection) refreshes ``jobs.heartbeat_at`` every 30 s while a step runs, so
``queue.requeue_stale`` can tell a slow Gemini call from a dead worker.
"""
from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import traceback

from . import pipeline as P
from . import queue as Q
from .db import connect

POLL_S = 10
HEARTBEAT_S = 30
# Runaway guard: a run that costs us more than this per minute of episode is stopped (a bug, e.g. a retry loop),
# the customer's minutes are refunded and the job is marked RUNAWAY. Normal: ≈ ₹2.7-3.9/min now (Nov 2026),
# ≈ ₹4.5-6.2 from Jan 2027 when Gemini prices double: raise it to ~18 then (env KLIPVI_RUNAWAY_INR_PER_MIN).
RUNAWAY_INR_PER_MIN = float(os.environ.get("KLIPVI_RUNAWAY_INR_PER_MIN", "12"))
RUNAWAY_MIN_INR = 50.0          # never below this, so very short episodes are not stopped by noise


class RunawayCost(RuntimeError):
    """The run's cost passed the runaway limit; not retried."""


class Heartbeat:
    def __init__(self, job_id: int):
        self.job_id, self.stop = job_id, threading.Event()
        self.t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        try:
            with connect() as c:
                while not self.stop.wait(HEARTBEAT_S):
                    Q.heartbeat(c, self.job_id)
        except Exception as e:      # a missed heartbeat only risks a re-queue later; never kill the job for it
            print(f"    heartbeat stopped: {type(e).__name__}: {e}")

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.stop.set()


def handle(conn, job: dict, log=print, run=P.run) -> str:
    """Run one claimed job to the end. Returns 'done', 'retry' or 'failed'."""
    ep = conn.execute("select * from public.episodes where id = %s", (job["episode_id"],)).fetchone()
    minutes = float(ep["minutes"] or Q.charge_minutes(ep["length_s"] or 60))
    limit = max(RUNAWAY_MIN_INR, minutes * RUNAWAY_INR_PER_MIN)
    log(f"job {job['id']} attempt {job['attempts']}: episode {ep['id']} ({ep['video_id']}), {minutes:g} min, "
        f"runaway limit ₹{limit:.0f}")

    def on_step(i, key, label):
        Q.set_step(conn, job, key, f"{label} ({i} of {len(P.STEPS)})")
        log(f"  step {i}/{len(P.STEPS)} {key}")

    def add_cost(inr):
        total = Q.add_cost(conn, job["id"], inr)
        if total > limit:
            raise RunawayCost(f"RUNAWAY: this run has cost ₹{total:.2f}, over the limit ₹{limit:.0f} "
                              f"({minutes:g} min x ₹{RUNAWAY_INR_PER_MIN:g})")

    try:
        with Heartbeat(job["id"]):
            res = run(ep, on_step=on_step, add_cost=add_cost,
                      save=lambda rows, upd: Q.save_clips(conn, ep["id"], rows, upd), log=log)
        Q.succeed(conn, job)
        log(f"job {job['id']} done: ran {', '.join(res['ran']) or 'nothing (all steps existed)'}")
        return "done"
    except (RunawayCost, P.RE.GuardRefused) as e:     # retrying cannot help; minutes are refunded
        log(f"job {job['id']} stopped: {e}")
        return Q.fail(conn, job, f"{type(e).__name__}: {e}", final=True)
    except Exception as e:
        tb = traceback.format_exc()
        log(f"job {job['id']} error: {type(e).__name__}: {e}")
        return Q.fail(conn, job, tb[-8000:])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.worker")
    ap.add_argument("--once", action="store_true", help="handle at most one job, then exit")
    a = ap.parse_args(argv)
    name = f"{socket.gethostname()}:{os.getpid()}"
    if os.environ.get("KLIPVI_TEST_MODEL"):         # test mode: cheap model + the test key, never for customers
        from .db import ROOT
        from dotenv import dotenv_values
        key = dotenv_values(ROOT / ".env").get("KLIPVI_TEST_GEMINI_API_KEY")
        if not key:
            raise SystemExit("KLIPVI_TEST_MODEL is set but KLIPVI_TEST_GEMINI_API_KEY is not in .env")
        os.environ["GEMINI_API_KEY"] = key
        print(f"TEST MODE: every flow step on {os.environ['KLIPVI_TEST_MODEL']}, test key")
    print(f"worker {name} started")
    with connect() as conn:
        while True:
            n = Q.requeue_stale(conn)
            if n:
                print(f"requeued {n} stale job(s)")
            job = Q.claim(conn, name)
            if job:
                handle(conn, job)
            if a.once:
                return 0
            if not job:
                time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
