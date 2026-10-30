"""Admin-adjustable scoring weights and background re-calculation jobs.

Defaults live in config.STATES; admin overrides are stored per state in the
`scoring_config` table (scope '*' holds the quadrant thresholds). Saving a
change records an audit entry and queues a re-score job that runs on a
background thread, so the HTTP request returns immediately with a job id.
"""
from __future__ import annotations

import json
import threading
import traceback
import uuid

from . import db
from .config import FEATURE_LABELS, STATES, THRESHOLDS
from .db import now_iso

_JOB_LOCK = threading.Lock()   # one recalculation at a time per process


class JobBusy(Exception):
    pass


def _defaults(state: str) -> dict:
    s = STATES[state]
    return {"swing_weights": dict(s["swing_weights"]), "turnout_weights": dict(s["turnout_weights"]),
            "migrant_penalty": s["migrant_penalty"]}


def effective_config(conn) -> dict:
    states = {code: _defaults(code) for code in STATES}
    thresholds, version = dict(THRESHOLDS), 0
    for r in conn.execute("SELECT scope, config, version FROM scoring_config"):
        cfg = json.loads(r["config"])
        version = max(version, r["version"])
        if r["scope"] == "*":
            thresholds.update(cfg)
        elif r["scope"] in states:
            states[r["scope"]].update(cfg)
    return {"states": states, "thresholds": thresholds, "version": version}


def describe(conn) -> dict:
    eff = effective_config(conn)
    meta = {r["scope"]: {"updated_by": r["updated_by"], "updated_at": r["updated_at"], "version": r["version"]}
            for r in conn.execute("SELECT scope, updated_by, updated_at, version FROM scoring_config")}
    return {
        "labels": FEATURE_LABELS,
        "states": {code: {"name": STATES[code]["name"], "current": eff["states"][code], "default": _defaults(code),
                          "customised": code in meta, "updated": meta.get(code)} for code in STATES},
        "thresholds": {"current": eff["thresholds"], "default": dict(THRESHOLDS), "updated": meta.get("*")},
        "version": eff["version"],
    }


def _weights(raw, allowed: set[str], kind: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{kind} must be an object of feature: weight")
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{kind}: unknown features {sorted(unknown)} (allowed: {sorted(allowed)})")
    out = {}
    for k in allowed:
        w = float(raw.get(k, 0))
        if not 0 <= w <= 100:
            raise ValueError(f"{kind}.{k} must be between 0 and 100")
        out[k] = round(w, 4)
    if sum(out.values()) <= 0:
        raise ValueError(f"{kind}: at least one weight must be above zero")
    total = sum(out.values())
    return {k: round(v / total, 4) for k, v in out.items()}  # stored as shares summing to 1


def save_state_config(conn, state: str, body: dict, actor: str) -> dict:
    if state not in STATES:
        raise ValueError(f"unknown state {state}")
    d = _defaults(state)
    cfg = {
        "swing_weights": _weights(body.get("swing_weights", d["swing_weights"]), set(d["swing_weights"]), "swing_weights"),
        "turnout_weights": _weights(body.get("turnout_weights", d["turnout_weights"]), set(d["turnout_weights"]), "turnout_weights"),
        "migrant_penalty": float(body.get("migrant_penalty", d["migrant_penalty"])),
    }
    if not 0 <= cfg["migrant_penalty"] <= 0.95:
        raise ValueError("migrant_penalty must be between 0 and 0.95")
    _upsert(conn, state, cfg, actor)
    return cfg


def save_thresholds(conn, body: dict, actor: str) -> dict:
    t = {**THRESHOLDS, **{k: float(v) for k, v in body.items() if k in THRESHOLDS}}
    if not (30 <= t["swing_high"] <= 90 and 30 <= t["turnout_high"] <= 90):
        raise ValueError("swing_high and turnout_high must be between 30 and 90")
    if not (0 < t["loyal_affinity"] <= 1 and -1 <= t["opp_affinity"] < 0):
        raise ValueError("loyal_affinity must be in (0, 1] and opp_affinity in [-1, 0)")
    _upsert(conn, "*", t, actor)
    return t


def reset(conn, scope: str) -> None:
    conn.execute("DELETE FROM scoring_config WHERE scope = ?", (scope,))


def _upsert(conn, scope: str, cfg: dict, actor: str) -> None:
    nxt = (conn.execute("SELECT COALESCE(MAX(version), 0) FROM scoring_config").fetchone()[0] or 0) + 1
    conn.execute("""INSERT INTO scoring_config (scope, config, version, updated_by, updated_at) VALUES (?,?,?,?,?)
                    ON CONFLICT(scope) DO UPDATE SET config=excluded.config, version=excluded.version,
                    updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                 (scope, json.dumps(cfg), nxt, actor, now_iso()))


# ---------------- background jobs ----------------

def _quadrant_counts(conn, state: str | None) -> dict:
    where, params = ("WHERE v.state = ?", (state,)) if state else ("", ())
    out: dict[str, dict[str, int]] = {}
    for r in conn.execute(f"""SELECT v.state, s.quadrant, COUNT(*) n FROM voter_scores s
                              JOIN voters v USING(voter_id) {where} GROUP BY v.state, s.quadrant""", params):
        out.setdefault(r["state"], {})[r["quadrant"]] = r["n"]
    return out


def job_running(conn) -> str | None:
    r = conn.execute("SELECT job_id FROM scoring_jobs WHERE status IN ('QUEUED','RUNNING') LIMIT 1").fetchone()
    return r["job_id"] if r else None


def start_job(conn, state: str | None, actor: str) -> dict:
    """Create a job row and run it on a daemon thread. Raises JobBusy if one is running."""
    if not _JOB_LOCK.acquire(blocking=False):
        raise JobBusy("A recalculation is already running. Wait for it to finish.")
    try:
        if job_running(conn):
            # stale row from a crashed process: mark failed and carry on
            conn.execute("UPDATE scoring_jobs SET status='FAILED', error='interrupted' WHERE status IN ('QUEUED','RUNNING')")
        job_id = uuid.uuid4().hex[:12]
        conn.execute("INSERT INTO scoring_jobs (job_id, status, scope, requested_by, created_at) VALUES (?,?,?,?,?)",
                     (job_id, "QUEUED", state or "ALL", actor, now_iso()))
        conn.commit()
    except Exception:
        _JOB_LOCK.release()
        raise
    t = threading.Thread(target=_run_job, args=(job_id, state, db.workspace_path()), daemon=True, name=f"rescore-{job_id}")
    t.start()
    return get_job(conn, job_id)


def _run_job(job_id: str, state: str | None, path=None) -> None:
    from .scoring import run_scoring
    c = db.connect(path)                     # the workspace the job was started in
    try:
        c.execute("UPDATE scoring_jobs SET status='RUNNING', started_at=? WHERE job_id=?", (now_iso(), job_id))
        c.commit()
        before = _quadrant_counts(c, state)
        res = run_scoring(c, state)
        after = _quadrant_counts(c, state)
        c.execute("UPDATE scoring_jobs SET status='DONE', finished_at=?, result=? WHERE job_id=?",
                  (now_iso(), json.dumps({**res, "before": before, "after": after}), job_id))
        c.commit()
    except Exception as e:  # pragma: no cover - surfaced through job status
        c.rollback()
        c.execute("UPDATE scoring_jobs SET status='FAILED', finished_at=?, error=? WHERE job_id=?",
                  (now_iso(), f"{e}\n{traceback.format_exc(limit=3)}", job_id))
        c.commit()
    finally:
        c.close()
        _JOB_LOCK.release()


def get_job(conn, job_id: str) -> dict:
    r = conn.execute("SELECT * FROM scoring_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not r:
        raise KeyError(f"job {job_id} not found")
    d = dict(r)
    d["result"] = json.loads(d["result"] or "{}")
    return d
