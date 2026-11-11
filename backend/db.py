"""Connection to the Klipvi database (Supabase Postgres). The worker uses the secret connection string, which
bypasses row-level security: workers and the API are the only writers."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]


def db_url() -> str:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        raise RuntimeError("SUPABASE_DB_URL is not set (put the Session pooler connection string in .env)")
    return url


def connect(autocommit: bool = True) -> psycopg.Connection:
    return psycopg.connect(db_url(), autocommit=autocommit, row_factory=dict_row, connect_timeout=15)
