"""Klipvi backend (customer product; "Shotvi" stays the internal pipeline name).

  db.py        connection to the Supabase Postgres database (SUPABASE_DB_URL, session pooler)
  queue.py     the jobs table as a work queue: submit, claim (SKIP LOCKED), steps, heartbeat, retry, finish
  pipeline.py  one episode from a YouTube link to clips: the existing prep steps (shotvi.run_episode) and the
               web-series flow (selection_v2.flow_v2), each skipped when its output exists; then the clips are saved
  worker.py    the loop a worker process runs:  python -m backend.worker
  submit.py    add an episode to the queue:      python -m backend.submit <youtube url> [--workspace <id>]
  status.py    what is queued / running / done:  python -m backend.status
"""
