"""SQLite access layer. Swap this module for Postgres/PostGIS later without
touching scoring or routing — they only see plain dicts."""
from __future__ import annotations

import contextvars
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH, SCHEMA_PATH


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Workspaces: "main" holds the campaign's real data; "demo" is a separate, disposable
# SQLite file for pitch presentations. Each request picks one (see main._auth), so a
# pitch never mixes synthetic records into the real workspace.
WORKSPACES = ("main", "demo")
_WS: contextvars.ContextVar[str] = contextvars.ContextVar("ve_workspace", default="main")


def workspace() -> str:
    return _WS.get()


def set_workspace(ws: str) -> None:
    if ws not in WORKSPACES:
        raise ValueError(f"unknown workspace {ws}")
    _WS.set(ws)


def workspace_path(ws: str | None = None):
    ws = ws or _WS.get()
    return DB_PATH if ws == "main" else DB_PATH.with_name(f"{DB_PATH.stem}-pitch-demo{DB_PATH.suffix}")


def connect(path=None) -> sqlite3.Connection:
    p = path or workspace_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session(path=None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


MIGRATIONS = [
    # (table, column, DDL) — additive only, safe to re-run on v1 databases
    ("voters", "consent_status", "ALTER TABLE voters ADD COLUMN consent_status TEXT NOT NULL DEFAULT 'PENDING'"),
    ("voters", "consent_updated_at", "ALTER TABLE voters ADD COLUMN consent_updated_at TEXT"),
    ("voters", "consent_source", "ALTER TABLE voters ADD COLUMN consent_source TEXT"),
]


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    for table, col, ddl in MIGRATIONS:
        if col not in _columns(conn, table):
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_voters_consent ON voters(consent_status)")
    conn.commit()


def reset_all(conn: sqlite3.Connection) -> None:
    """Drops data tables. Audit and consent logs are deliberately kept."""
    for t in ["sync_ops", "route_stops", "routes", "voter_scores", "users", "voters", "households",
              "cadres", "booths", "ingestion_log", "scoring_jobs", "talking_point_cache", "cadre_devices",
              "directives"]:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    init_schema(conn)


def rows(cur) -> list[dict]:
    out = []
    for r in cur.fetchall():
        d = dict(r)
        for k in ("attributes", "drivers"):
            if k in d and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    d[k] = {}
        out.append(d)
    return out
