"""Minutes of a workspace (until billing exists, this is how we give a customer minutes).

    python -m backend.minutes <workspace id>              # show left / on hold / free
    python -m backend.minutes <workspace id> --add 60     # give 60 minutes (negative takes minutes away)
"""
from __future__ import annotations

import argparse

from .db import connect


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.minutes")
    ap.add_argument("workspace")
    ap.add_argument("--add", type=float, default=0.0)
    a = ap.parse_args(argv)
    with connect() as conn:
        if a.add:
            with conn.transaction():
                ws = conn.execute("select minutes_left, minutes_held from public.workspaces where id = %s for update",
                                  (a.workspace,)).fetchone()
                if ws is None:
                    raise SystemExit(f"no workspace {a.workspace}")
                new = float(ws["minutes_left"]) + a.add
                if new < float(ws["minutes_held"]):
                    raise SystemExit(f"cannot go below the {float(ws['minutes_held']):g} min on hold")
                conn.execute("update public.workspaces set minutes_left = %s where id = %s", (new, a.workspace))
        ws = conn.execute("select name, plan, minutes_left, minutes_held from public.workspaces where id = %s",
                          (a.workspace,)).fetchone()
    if ws is None:
        raise SystemExit(f"no workspace {a.workspace}")
    left, held = float(ws["minutes_left"]), float(ws["minutes_held"])
    print(f"{ws['name']} ({ws['plan']}): {left:g} min left, {held:g} on hold, {left - held:g} free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
