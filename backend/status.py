"""What is in the queue:  python -m backend.status [--all]"""
from __future__ import annotations

import argparse

from .db import connect


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.status")
    ap.add_argument("--all", action="store_true", help="include finished episodes older than a day")
    a = ap.parse_args(argv)
    q = """select e.created_at, e.video_id, coalesce(e.title, '') as title, e.status, coalesce(e.step, '') as step,
                  e.verdict, j.attempts, j.cost_inr, (select count(*) from public.clips c where c.episode_id = e.id) as clips,
                  left(coalesce(j.error, ''), 120) as error
             from public.episodes e left join lateral (select * from public.jobs where episode_id = e.id
                                                       order by id desc limit 1) j on true
            where %s or e.status in ('waiting', 'running') or e.created_at > now() - interval '1 day'
            order by e.created_at desc limit 50"""
    with connect() as conn:
        rows = conn.execute(q, (a.all,)).fetchall()
    if not rows:
        print("no episodes")
    for r in rows:
        print(f"{r['created_at']:%d %b %H:%M}  {r['video_id']}  {r['status']:8} {r['step'][:34]:34} clips {r['clips']:>2}  "
              f"₹{float(r['cost_inr'] or 0):.2f}  tries {r['attempts']}  {r['verdict'] or ''}  {r['title'][:40]}")
        if r["error"]:
            print(f"    last error: {r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
