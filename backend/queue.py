"""The jobs table as a work queue.

One job per episode run. A worker claims the oldest waiting job with ``FOR UPDATE SKIP LOCKED`` (two workers never
get the same job), keeps ``heartbeat_at`` fresh while it runs, and finishes it as done, or failed with a retry:
``attempts < max_attempts`` puts it back to waiting after a back-off; the last attempt fails the episode with a
friendly message. A job whose heartbeat stopped (worker crashed, server restarted) is put back by ``requeue_stale``;
the pipeline then resumes from the first step whose output is missing.

Minutes (founder, 2026-11-11): customers are limited by the minutes they paid for, never by our rupee cost. An
episode HOLDS its minutes (length rounded up to whole minutes) at submit, refused if the workspace has too few free
minutes; they are SPENT when the run finishes, clips found or not (the work was done), and REFUNDED when it fails.
Workspaces on the 'internal' plan (our team) are not counted.
"""
from __future__ import annotations

import json
import math

STALE_AFTER_S = 600                      # no heartbeat for 10 min = the worker died
BACKOFF_S = (120, 600, 1800)             # wait before retry 1, 2, 3...
MSG_RETRYING = "We hit a problem and are trying again."
MSG_FAILED = "We couldn't make clips from this episode. Our team has been told and will look into it."
INTERNAL_PLAN = "internal"


class NotEnoughMinutes(RuntimeError):
    def __init__(self, needs: float, free: float):
        self.needs, self.free = needs, free
        super().__init__(f"This episode is {needs:g} min; you have {max(free, 0):g} min free.")


def charge_minutes(length_s: float) -> float:
    """Minutes an episode costs the customer: its length rounded UP to whole minutes (at least 1)."""
    return float(max(1, math.ceil(float(length_s) / 60.0 - 1e-9)))


def submit(conn, workspace_id, source_url: str, video_id: str, length_s: float, submitted_by=None,
           title: str | None = None) -> dict:
    """A new episode and its job, in one transaction, holding the episode's minutes (``NotEnoughMinutes`` if the
    workspace has too few free minutes; nothing is created then)."""
    m = charge_minutes(length_s)
    with conn.transaction():
        ws = conn.execute("select plan, minutes_left, minutes_held from public.workspaces where id = %s for update",
                          (workspace_id,)).fetchone()
        if ws is None:
            raise ValueError(f"no workspace {workspace_id}")
        counted = ws["plan"] != INTERNAL_PLAN
        if counted:
            free = float(ws["minutes_left"]) - float(ws["minutes_held"])
            if m > free:
                raise NotEnoughMinutes(m, free)
            conn.execute("update public.workspaces set minutes_held = minutes_held + %s where id = %s", (m, workspace_id))
        ep = conn.execute(
            "insert into public.episodes (workspace_id, submitted_by, source_url, video_id, title, length_s, minutes, "
            "minutes_state, step) values (%s, %s, %s, %s, %s, %s, %s, %s, 'Waiting to start') returning *",
            (workspace_id, submitted_by, source_url, video_id, title, round(float(length_s), 2), m,
             "held" if counted else None)).fetchone()
        job = conn.execute("insert into public.jobs (episode_id) values (%s) returning *", (ep["id"],)).fetchone()
    return {"episode": ep, "job": job}


def _settle(conn, episode_id, spend: bool) -> dict:
    """Held minutes -> spent (``spend``) or refunded. Call inside a transaction. No-op unless still held."""
    ep = conn.execute("select workspace_id, minutes, minutes_state from public.episodes where id = %s for update",
                      (episode_id,)).fetchone()
    if ep["minutes_state"] == "held":
        m = ep["minutes"]
        if spend:
            conn.execute("update public.workspaces set minutes_left = minutes_left - %s, minutes_held = minutes_held - %s "
                         "where id = %s", (m, m, ep["workspace_id"]))
        else:
            conn.execute("update public.workspaces set minutes_held = minutes_held - %s where id = %s",
                         (m, ep["workspace_id"]))
        conn.execute("update public.episodes set minutes_state = %s where id = %s",
                     ("spent" if spend else "refunded", episode_id))
    return ep


# Work of the API tests' workspaces (named "pytest: ...") is never claimed: the tests run against the live database
# and a worker running at the same time must not make videos (or paid AI calls) for them.
TEST_WS_PATTERN = "pytest:%"


def claim(conn, worker: str) -> dict | None:
    """The oldest waiting job that is due, now ``running`` for this worker (or None)."""
    with conn.transaction():
        job = conn.execute(
            """update public.jobs
                  set status = 'running', worker = %s, attempts = attempts + 1, started_at = now(),
                      heartbeat_at = now(), error = null
                where id = (select j.id from public.jobs j
                             join public.episodes e on e.id = j.episode_id
                             join public.workspaces w on w.id = e.workspace_id
                             where j.status = 'waiting' and j.run_after <= now() and w.name not like %s
                             order by j.run_after, j.id
                             limit 1
                             for update of j skip locked)
            returning *""", (worker, TEST_WS_PATTERN)).fetchone()
        if job:
            conn.execute("update public.episodes set status = 'running', message = null where id = %s",
                         (job["episode_id"],))
    return job


def heartbeat(conn, job_id: int) -> None:
    conn.execute("update public.jobs set heartbeat_at = now() where id = %s and status = 'running'", (job_id,))


def set_step(conn, job: dict, step: str, label: str) -> None:
    """The step the job is on (internal name) and what the customer sees ("Finding the clips (9 of 11)")."""
    with conn.transaction():
        conn.execute("update public.jobs set step = %s, heartbeat_at = now() where id = %s", (step, job["id"]))
        conn.execute("update public.episodes set step = %s where id = %s", (label, job["episode_id"]))


def add_cost(conn, job_id: int, inr: float) -> float:
    """Add ``inr`` to the job's cost; returns the job's total so far (all attempts)."""
    return float(conn.execute("update public.jobs set cost_inr = cost_inr + %s where id = %s returning cost_inr",
                              (round(max(inr, 0.0), 2), job_id)).fetchone()["cost_inr"])


def succeed(conn, job: dict) -> None:
    """Job done, episode done, its held minutes spent (clips found or not), one usage row."""
    with conn.transaction():
        j = conn.execute("update public.jobs set status = 'done', finished_at = now(), heartbeat_at = now() "
                         "where id = %s returning cost_inr", (job["id"],)).fetchone()
        conn.execute("update public.episodes set status = 'done', step = 'Ready', message = null, finished_at = now() "
                     "where id = %s", (job["episode_id"],))
        ep = _settle(conn, job["episode_id"], spend=True)
        conn.execute(
            "insert into public.usage (workspace_id, episode_id, minutes_charged, cost_inr) values (%s, %s, %s, %s) "
            "on conflict (episode_id) do update set minutes_charged = excluded.minutes_charged, "
            "cost_inr = excluded.cost_inr",
            (ep["workspace_id"], job["episode_id"], ep["minutes"] or 0, j["cost_inr"]))


def fail(conn, job: dict, error: str, final: bool = False) -> str:
    """Record the error; retry later unless ``final`` or out of attempts. Returns 'retry' or 'failed'."""
    with conn.transaction():
        j = conn.execute("select attempts, max_attempts from public.jobs where id = %s", (job["id"],)).fetchone()
        if not j:                       # the episode (and its job) was deleted while it ran: nothing to record
            return "gone"
        if not final and j["attempts"] < j["max_attempts"]:
            wait = BACKOFF_S[min(j["attempts"], len(BACKOFF_S)) - 1]
            conn.execute("update public.jobs set status = 'waiting', error = %s, worker = null, "
                         "run_after = now() + make_interval(secs => %s) where id = %s", (error, wait, job["id"]))
            conn.execute("update public.episodes set message = %s where id = %s", (MSG_RETRYING, job["episode_id"]))
            return "retry"
        conn.execute("update public.jobs set status = 'failed', error = %s, finished_at = now() where id = %s",
                     (error, job["id"]))
        conn.execute("update public.episodes set status = 'failed', message = %s, finished_at = now() where id = %s",
                     (MSG_FAILED, job["episode_id"]))
        _settle(conn, job["episode_id"], spend=False)
        return "failed"


def requeue_stale(conn, stale_after_s: int = STALE_AFTER_S) -> int:
    """Running jobs whose worker stopped sending heartbeats go back to waiting (or fail if out of attempts)."""
    rows = conn.execute("select id, episode_id, attempts, max_attempts from public.jobs where status = 'running' "
                        "and heartbeat_at < now() - make_interval(secs => %s)", (stale_after_s,)).fetchall()
    for r in rows:
        fail(conn, r, "the worker stopped responding (crash or restart); resuming", final=False)
    return len(rows)


def save_clips(conn, episode_id, rows: list[dict], episode_update: dict) -> int:
    """Replace the episode's clips with ``rows`` and set its title / length / verdict, in one transaction."""
    with conn.transaction():
        conn.execute("delete from public.clips where episode_id = %s", (episode_id,))
        for r in rows:
            conn.execute(
                "insert into public.clips (episode_id, number, rank, post_first, title, hook, posting_text, why, "
                "start_s, end_s, length_s, pieces, strength) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (episode_id, r["number"], r["rank"], r["post_first"], r["title"], r["hook"], r["posting_text"],
                 r["why"], r["start_s"], r["end_s"], r["length_s"], json.dumps(r["pieces"], ensure_ascii=False),
                 r["strength"]))
        sets = ", ".join(f"{k} = %s" for k in episode_update)
        if sets:
            conn.execute(f"update public.episodes set {sets} where id = %s", (*episode_update.values(), episode_id))
    return len(rows)


PLAIN = "plain"     # settings_hash of a render without captions


def save_plain_renders(conn, episode_id, rows: list[dict]) -> int:
    """Ready fit/crop videos (no captions) of an episode's clips -> public.renders (one per clip and style)."""
    with conn.transaction():
        ids = {r["number"]: r["id"] for r in conn.execute(
            "select id, number from public.clips where episode_id = %s", (episode_id,)).fetchall()}
        n = 0
        for r in rows:
            if r["number"] not in ids:
                continue
            conn.execute(
                "insert into public.renders (clip_id, style, template_id, settings, settings_hash, status, file, size_mb) "
                "values (%s, %s, null, '{}', %s, 'ready', %s, %s) on conflict (clip_id, style, settings_hash) do update "
                "set status = 'ready', file = excluded.file, size_mb = excluded.size_mb",
                (ids[r["number"]], r["style"], PLAIN, r["file"], r["size_mb"]))
            n += 1
    return n


# ----------------------------------------------------------------------------------------------- caption renders
RENDER_STALE_S = 900


def request_render(conn, clip_id, style: str, template_id, settings: dict, settings_hash: str,
                   captions: dict | None = None) -> dict:
    """The render for (clip, style, settings[, edited words]): the existing one when it was made already (or is being
    made), else a new 'waiting' request. A failed one is asked again."""
    with conn.transaction():
        r = conn.execute(
            "insert into public.renders (clip_id, style, template_id, settings, settings_hash, status, captions) "
            "values (%s, %s, %s, %s, %s, 'waiting', %s) on conflict (clip_id, style, settings_hash) do update "
            "set status = case when public.renders.status = 'failed' then 'waiting' else public.renders.status end, "
            "error = case when public.renders.status = 'failed' then null else public.renders.error end "
            "returning *", (clip_id, style, template_id, json.dumps(settings), settings_hash,
                             json.dumps(captions, ensure_ascii=False) if captions else None)).fetchone()
    return r


def claim_render(conn) -> dict | None:
    with conn.transaction():
        conn.execute("update public.renders set status = 'waiting' where status = 'making' "
                     "and updated_at < now() - make_interval(secs => %s)", (RENDER_STALE_S,))
        return conn.execute(
            """update public.renders set status = 'making'
                where id = (select r.id from public.renders r
                             join public.clips c on c.id = r.clip_id
                             join public.episodes e on e.id = c.episode_id
                             join public.workspaces w on w.id = e.workspace_id
                             where r.status = 'waiting' and w.name not like %s
                             order by r.created_at limit 1 for update of r skip locked)
            returning *""", (TEST_WS_PATTERN,)).fetchone()


def finish_render(conn, render_id, file: str | None, size_mb: float | None, error: str | None = None) -> None:
    conn.execute("update public.renders set status = %s, file = %s, size_mb = %s, error = %s where id = %s",
                 ("failed" if error else "ready", file, size_mb, (error or None) and error[-4000:], render_id))
