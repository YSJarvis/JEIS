"""Authentication and role-based access control.

- Passwords: Werkzeug scrypt/pbkdf2 hashes.
- Sessions: HS256 JWT, delivered as an HTTP-only SameSite=Lax cookie for the
  web app, or as a Bearer token for native clients that ask for one.
- Every request re-checks the user row, so deactivating a user takes effect
  immediately without waiting for token expiry.
- `Scope` turns a user into the set of states / booths / cadre they may see;
  every data endpoint filters through it.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

import jwt
from flask import g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .config import (AUTH_COOKIE, CSRF_HEADER, DEMO_PASSWORD, ENV, JWT_ALGORITHM, JWT_SECRET,
                     JWT_TTL_HOURS, ROLES, STATES, TIMEZONE)
from .db import now_iso, workspace


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def today_local() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()


# ---------------- tokens ----------------

def issue_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user["user_id"], "role": user["role"], "name": user["display_name"],
        "state": user.get("state"), "constituency": user.get("constituency"), "cadre_id": user.get("cadre_id"),
        "iat": now, "exp": now + timedelta(hours=JWT_TTL_HOURS), "jti": uuid.uuid4().hex,
        "ws": workspace(),            # a session is valid only in the workspace that issued it
    }
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub", "role"]})
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired. Log in again.")
    except jwt.InvalidTokenError:
        raise AuthError("Invalid session. Log in again.")


def _request_token() -> tuple[str | None, str]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), "bearer"
    return request.cookies.get(AUTH_COOKIE), "cookie"


# ---------------- login throttling ----------------

_FAILS: dict[str, list[float]] = defaultdict(list)
MAX_FAILS, WINDOW_S = 5, 300


def check_throttle(key: str) -> None:
    now = time.time()
    _FAILS[key] = [t for t in _FAILS[key] if now - t < WINDOW_S]
    if len(_FAILS[key]) >= MAX_FAILS:
        raise AuthError("Too many failed attempts. Try again in a few minutes.", 429)


def record_failure(key: str) -> None:
    _FAILS[key].append(time.time())


def clear_failures(key: str) -> None:
    _FAILS.pop(key, None)


def authenticate(conn, username: str, password: str) -> dict:
    key = f"{(username or '').lower()}|{request.remote_addr}"
    check_throttle(key)
    row = conn.execute("SELECT * FROM users WHERE lower(username) = lower(?)", (username or "",)).fetchone()
    # Always run a hash check so response time doesn't reveal whether the user exists.
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password or "")
    if not row or not ok or not row["active"]:
        record_failure(key)
        raise AuthError("Username or password is incorrect.")
    clear_failures(key)
    conn.execute("UPDATE users SET last_login_at = ? WHERE user_id = ?", (now_iso(), row["user_id"]))
    conn.commit()
    return dict(row)


_DUMMY_HASH = generate_password_hash("not-a-real-password")


# ---------------- scope ----------------

@dataclass
class Scope:
    user: dict
    states: list[str]
    booth_ids: list[str] | None          # None = every booth in `states`
    cadre_id: str | None = None
    constituency: str | None = None
    _cadre_booths: list[str] = field(default_factory=list)

    @property
    def role(self) -> str:
        return self.user["role"]

    def allows_state(self, state: str) -> bool:
        return state in self.states

    def allows_booth(self, booth_id: str) -> bool:
        return self.booth_ids is None or booth_id in self.booth_ids

    def booth_filter(self, col: str = "v.booth_id", state_col: str = "v.state") -> tuple[str, list]:
        """SQL fragment restricting rows to this scope."""
        if self.booth_ids is None:
            if set(self.states) == set(STATES):
                return "1=1", []
            return f"{state_col} IN ({','.join('?' * len(self.states))})", list(self.states)
        if not self.booth_ids:
            return "0=1", []
        return f"{col} IN ({','.join('?' * len(self.booth_ids))})", list(self.booth_ids)

    def summary(self) -> dict:
        return {"states": self.states, "booth_ids": self.booth_ids, "constituency": self.constituency,
                "cadre_id": self.cadre_id}


def build_scope(conn, user: dict) -> Scope:
    role = user["role"]
    if role == "ADMIN":
        return Scope(user, list(STATES), None)
    if role == "MANAGER":
        booths = [r[0] for r in conn.execute("SELECT booth_id FROM booths WHERE state = ? AND constituency = ? ORDER BY booth_id",
                                             (user["state"], user["constituency"]))]
        return Scope(user, [user["state"]], booths, constituency=user["constituency"])
    cadre = conn.execute("SELECT * FROM cadres WHERE cadre_id = ?", (user["cadre_id"],)).fetchone()
    booths = cadre["booth_ids"].split(",") if cadre else []
    return Scope(user, [user["state"]], booths, cadre_id=user["cadre_id"])


# ---------------- request hooks & decorators ----------------

def load_user(conn) -> None:
    """before_request hook: resolve g.user / g.scope (or leave None)."""
    g.user, g.scope, g.auth_via = None, None, None
    token, via = _request_token()
    if not token:
        return
    claims = decode_token(token)
    if claims.get("ws", "main") != workspace():
        raise AuthError("This session belongs to the other workspace. Log in again.")
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (claims["sub"],)).fetchone()
    if not row or not row["active"]:
        raise AuthError("Account disabled or removed.")
    g.user, g.auth_via = dict(row), via
    g.scope = build_scope(conn, g.user)


def csrf_check() -> None:
    """Cookie-authenticated writes must carry a custom header, which browsers
    only allow cross-origin after a CORS preflight we don't grant."""
    if request.method in ("POST", "PATCH", "PUT", "DELETE") and g.get("auth_via") != "bearer":
        name, value = CSRF_HEADER
        if request.headers.get(name) != value:
            raise AuthError(f"Missing {name} header.", 403)


def require(*roles: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if g.get("user") is None:
                return jsonify({"error": "Log in to continue."}), 401
            if roles and g.user["role"] not in roles:
                return jsonify({"error": "Your role does not have access to this."}), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def public_user(user: dict) -> dict:
    return {k: user.get(k) for k in ("user_id", "username", "display_name", "role", "state", "constituency", "cadre_id")} | \
        {"role_label": ROLES[user["role"]]}


# ---------------- user management ----------------

def create_user(conn, username, password, role, display_name, state=None, constituency=None, cadre_id=None) -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {list(ROLES)}")
    if role == "MANAGER" and not (state and constituency):
        raise ValueError("a manager needs state and constituency")
    if role == "CADRE" and not cadre_id:
        raise ValueError("a cadre user needs cadre_id")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    uid = f"U-{uuid.uuid4().hex[:10]}"
    conn.execute("""INSERT INTO users (user_id, username, password_hash, display_name, role, state, constituency,
                    cadre_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (uid, username, generate_password_hash(password), display_name, role, state, constituency,
                  cadre_id, now_iso()))
    return uid


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in s.lower()).strip("-").replace("--", "-")


def seed_demo_users(conn, force: bool = False) -> dict[str, int]:
    """One strategist, one manager per constituency, one login per cadre.
    Never runs in production, except into the isolated pitch-demo workspace (force=True)."""
    if ENV == "production" and not force:
        return {}
    pw = DEMO_PASSWORD
    hashed = generate_password_hash(pw)   # hash once: thousands of cadres would be slow otherwise
    rows = [("admin", "Central Strategist", "ADMIN", None, None, None)]
    for r in conn.execute("SELECT DISTINCT state, constituency FROM booths ORDER BY state, constituency"):
        rows.append((f"mgr.{_slug(r['constituency'])}", f"{r['constituency']} Manager", "MANAGER",
                     r["state"], r["constituency"], None))
    for c in conn.execute("SELECT cadre_id, name, state FROM cadres ORDER BY cadre_id"):
        rows.append((c["cadre_id"].lower(), c["name"], "CADRE", c["state"], None, c["cadre_id"]))
    ts = now_iso()
    conn.executemany("""INSERT OR IGNORE INTO users (user_id, username, password_hash, display_name, role, state,
                        constituency, cadre_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                     [(f"U-{u}", u, hashed, n, role, st, cons, cad, ts) for u, n, role, st, cons, cad in rows])
    conn.commit()
    return {"users": len(rows)}
