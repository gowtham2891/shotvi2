"""Add an episode to the queue (until the web app exists, this is how an episode is submitted).

    python -m backend.submit <youtube url or id> [--workspace <uuid>] [--plan]

Without --workspace the episode goes to the internal "Klipvi team" workspace (created on first use). --plan only
lists which steps would run (free; reads files), without queueing anything.
"""
from __future__ import annotations

import argparse

from shotvi import run_episode as RE

from . import pipeline as P
from . import queue as Q
from .db import connect

TEAM = "Klipvi team"          # plan 'internal': minutes are not counted


def team_workspace(conn) -> str:
    row = conn.execute("select id from public.workspaces where name = %s and kind = 'team' order by created_at limit 1",
                       (TEAM,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("insert into public.workspaces (name, kind, plan) values (%s, 'team', 'internal') returning id",
                        (TEAM,)).fetchone()["id"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.submit")
    ap.add_argument("source", help="YouTube URL or 11-character video id")
    ap.add_argument("--workspace", default="", help="workspace id (default: the internal 'Klipvi team' workspace)")
    ap.add_argument("--plan", action="store_true", help="only show which steps would run; queue nothing")
    a = ap.parse_args(argv)
    vid = RE.video_id_of(a.source)
    url = a.source if a.source.startswith("http") else f"https://www.youtube.com/watch?v={vid}"
    todo = P.pending(vid, url)
    paid = [k for k in todo if k in P.PAID]
    print(f"{vid}: steps to run: {', '.join(todo)}")
    print(f"  paid steps: {', '.join(paid) if paid else 'none (everything exists; this run is free)'}")
    if a.plan:
        return 0
    secs = P.source_length_s(vid, url)
    print(f"  length {secs / 60:.1f} min -> charged {Q.charge_minutes(secs):g} min")
    with connect() as conn:
        ws = a.workspace or team_workspace(conn)
        try:
            out = Q.submit(conn, ws, url, vid, secs)
        except Q.NotEnoughMinutes as e:
            print(f"refused: {e}")
            return 2
    print(f"queued: episode {out['episode']['id']}, job {out['job']['id']} (workspace {ws})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
