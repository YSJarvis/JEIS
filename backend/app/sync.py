"""Offline sync for the cadre app.

The client queues every field action with a client-generated `op_id` and its
own timestamp, then posts the queue here. Guarantees:

- Idempotent: an op_id is applied at most once (sync_ops ledger), so retries
  after a dropped connection are safe.
- Scoped: a cadre can only touch stops on their own routes and voters in
  households on those routes.
- Last-write-wins: if the server already holds a newer update for a stop
  (e.g. from the same cadre's second phone), the older op is reported `stale`
  and not applied.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .db import now_iso
from .privacy import set_consent

STATUSES = {"PENDING", "VISITED", "NOT_HOME", "REFUSED", "REVISIT"}
MAX_OPS = 500


def _ts(v) -> str:
    """Normalise a client timestamp (ISO or epoch ms) to UTC ISO seconds."""
    if isinstance(v, (int, float)):
        dt = datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    else:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    # never trust a client clock that is ahead of the server
    return min(dt.astimezone(timezone.utc), datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _stop_for_cadre(conn, cadre_id: str, stop_id, household_id):
    row = None
    if stop_id is not None:
        row = conn.execute("""SELECT rs.* FROM route_stops rs JOIN routes r USING(route_id)
                              WHERE rs.stop_id = ? AND r.cadre_id = ?""", (stop_id, cadre_id)).fetchone()
    if row is None and household_id:
        # stop was re-planned since the client cached it: fall back to the same door on a current route
        row = conn.execute("""SELECT rs.* FROM route_stops rs JOIN routes r USING(route_id)
                              WHERE rs.household_id = ? AND r.cadre_id = ?
                              ORDER BY r.route_date DESC LIMIT 1""", (household_id, cadre_id)).fetchone()
    return row


def apply_op(conn, user: dict, op: dict) -> dict:
    cadre_id = user["cadre_id"]
    kind = op.get("type")
    client_ts = _ts(op.get("client_ts") or now_iso())

    if kind == "stop_status":
        status = str(op.get("status", "")).upper()
        if status not in STATUSES:
            return {"result": "rejected", "reason": f"unknown status {status}"}
        stop = _stop_for_cadre(conn, cadre_id, op.get("stop_id"), op.get("household_id"))
        if stop is None:
            return {"result": "rejected", "reason": "stop is not on your route"}
        if stop["updated_at"] and stop["updated_at"] > client_ts:
            return {"result": "stale", "reason": "a newer update is already saved", "stop_id": stop["stop_id"],
                    "server": {"status": stop["status"], "notes": stop["notes"], "updated_at": stop["updated_at"]}}
        notes = op.get("notes")
        notes = str(notes)[:500] if notes is not None else None
        conn.execute("UPDATE route_stops SET status = ?, notes = COALESCE(?, notes), updated_at = ? WHERE stop_id = ?",
                     (status, notes, client_ts, stop["stop_id"]))
        return {"result": "applied", "stop_id": stop["stop_id"]}

    if kind == "consent":
        voter_id = op.get("voter_id")
        ok = conn.execute("""SELECT 1 FROM voters v JOIN route_stops rs ON rs.household_id = v.household_id
                             JOIN routes r USING(route_id) WHERE v.voter_id = ? AND r.cadre_id = ? LIMIT 1""",
                          (voter_id, cadre_id)).fetchone()
        if not ok:
            return {"result": "rejected", "reason": "voter is not on your route"}
        try:
            res = set_consent(conn, voter_id, op.get("status"), user["user_id"], "field")
        except ValueError as e:
            return {"result": "rejected", "reason": str(e)}
        return {"result": "applied", "voter_id": voter_id, "consent_status": res["consent_status"]}

    return {"result": "rejected", "reason": f"unknown op type {kind!r}"}


def sync(conn, user: dict, ops: list[dict]) -> dict:
    if not isinstance(ops, list):
        raise ValueError("ops must be a list")
    if len(ops) > MAX_OPS:
        raise ValueError(f"send at most {MAX_OPS} ops per request")
    results = []
    for op in ops:
        op_id = str(op.get("op_id") or "")
        if not op_id or len(op_id) > 64:
            results.append({"op_id": op_id, "result": "rejected", "reason": "op_id is required"}); continue
        prev = conn.execute("SELECT result, cadre_id FROM sync_ops WHERE op_id = ?", (op_id,)).fetchone()
        if prev:
            if prev["cadre_id"] != user["cadre_id"]:
                results.append({"op_id": op_id, "result": "rejected", "reason": "op_id already used"}); continue
            results.append({"op_id": op_id, **json.loads(prev["result"]), "duplicate": True}); continue
        try:
            res = apply_op(conn, user, op)
        except (ValueError, TypeError) as e:
            res = {"result": "rejected", "reason": str(e)}
        conn.execute("INSERT INTO sync_ops (op_id, cadre_id, op_type, payload, result, received_at) VALUES (?,?,?,?,?,?)",
                     (op_id, user["cadre_id"], str(op.get("type")), json.dumps(op)[:2000], json.dumps(res), now_iso()))
        results.append({"op_id": op_id, **res})
    conn.commit()
    summary = {k: sum(r["result"] == k for r in results) for k in ("applied", "stale", "rejected")}
    return {"results": results, "summary": summary, "server_time": now_iso()}
