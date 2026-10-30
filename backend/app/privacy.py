"""DPDP Act 2023 privacy layer: PII masking, consent state and audit trail.

Masking happens on the server, so masked fields never reach the browser.
"Essential" views (a cadre's own route for today) are exempt from the masking
toggle but still follow consent: phone numbers are only released for voters
whose consent is GRANTED.
"""
from __future__ import annotations

import json

from .config import CONSENT_STATUSES, DEFAULT_SETTINGS
from .db import now_iso

# ---------------- settings ----------------

def get_setting(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS[key]


def set_setting(conn, key: str, value: str, actor: str) -> None:
    if key not in DEFAULT_SETTINGS:
        raise ValueError(f"unknown setting {key}")
    conn.execute("""INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?,?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at""", (key, value, actor, now_iso()))


def masking_on(conn) -> bool:
    return get_setting(conn, "pii_masking") == "on"


# ---------------- masking ----------------

def mask_name(name: str | None) -> str:
    parts = [p for p in (name or "").split() if p]
    return " ".join(f"{p[0]}." for p in parts) if parts else ""


def mask_phone(phone: str | None, full: bool) -> str | None:
    if not phone:
        return None
    return "•••••• ••••" if full else f"••••••{phone[-4:]}"


def mask_address(addr: str | None) -> str:
    """'H.No. 370, Rohta' -> 'H.No. •••, Rohta' (keeps the locality, drops the house number)."""
    if not addr:
        return ""
    head, _, rest = addr.partition(",")
    if any(ch.isdigit() for ch in head):
        return f"H.No. •••,{rest}" if rest else "H.No. •••"
    return addr


def mask_rows(rows: list[dict], masking: bool) -> list[dict]:
    """For non-essential views. Phones never appear in full here; with masking on,
    names and house numbers are reduced too."""
    for r in rows:
        if "phone" in r:
            r["phone"] = mask_phone(r["phone"], full=masking)
        if masking:
            if "name" in r:
                r["name"] = mask_name(r["name"])
            if "address" in r:
                r["address"] = mask_address(r["address"])
        r["pii_masked"] = masking
    return rows


def essential_member_view(m: dict) -> dict:
    """A cadre's own route: full name and address, phone only with consent."""
    if m.get("consent_status") != "GRANTED":
        m["phone"] = None
    return m


# ---------------- consent ----------------

def normalize_consent(value) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    v = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if v in ("granted", "consent_granted", "yes", "y", "true", "1", "given"):
        return "GRANTED"
    if v in ("opt_out", "optout", "opted_out", "withdrawn", "no", "n", "false", "0", "refused"):
        return "OPT_OUT"
    if v in ("pending", "unknown", "na", "not_asked"):
        return "PENDING"
    raise ValueError(f"consent_status must be one of {list(CONSENT_STATUSES)}")


def set_consent(conn, voter_id: str, status: str, actor: str, source: str) -> dict:
    status = normalize_consent(status)
    if status is None:
        raise ValueError("consent_status is required")
    row = conn.execute("SELECT consent_status FROM voters WHERE voter_id = ?", (voter_id,)).fetchone()
    if not row:
        raise KeyError(f"voter {voter_id} not found")
    old, ts = row["consent_status"], now_iso()
    if old != status:
        conn.execute("UPDATE voters SET consent_status = ?, consent_updated_at = ?, consent_source = ? WHERE voter_id = ?",
                     (status, ts, source, voter_id))
        log_consent(conn, voter_id, old, status, source, actor, ts)
        if status == "OPT_OUT":
            enforce_opt_outs(conn, [voter_id])
    return {"voter_id": voter_id, "consent_status": status, "previous": old, "changed": old != status}


def log_consent(conn, voter_id, old, new, source, actor, ts=None) -> None:
    conn.execute("""INSERT INTO consent_log (voter_id, old_status, new_status, source, actor, created_at)
                    VALUES (?,?,?,?,?,?)""", (voter_id, old, new, source, actor, ts or now_iso()))


def enforce_opt_outs(conn, voter_ids: list[str] | None = None) -> int:
    """Stop processing opted-out voters: erase contact data and derived scores."""
    where, params = ("AND voter_id IN (%s)" % ",".join("?" * len(voter_ids)), voter_ids) if voter_ids else ("", [])
    conn.execute(f"UPDATE voters SET phone = NULL WHERE consent_status = 'OPT_OUT' AND phone IS NOT NULL {where}", params)
    cur = conn.execute(f"""DELETE FROM voter_scores WHERE voter_id IN
        (SELECT voter_id FROM voters WHERE consent_status = 'OPT_OUT' {where})""", params)
    return cur.rowcount


def consent_summary(conn, booth_clause="1=1", params=()) -> dict:
    rows = conn.execute(f"""SELECT consent_status s, COUNT(*) n FROM voters v WHERE {booth_clause}
                            GROUP BY consent_status""", params).fetchall()
    out = {s: 0 for s in CONSENT_STATUSES}
    out.update({r["s"]: r["n"] for r in rows})
    return out


# ---------------- audit ----------------

def audit(conn, user: dict | None, action: str, detail: dict | None = None) -> None:
    conn.execute("INSERT INTO audit_log (actor, role, action, detail, created_at) VALUES (?,?,?,?,?)",
                 (user and user.get("user_id"), user and user.get("role"), action, json.dumps(detail or {}), now_iso()))
