"""VoterEngine AI — REST API (Flask).

Run:  python -m app.main            (serves http://localhost:8000)

Every endpoint except /api/health and /api/auth/login requires a session.
Access by role:
  ADMIN    all states; imports, scoring weights, privacy settings, audit log
  MANAGER  booths of their constituency; booth ranking, voters, cadre assignment
  CADRE    their own route for today, offline sync, consent capture at the door
The service layer (generator, scoring, routing, ingestion, sync) has no Flask
dependency, so a FastAPI port only rewrites this file.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, make_response, request

from . import auth, db, privacy, scoring_config
from .auth import AuthError, require, today_local
from .config import (AUTH_COOKIE, CONSENT_POLICY, CORS_ORIGIN, COOKIE_SECURE, CSRF_HEADER, ENV, JWT_TTL_HOURS,
                     QUADRANTS, STATES)
from .generator import seed_database
from .ingestion import ingest_voters, parse_csv
from .routing import build_clusters, generate_route, get_route, household_priorities
from .scoring import run_scoring
from .sync import sync as run_sync
from . import electoral_roll, knowledge, ops, talking_points
from .copilot import export as cp_export
from .copilot import generate as cp_gen
from .copilot import kb as cp_kb
from .warroom import counter as wr_counter
from .warroom import engine as wr
from .warroom import export as wr_export
from .warroom import seed as wr_seed
from .suite import briefing as suite_briefing
from .suite import command as suite_command
from .suite import pitch as suite_pitch
from .llm import provider_status

app = Flask(__name__)
app.json.sort_keys = False        # keep state / consent / feature order as defined in config
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024   # 50 MB imports
PUBLIC = {"health", "login", "logout", "demo_status", "demo_exit","setup"}
DEMO_COOKIE, MAIN_TOKEN_COOKIE = "ve_demo", "ve_token_main"
_PITCH_LOCK = threading.Lock()


def conn():
    if "conn" not in g:
        g.conn = db.connect()
    return g.conn


# ---------------- request lifecycle ----------------

@app.before_request
def _auth():
    # Pick the workspace first: the pitch demo is a separate database chosen per browser session.
    ws = "demo" if (request.cookies.get(DEMO_COOKIE) == "1" and suite_pitch.demo_allowed()
                    and db.workspace_path("demo").exists()) else "main"
    db.set_workspace(ws)
    g.workspace = ws
    if request.method == "OPTIONS":
        return None
    try:
        auth.load_user(conn())
    except AuthError as e:
        if request.endpoint not in PUBLIC:
            return _clear_cookie(jsonify({"error": str(e)})), e.status
        g.user = g.scope = g.auth_via = None
    auth.csrf_check()


@app.teardown_appcontext
def _close(_exc):
    c = g.pop("conn", None)
    if c is not None:
        c.close()


@app.after_request
def _headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Headers"] = f"Content-Type, Authorization, {CSRF_HEADER[0]}"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    resp.headers["Vary"] = "Origin"
    resp.headers["Cache-Control"] = "no-store"          # responses carry PII: never cache
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.errorhandler(AuthError)
def _auth_error(e):
    return jsonify({"error": str(e)}), e.status


@app.errorhandler(KeyError)
def _not_found(e):
    return jsonify({"error": str(e).strip("'\"")}), 404


@app.errorhandler(ValueError)
def _bad(e):
    return jsonify({"error": str(e)}), 400


@app.errorhandler(scoring_config.JobBusy)
def _busy(e):
    return jsonify({"error": str(e)}), 409


def forbid(msg="This is outside your assigned area."):
    raise AuthError(msg, 403)


def scoped_where(alias="v") -> tuple[str, list]:
    """WHERE clause from ?state/?booth_id, clamped to the caller's scope."""
    sc = g.scope
    state, booth = request.args.get("state"), request.args.get("booth_id")
    clauses, params = [], []
    if state:
        if not sc.allows_state(state):
            forbid()
        clauses.append(f"{alias}.state = ?"); params.append(state)
    if booth:
        if not sc.allows_booth(booth):
            forbid()
        clauses.append(f"{alias}.booth_id = ?"); params.append(booth)
    sql, p = sc.booth_filter(f"{alias}.booth_id", f"{alias}.state")
    clauses.append(sql); params += p
    return "WHERE " + " AND ".join(clauses), params


def masking() -> bool:
    return privacy.masking_on(conn())


# ---------------- auth ----------------

def _clear_cookie(resp):
    resp.delete_cookie(AUTH_COOKIE, path="/", samesite="None", secure=True)
    return resp

@app.get("/api/health")
def health():
    return {"status": "ok"}
  
@app.get("/api/setup")
def run_setup():
    with db.session() as c:
        mock_res = seed_database(c)
        user_res = auth.seed_demo_users(c, force=True)
        run_scoring(c)
        knowledge.seed_knowledge(c)
        cp_kb.seed_corpus(c)
        wr_seed.seed_feed(c)
    return {"status": "seeded", "users": user_res, "mock": mock_res}


@app.post("/api/auth/login")
def login():
    body = request.get_json(force=True, silent=True) or {}
    c = conn()
    try:
        user = auth.authenticate(c, str(body.get("username", "")), str(body.get("password", "")))
    except AuthError as e:
        privacy.audit(c, None, "login_failed", {"username": str(body.get("username", ""))[:64], "ip": request.remote_addr})
        c.commit()
        raise e
    token = auth.issue_token(user)
    scope = auth.build_scope(c, user)
    privacy.audit(c, user, "login", {"ip": request.remote_addr})
    c.commit()
    expires = datetime.now(timezone.utc) + timedelta(hours=JWT_TTL_HOURS)
    payload = {"user": auth.public_user(user), "scope": scope.summary(), "expires_at": expires.isoformat(timespec="seconds"),
               "workspace": g.workspace}
    if body.get("issue_bearer"):          # native clients; the web app relies on the HTTP-only cookie
        payload["token"] = token
    resp = make_response(jsonify(payload))
    resp.set_cookie(AUTH_COOKIE, token, max_age=JWT_TTL_HOURS * 3600, httponly=True, secure=True,
                samesite="None", path="/")
    return resp


@app.post("/api/auth/logout")
def logout():
    if g.get("user"):
        privacy.audit(conn(), g.user, "logout"); conn().commit()
    resp = _clear_cookie(make_response(jsonify({"ok": True})))
    resp.delete_cookie(MAIN_TOKEN_COOKIE, path="/", samesite="Lax")    # never leave a strategist session behind
    return resp


@app.get("/api/auth/me")
@require()
def me():
    return {"user": auth.public_user(g.user), "scope": g.scope.summary(), "today": today_local(), "workspace": g.workspace}


# ---------------- meta ----------------

@app.get("/api/meta")
@require()
def meta():
    sc = g.scope
    counts = {r["state"]: r["n"] for r in conn().execute(
        "SELECT state, COUNT(*) n FROM voters WHERE consent_status != 'OPT_OUT' GROUP BY state")}
    eff = scoring_config.effective_config(conn())
    return {"states": [{"code": k, "name": v["name"], "center": v["center"], "voters": counts.get(k, 0)}
                       for k, v in STATES.items() if sc.allows_state(k)],
            "quadrants": QUADRANTS, "thresholds": eff["thresholds"], "role": sc.role,
            "constituency": sc.constituency, "pii_masking": masking()}


@app.get("/api/booths")
@require("ADMIN", "MANAGER")
def booths():
    where, params = scoped_where("b")
    return jsonify(db.rows(conn().execute(f"SELECT * FROM booths b {where} ORDER BY priority_score DESC", params)))


# ---------------- dashboard ----------------

@app.get("/api/dashboard")
@require("ADMIN", "MANAGER")
def dashboard():
    where, params = scoped_where("v")
    c = conn()
    base = f"FROM voters v JOIN voter_scores s ON s.voter_id = v.voter_id {where}"
    tot = dict(c.execute(f"""SELECT COUNT(*) voters, COUNT(DISTINCT v.household_id) households,
        COUNT(DISTINCT v.booth_id) booths, ROUND(AVG(s.swing_score),1) avg_swing,
        ROUND(AVG(s.turnout_score),1) avg_turnout, SUM(v.is_migrant) migrants,
        SUM(v.scheme_beneficiary) beneficiaries {base}""", params).fetchone())
    quads = {r["quadrant"]: r["n"] for r in c.execute(f"SELECT s.quadrant, COUNT(*) n {base} GROUP BY s.quadrant", params)}
    scatter = db.rows(c.execute(f"""SELECT s.swing_score x, s.turnout_score y, s.quadrant q {base}
        ORDER BY (abs(random()) % 1000) LIMIT 700""", params))
    segments = db.rows(c.execute(f"""SELECT v.community_group AS segment, COUNT(*) voters,
        ROUND(AVG(s.swing_score),1) avg_swing, ROUND(AVG(s.turnout_score),1) avg_turnout,
        SUM(s.quadrant='PERSUADE') persuade, SUM(s.quadrant='GOTV') gotv {base}
        GROUP BY v.community_group ORDER BY voters DESC""", params))
    schemes = db.rows(c.execute(f"""SELECT CASE WHEN v.scheme_beneficiary=1 THEN 'Beneficiary' ELSE 'Non-beneficiary' END segment,
        COUNT(*) voters, ROUND(AVG(s.swing_score),1) avg_swing, ROUND(AVG(s.turnout_score),1) avg_turnout,
        SUM(s.quadrant='PERSUADE') persuade, SUM(s.quadrant='GOTV') gotv {base} GROUP BY 1""", params))
    bwhere = where.replace("v.", "b.")
    booth_rows = db.rows(c.execute(f"""SELECT b.booth_id, b.name, b.constituency, b.total_voters,
        b.historical_turnout, b.last_margin_pct, b.priority_score, b.lat, b.lng,
        (SELECT COUNT(*) FROM voters v JOIN voter_scores s USING(voter_id) WHERE v.booth_id=b.booth_id AND s.quadrant='PERSUADE') persuade,
        (SELECT COUNT(*) FROM voters v JOIN voter_scores s USING(voter_id) WHERE v.booth_id=b.booth_id AND s.quadrant='GOTV') gotv
        FROM booths b {bwhere} ORDER BY b.priority_score DESC""", params))
    visits = dict(c.execute(f"""SELECT COUNT(*) planned, COALESCE(SUM(rs.status!='PENDING'),0) done,
        COALESCE(SUM(rs.status='VISITED'),0) visited FROM route_stops rs JOIN households v ON v.household_id = rs.household_id
        {where}""", params).fetchone())
    consent = privacy.consent_summary(c, where[6:], params)
    return {"totals": tot, "quadrants": quads, "scatter": scatter, "segments": segments,
            "schemes": schemes, "booths": booth_rows, "field": visits, "consent": consent}


@app.get("/api/clusters")
@require("ADMIN", "MANAGER")
def clusters():
    sc = g.scope
    state, booth = request.args.get("state"), request.args.get("booth_id")
    if (state and not sc.allows_state(state)) or (booth and not sc.allows_booth(booth)):
        forbid()
    booth_ids = [booth] if booth else sc.booth_ids
    hh = household_priorities(conn(), booth_ids=booth_ids, state=state)
    cl = build_clusters(hh, target_size=int(request.args.get("size", 25)))
    for c in cl:   # household ids only: no PII on the map
        c["points"] = [{"household_id": m["household_id"], "lat": m["lat"], "lng": m["lng"],
                        "priority": m["priority"], "q": m["primary_quadrant"]} for m in c.pop("members")]
    return jsonify(cl)


# ---------------- voters ----------------

@app.get("/api/voters")
@require("ADMIN", "MANAGER")
def voters():
    where, params = scoped_where("v")
    mask_on = masking()
    if q := request.args.get("quadrant"):
        where += " AND s.quadrant = ?"; params.append(q)
    if cs := request.args.get("consent"):
        where += " AND v.consent_status = ?"; params.append(cs)
    if term := request.args.get("q"):
        # with masking on, name search is disabled: it would confirm who is on the roll
        if mask_on:
            where += " AND v.voter_id LIKE ?"; params.append(f"%{term}%")
        else:
            where += " AND (v.name LIKE ? OR v.voter_id LIKE ?)"; params += [f"%{term}%"] * 2
    limit, offset = min(int(request.args.get("limit", 50)), 500), int(request.args.get("offset", 0))
    c = conn()
    total = c.execute(f"SELECT COUNT(*) FROM voters v JOIN voter_scores s USING(voter_id) {where}", params).fetchone()[0]
    rs = db.rows(c.execute(f"""SELECT v.voter_id, v.name, v.age, v.gender, v.booth_id, v.household_id,
        v.community_group, v.scheme_types, v.is_migrant, v.phone, v.consent_status, s.swing_score, s.turnout_score,
        s.quadrant, s.priority, h.address FROM voters v JOIN voter_scores s USING(voter_id)
        JOIN households h ON h.household_id = v.household_id {where}
        ORDER BY s.priority DESC LIMIT ? OFFSET ?""", params + [limit, offset]))
    return {"total": total, "items": privacy.mask_rows(rs, mask_on), "pii_masked": mask_on}


def _voter_in_scope(voter_id: str) -> dict:
    r = conn().execute("SELECT booth_id, state FROM voters WHERE voter_id = ?", (voter_id,)).fetchone()
    if not r:
        raise KeyError(f"voter {voter_id} not found")
    if not g.scope.allows_booth(r["booth_id"]) or not g.scope.allows_state(r["state"]):
        forbid()
    return dict(r)


@app.get("/api/voters/<voter_id>")
@require("ADMIN", "MANAGER")
def voter(voter_id):
    _voter_in_scope(voter_id)
    r = db.rows(conn().execute("""SELECT v.*, h.address, s.swing_score, s.turnout_score, s.quadrant, s.priority,
        s.drivers, s.model_version FROM voters v JOIN households h ON h.household_id = v.household_id
        LEFT JOIN voter_scores s USING(voter_id) WHERE v.voter_id = ?""", (voter_id,)))
    out = privacy.mask_rows(r, masking())[0]
    out["consent_log"] = db.rows(conn().execute(
        "SELECT old_status, new_status, source, created_at FROM consent_log WHERE voter_id = ? ORDER BY id DESC LIMIT 20",
        (voter_id,)))
    return out


@app.patch("/api/voters/<voter_id>/consent")
@require("ADMIN", "MANAGER")
def voter_consent(voter_id):
    _voter_in_scope(voter_id)
    body = request.get_json(force=True, silent=True) or {}
    res = privacy.set_consent(conn(), voter_id, body.get("consent_status"), g.user["user_id"], "admin")
    conn().commit()
    return res


# ---------------- cadres (manager / admin) ----------------

def _cadre_in_scope(cadre_id: str) -> dict:
    row = conn().execute("SELECT * FROM cadres WHERE cadre_id = ?", (cadre_id,)).fetchone()
    if not row:
        raise KeyError(f"cadre {cadre_id} not found")
    booths = row["booth_ids"].split(",")
    if not g.scope.allows_state(row["state"]) or not all(g.scope.allows_booth(b) for b in booths):
        forbid()
    return dict(row)


@app.get("/api/cadres")
@require("ADMIN", "MANAGER")
def cadres():
    sc, today = g.scope, today_local()
    state = request.args.get("state")
    out = []
    for r in db.rows(conn().execute("""SELECT c.*, u.username, r.route_id, r.total_km,
            (SELECT COUNT(*) FROM route_stops rs WHERE rs.route_id = r.route_id) planned,
            (SELECT COUNT(*) FROM route_stops rs WHERE rs.route_id = r.route_id AND rs.status != 'PENDING') done
            FROM cadres c LEFT JOIN users u ON u.cadre_id = c.cadre_id
            LEFT JOIN routes r ON r.cadre_id = c.cadre_id AND r.route_date = ?
            ORDER BY c.cadre_id""", (today,))):
        booths = r["booth_ids"].split(",")
        if not sc.allows_state(r["state"]) or (state and r["state"] != state):
            continue
        if not all(sc.allows_booth(b) for b in booths):
            continue
        r["booth_ids"] = booths
        r["phone"] = privacy.mask_phone(r["phone"], full=False)
        out.append(r)
    return jsonify(out)


@app.patch("/api/cadres/<cadre_id>")
@require("ADMIN", "MANAGER")
def assign_cadre(cadre_id):
    cadre = _cadre_in_scope(cadre_id)
    body = request.get_json(force=True, silent=True) or {}
    booth_ids = body.get("booth_ids")
    if not isinstance(booth_ids, list) or not booth_ids:
        raise ValueError("booth_ids must be a non-empty list")
    c = conn()
    for b in booth_ids:
        row = c.execute("SELECT state FROM booths WHERE booth_id = ?", (b,)).fetchone()
        if not row:
            raise KeyError(f"booth {b} not found")
        if row["state"] != cadre["state"]:
            raise ValueError(f"booth {b} is in another state")
        if not g.scope.allows_booth(b):
            forbid(f"Booth {b} is outside your constituency.")
    c.execute("UPDATE cadres SET booth_ids = ? WHERE cadre_id = ?", (",".join(dict.fromkeys(booth_ids)), cadre_id))
    privacy.audit(c, g.user, "cadre_assigned", {"cadre_id": cadre_id, "from": cadre["booth_ids"], "to": booth_ids})
    c.commit()
    return {**_cadre_in_scope(cadre_id), "booth_ids": booth_ids}


def enrich(route: dict, essential: bool) -> dict:
    """Attach household members (scored voters only — opted-out voters are
    never returned), today's directives and baseline talking points to each
    stop. Baseline points come from the offline generator (directives +
    knowledge base + rules), so they work without a network; the field app
    asks /api/cadre/generate-talking-points for AI-tailored ones when online."""
    c, mask_on = conn(), masking()
    date = route.get("route_date") or today_local()
    places: dict[tuple, tuple] = {}
    for stop in route.get("stops", []):
        state, const = talking_points.place_of(c, stop["household_id"])
        if (state, const) not in places:
            places[(state, const)] = (knowledge.list_docs(c, state, const), knowledge.directives_for(c, date, state, const))
        docs, dirs = places[(state, const)]
        tp = talking_points.generate(c, stop["household_id"], date, allow_live=False, docs=docs, directives=dirs)
        stop["talking_points"] = tp["points"]
        stop["sources"] = tp["sources"]
        members = db.rows(c.execute("""SELECT v.voter_id, v.name, v.age, v.gender, v.community_group,
            v.scheme_beneficiary, v.scheme_types, v.is_migrant, v.phone, v.attributes, v.state, v.consent_status,
            s.swing_score, s.turnout_score, s.quadrant, s.priority, s.drivers
            FROM voters v JOIN voter_scores s USING(voter_id) WHERE v.household_id = ?
            ORDER BY s.priority DESC""", (stop["household_id"],)))
        if essential:
            stop["members"] = [privacy.essential_member_view(m) for m in members]
        else:
            stop["members"] = privacy.mask_rows(members, mask_on)
            if mask_on:
                stop["address"] = privacy.mask_address(stop["address"])
    return route


@app.get("/api/cadres/<cadre_id>/route")
@require("ADMIN", "MANAGER")
def cadre_route(cadre_id):
    _cadre_in_scope(cadre_id)
    d = request.args.get("date") or today_local()
    r = conn().execute("SELECT route_id FROM routes WHERE cadre_id = ? AND route_date = ?", (cadre_id, d)).fetchone()
    if not r:
        return {"route_id": None, "cadre_id": cadre_id, "route_date": d, "stops": []}
    return enrich(get_route(conn(), r["route_id"]), essential=False)


@app.post("/api/routes/generate")
@require("ADMIN", "MANAGER")
def routes_generate():
    body = request.get_json(force=True, silent=True) or {}
    if not body.get("cadre_id"):
        raise ValueError("cadre_id is required")
    _cadre_in_scope(body["cadre_id"])
    r = generate_route(conn(), body["cadre_id"], today_local(), int(body.get("max_stops", 20)),
                       bool(body.get("regenerate", False)))
    return enrich(r, essential=False) if r.get("route_id") else r


# ---------------- cadre (field app) ----------------

def _my_route(include_empty=True) -> dict:
    cadre_id, d = g.user["cadre_id"], today_local()
    r = conn().execute("SELECT route_id FROM routes WHERE cadre_id = ? AND route_date = ?", (cadre_id, d)).fetchone()
    cadre = dict(conn().execute("SELECT cadre_id, name, booth_ids, state FROM cadres WHERE cadre_id = ?", (cadre_id,)).fetchone())
    if not r:
        return {"route_id": None, "cadre_id": cadre_id, "route_date": d, "stops": [], "cadre": cadre}
    route = enrich(get_route(conn(), r["route_id"]), essential=True)
    route["cadre"] = cadre
    st = conn().execute("SELECT constituency FROM booths WHERE booth_id = ?", (cadre["booth_ids"].split(",")[0],)).fetchone()
    route["directives"] = [{"id": x["directive_id"], "text": x["text"], "author": x.get("author")}
                           for x in knowledge.directives_for(conn(), d, cadre["state"], st["constituency"] if st else None)]
    return route


@app.get("/api/cadre/route")
@require("CADRE")
def my_route():
    ops.touch_device(conn(), g.user["cadre_id"], synced=True,
                     queue_remaining=int(request.args.get("queued", 0) or 0), app_version=request.headers.get("X-App-Version"))
    conn().commit()
    return _my_route()


@app.post("/api/cadre/route")
@require("CADRE")
def my_route_build():
    body = request.get_json(force=True, silent=True) or {}
    n = max(5, min(int(body.get("max_stops", 20)), 40))
    generate_route(conn(), g.user["cadre_id"], today_local(), n, bool(body.get("regenerate", False)))
    ops.touch_device(conn(), g.user["cadre_id"], synced=True)
    conn().commit()
    return _my_route()


@app.post("/api/cadre/sync")
@require("CADRE")
def cadre_sync():
    body = request.get_json(force=True, silent=True) or {}
    res = run_sync(conn(), g.user, body.get("ops", []))
    client = body.get("client") or {}
    ops.touch_device(conn(), g.user["cadre_id"], synced=True, queue_remaining=int(client.get("remaining", 0) or 0),
                     app_version=str(client.get("app_version") or "")[:20] or None)
    conn().commit()
    if body.get("include_route", True):
        res["route"] = _my_route()
    return res


@app.patch("/api/stops/<int:stop_id>")
@require("CADRE")
def update_stop(stop_id):
    """Online single update; goes through the same path as offline sync."""
    body = request.get_json(force=True, silent=True) or {}
    op = {"op_id": body.get("op_id") or f"srv-{uuid.uuid4().hex}", "type": "stop_status", "stop_id": stop_id,
          "status": body.get("status"), "notes": body.get("notes")}
    res = run_sync(conn(), g.user, [op])["results"][0]
    if res["result"] == "rejected":
        raise ValueError(res["reason"])
    return res


# ---------------- admin: scoring ----------------

@app.post("/api/scoring/run")
@require("ADMIN")
def scoring_run():
    return run_scoring(conn(), request.args.get("state"))


@app.get("/api/scoring/config")
@require("ADMIN")
def scoring_config_get():
    return scoring_config.describe(conn())


@app.post("/api/scoring/recalibrate")
@require("ADMIN")
def scoring_recalibrate():
    """Save weight changes (optional) and queue a background re-score.
    Body: {state?, swing_weights?, turnout_weights?, migrant_penalty?, thresholds?, reset?}"""
    body = request.get_json(force=True, silent=True) or {}
    c, actor = conn(), g.user["user_id"]
    state = body.get("state")
    if state and state not in STATES:
        raise ValueError(f"unknown state {state}")
    changes = {}
    if state and body.get("reset"):
        scoring_config.reset(c, state); changes["reset"] = state
    elif state and any(k in body for k in ("swing_weights", "turnout_weights", "migrant_penalty")):
        changes[state] = scoring_config.save_state_config(c, state, body, actor)
    if "thresholds" in body:
        changes["thresholds"] = scoring_config.save_thresholds(c, body["thresholds"] or {}, actor)
    if changes:
        privacy.audit(c, g.user, "scoring_config_changed", changes)
    c.commit()
    job_scope = None if ("thresholds" in body or not state) else state
    job = scoring_config.start_job(c, job_scope, actor)
    return {"job": job, "changes": changes, "config": scoring_config.describe(c)}, 202


@app.get("/api/scoring/jobs/<job_id>")
@require("ADMIN")
def scoring_job(job_id):
    return scoring_config.get_job(conn(), job_id)


# ---------------- admin: privacy ----------------

@app.get("/api/privacy")
@require("ADMIN")
def privacy_get():
    c = conn()
    s = c.execute("SELECT updated_by, updated_at FROM settings WHERE key = 'pii_masking'").fetchone()
    return {"pii_masking": privacy.masking_on(c), "updated": dict(s) if s else None,
            "consent": privacy.consent_summary(c), "policy": CONSENT_POLICY,
            "recent_consent_changes": db.rows(c.execute(
                """SELECT l.voter_id, l.old_status, l.new_status, l.source, l.created_at, u.display_name actor
                   FROM consent_log l LEFT JOIN users u ON u.user_id = l.actor ORDER BY l.id DESC LIMIT 25"""))}


@app.patch("/api/privacy")
@require("ADMIN")
def privacy_set():
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body.get("pii_masking"), bool):
        raise ValueError("pii_masking must be true or false")
    c = conn()
    privacy.set_setting(c, "pii_masking", "on" if body["pii_masking"] else "off", g.user["user_id"])
    privacy.audit(c, g.user, "pii_masking_changed", {"pii_masking": body["pii_masking"]})
    c.commit()
    return privacy_get()


@app.get("/api/admin/audit")
@require("ADMIN")
def audit_log():
    limit = min(int(request.args.get("limit", 50)), 500)
    return jsonify(db.rows(conn().execute(
        """SELECT a.id, a.action, a.detail, a.created_at, a.role, u.display_name actor FROM audit_log a
           LEFT JOIN users u ON u.user_id = a.actor ORDER BY a.id DESC LIMIT ?""", (limit,))))


# ---------------- admin: ingestion ----------------

@app.post("/api/ingest/csv")
@require("ADMIN")
def ingest_csv():
    f = request.files.get("file")
    if not f:
        raise ValueError("upload a CSV as multipart field 'file'")
    records = parse_csv(f.read().decode("utf-8", errors="replace"))
    mode = request.form.get("mode") or request.args.get("mode", "append")
    report = ingest_voters(conn(), records, request.form.get("state") or request.args.get("state"), mode,
                           source="csv", actor=g.user["user_id"])
    report["scoring"] = run_scoring(conn())
    privacy.audit(conn(), g.user, "import", {"source": "csv", "mode": mode, "accepted": report["accepted"],
                                              "rejected": report["rejected"]})
    conn().commit()
    return report


@app.post("/api/ingest/json")
@require("ADMIN")
def ingest_json():
    body = request.get_json(force=True, silent=True)
    if isinstance(body, list):
        body = {"voters": body}
    if not isinstance(body, dict) or not isinstance(body.get("voters"), list):
        raise ValueError("send a JSON array of voters or {\"voters\": [...], \"state\": .., \"mode\": ..}")
    report = ingest_voters(conn(), body["voters"], body.get("state"), body.get("mode", "append"), source="json",
                           actor=g.user["user_id"])
    report["scoring"] = run_scoring(conn())
    privacy.audit(conn(), g.user, "import", {"source": "json", "mode": body.get("mode", "append"),
                                              "accepted": report["accepted"], "rejected": report["rejected"]})
    conn().commit()
    return report


@app.post("/api/admin/seed")
@require("ADMIN")
def admin_seed():
    body = request.get_json(force=True, silent=True) or {}
    user = dict(g.user)
    seeded = seed_database(conn(), seed=int(body.get("seed", 42)))
    privacy.audit(conn(), user, "reset_to_mock_data", seeded)
    conn().commit()
    return {"seeded": seeded}


# ---------------- electoral roll import (admin) ----------------

def _roll_input():
    """multipart (file + mapping/options JSON fields) or JSON {records|electors, mapping?, options?}."""
    if request.files.get("file"):
        f = request.files["file"]
        fmt, rows = electoral_roll.parse_upload(f.read(), f.filename or "")
        mapping = json.loads(request.form.get("mapping") or "null")
        options = json.loads(request.form.get("options") or "{}")
        return fmt, rows, mapping, options, f.filename or ""
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise ValueError("upload a CSV/JSON file as 'file', or send JSON with 'records'")
    rows = electoral_roll._flatten_json({k: v for k, v in body.items() if k not in ("mapping", "options")})
    return "json", rows, body.get("mapping"), body.get("options") or {}, "api.json"


@app.post("/api/import/electoral-roll/preview")
@require("ADMIN")
def roll_preview():
    fmt, rows, mapping, options, name = _roll_input()
    suggested, confidence = electoral_roll.suggest_mapping(rows)
    use = {**suggested, **{k: v for k, v in (mapping or {}).items() if k in electoral_roll.TARGETS}}
    out = {"format": fmt, "rows": len(rows), "columns": electoral_roll.columns_of(rows), "sample": rows[:5],
           "targets": {k: {"label": v["label"], "required": v["required"]} for k, v in electoral_roll.TARGETS.items()},
           "suggested": suggested, "confidence": confidence, "mapping": use}
    try:
        out["report"] = electoral_roll.process(conn(), rows, use, options, dry_run=True, filename=name)
    except ValueError as e:
        out["report"], out["blocking"] = None, str(e)
    return out


@app.post("/api/import/electoral-roll")
@require("ADMIN")
def roll_import():
    fmt, rows, mapping, options, name = _roll_input()
    if not mapping:
        mapping, _ = electoral_roll.suggest_mapping(rows)
    c = conn()
    report = electoral_roll.process(c, rows, mapping, options, dry_run=False, actor=g.user["user_id"], filename=name)
    privacy.audit(c, g.user, "electoral_roll_import", {"file": name[:80], "format": fmt, **report["stats"]})
    c.commit()
    # score the new/updated voters with each state's active weights (background job)
    states = report["states"]
    if states:
        try:
            report["scoring"] = scoring_config.start_job(c, states[0] if len(states) == 1 else None, g.user["user_id"])
        except scoring_config.JobBusy as e:
            report["scoring"] = {"status": "DEFERRED", "error": f"{e} Then run scoring from the import panel."}
    return report


# ---------------- talking points ----------------

@app.get("/api/llm/status")
@require()
def llm_status():
    return provider_status()


@app.post("/api/cadre/generate-talking-points")
@require("CADRE")
def cadre_talking_points():
    body = request.get_json(force=True, silent=True) or {}
    hid = body.get("household_id")
    d = today_local()
    if not hid and body.get("stop_id"):
        r = conn().execute("SELECT household_id FROM route_stops WHERE stop_id = ?", (body["stop_id"],)).fetchone()
        hid = r["household_id"] if r else None
    if not hid:
        raise ValueError("household_id or stop_id is required")
    on_route = conn().execute("""SELECT 1 FROM route_stops rs JOIN routes r USING(route_id)
        WHERE rs.household_id = ? AND r.cadre_id = ? AND r.route_date = ?""", (hid, g.user["cadre_id"], d)).fetchone()
    if not on_route:
        forbid("This household is not on today's route.")
    return talking_points.generate(conn(), hid, d, language=str(body.get("language") or "en"),
                                   user_id=g.user["user_id"], refresh=bool(body.get("refresh")))


@app.post("/api/talking-points/preview")
@require("ADMIN", "MANAGER")
def talking_points_preview():
    body = request.get_json(force=True, silent=True) or {}
    hid = body.get("household_id")
    r = conn().execute("SELECT booth_id, state FROM households WHERE household_id = ?", (hid,)).fetchone()
    if not r:
        raise KeyError(f"household {hid} not found")
    if not g.scope.allows_booth(r["booth_id"]) or not g.scope.allows_state(r["state"]):
        forbid()
    return talking_points.generate(conn(), hid, body.get("date") or today_local(),
                                   language=str(body.get("language") or "en"), user_id=g.user["user_id"],
                                   refresh=bool(body.get("refresh")))


# ---------------- directives & knowledge base ----------------

def _directive_place(body) -> tuple[str | None, str | None]:
    if g.user["role"] == "MANAGER":
        return g.user["state"], g.user["constituency"]
    state = body.get("state") or None
    if state and state not in STATES:
        raise ValueError(f"unknown state {state}")
    return state, (body.get("constituency") or None) if state else None


@app.get("/api/directives")
@require()
def directives_list():
    d = request.args.get("date") or today_local()
    u, c = g.user, conn()
    if u["role"] == "CADRE":
        cadre = c.execute("SELECT booth_ids, state FROM cadres WHERE cadre_id = ?", (u["cadre_id"],)).fetchone()
        b = c.execute("SELECT constituency FROM booths WHERE booth_id = ?", (cadre["booth_ids"].split(",")[0],)).fetchone()
        return jsonify(knowledge.directives_for(c, d, cadre["state"], b["constituency"] if b else None))
    if u["role"] == "MANAGER":
        return jsonify(knowledge.directives_for(c, d, u["state"], u["constituency"]))
    return jsonify(db.rows(c.execute("""SELECT d.*, u.display_name author FROM directives d
        LEFT JOIN users u ON u.user_id = d.created_by WHERE d.for_date = ? AND d.active = 1
        ORDER BY d.directive_id DESC""", (d,))))


@app.post("/api/directives")
@require("ADMIN", "MANAGER")
def directives_add():
    body = request.get_json(force=True, silent=True) or {}
    state, const = _directive_place(body)
    c = conn()
    out = knowledge.add_directive(c, body.get("text"), body.get("for_date") or today_local(), state, const, g.user["user_id"])
    privacy.audit(c, g.user, "directive_added", {"id": out["directive_id"], "state": state, "constituency": const})
    c.commit()
    return out, 201


@app.patch("/api/directives/<int:directive_id>")
@require("ADMIN", "MANAGER")
def directives_update(directive_id):
    c = conn()
    row = c.execute("SELECT * FROM directives WHERE directive_id = ?", (directive_id,)).fetchone()
    if not row:
        raise KeyError(f"directive {directive_id} not found")
    if g.user["role"] == "MANAGER" and (row["state"] != g.user["state"] or (row["constituency"] or "").lower()
                                         != (g.user["constituency"] or "").lower()):
        forbid("You can only change your constituency's directives.")
    body = request.get_json(force=True, silent=True) or {}
    if "active" in body:
        c.execute("UPDATE directives SET active = ? WHERE directive_id = ?", (1 if body["active"] else 0, directive_id))
    c.commit()
    return dict(c.execute("SELECT * FROM directives WHERE directive_id = ?", (directive_id,)).fetchone())


@app.get("/api/knowledge")
@require("ADMIN", "MANAGER")
def knowledge_list():
    u = g.user
    if u["role"] == "MANAGER":
        return jsonify(knowledge.list_docs(conn(), u["state"], u["constituency"]))
    return jsonify(knowledge.list_docs(conn(), request.args.get("state"), request.args.get("constituency"),
                                       include_inactive=bool(request.args.get("all"))))


@app.post("/api/knowledge")
@require("ADMIN", "MANAGER")
def knowledge_add():
    body = request.get_json(force=True, silent=True) or {}
    if g.user["role"] == "MANAGER":   # managers add briefs for their own constituency only
        body.update(state=g.user["state"], constituency=g.user["constituency"], kind="brief")
    c = conn()
    doc = knowledge.save_doc(c, body, g.user["user_id"])
    privacy.audit(c, g.user, "knowledge_saved", {"doc_id": doc["doc_id"]})
    c.commit()
    return doc, 201


@app.patch("/api/knowledge/<doc_id>")
@require("ADMIN")
def knowledge_update(doc_id):
    c = conn()
    if not c.execute("SELECT 1 FROM kb_documents WHERE doc_id = ?", (doc_id,)).fetchone():
        raise KeyError(f"document {doc_id} not found")
    doc = knowledge.save_doc(c, request.get_json(force=True, silent=True) or {}, g.user["user_id"], doc_id=doc_id)
    c.commit()
    return doc


# ---------------- cadre operations (manager / admin) ----------------

def _ops_scope():
    sc = g.scope
    state, booth = request.args.get("state"), request.args.get("booth_id")
    d = request.args.get("date") or today_local()
    if sc.role == "MANAGER":
        state = g.user["state"]
        booths = list(sc.booth_ids or [])
        label = g.user["constituency"]
    else:
        if not state:
            raise ValueError("choose a state")
        if not sc.allows_state(state):
            forbid()
        booths = [r[0] for r in conn().execute("SELECT booth_id FROM booths WHERE state = ? ORDER BY booth_id", (state,))]
        label = STATES[state]["name"]
    if booth:
        if booth not in booths:
            forbid()
        booths = [booth]
    return booths, d, label, state


@app.get("/api/ops/summary")
@require("ADMIN", "MANAGER")
def ops_summary():
    booths, d, label, state = _ops_scope()
    return ops.summary(conn(), booths, d, label, state)


@app.get("/api/ops/summary.csv")
@require("ADMIN", "MANAGER")
def ops_summary_csv():
    booths, d, label, state = _ops_scope()
    data = ops.to_csv(ops.summary(conn(), booths, d, label, state))
    privacy.audit(conn(), g.user, "export_booth_summary", {"format": "csv", "date": d}); conn().commit()
    resp = make_response(data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="booth-summary-{label.replace(" ", "-")}-{d}.csv"'
    return resp


@app.get("/api/ops/summary.pdf")
@require("ADMIN", "MANAGER")
def ops_summary_pdf():
    booths, d, label, state = _ops_scope()
    data = ops.to_pdf(ops.summary(conn(), booths, d, label, state))
    privacy.audit(conn(), g.user, "export_booth_summary", {"format": "pdf", "date": d}); conn().commit()
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename="booth-summary-{label.replace(" ", "-")}-{d}.pdf"'
    return resp


# ---------------- JanVani Copilot ----------------

def _cp_scope_sql(alias="d") -> tuple[str, list]:
    """Documents a user may see: everything for strategists; for managers their
    constituency plus state-wide and national material."""
    u = g.user
    if u["role"] == "ADMIN":
        return "1=1", []
    cid = f"{u['state']}-{electoral_roll.constituency_code(u['constituency'])}"
    return (f"({alias}.constituency_id = ? OR ({alias}.constituency_id IS NULL AND ({alias}.state = ? OR {alias}.state IS NULL)))",
            [cid, u["state"]])


def _cp_place(constituency_id: str | None, booth_ids=None) -> dict:
    consts = {c["id"]: c for c in cp_kb.known_constituencies(conn())}
    u = g.user
    if u["role"] == "MANAGER":
        mine = f"{u['state']}-{electoral_roll.constituency_code(u['constituency'])}"
        if constituency_id and constituency_id != mine:
            forbid("You can work on your own constituency only.")
        constituency_id = mine
    if not constituency_id:
        raise ValueError("constituency_id is required")
    c = consts.get(constituency_id)
    if not c:
        raise ValueError(f"unknown constituency {constituency_id}")
    place = {"id": c["id"], "name": c["name"], "state": c["state"]}
    if booth_ids:
        if not isinstance(booth_ids, list):
            raise ValueError("booth_ids must be a list")
        for b in booth_ids:
            r = conn().execute("SELECT state, constituency FROM booths WHERE booth_id = ?", (b,)).fetchone()
            if not r or r["state"] != c["state"] or r["constituency"].lower() != c["name"].lower():
                raise ValueError(f"booth {b} is not in {c['name']}")
            if not g.scope.allows_booth(b):
                forbid()
        place["booth_ids"] = booth_ids
    return place


def _cp_doc(doc_id: str, edit=False) -> dict:
    where, params = _cp_scope_sql()
    row = conn().execute(f"SELECT * FROM copilot_documents d WHERE d.doc_id = ? AND {where}", [doc_id, *params]).fetchone()
    if not row:
        raise KeyError(f"document {doc_id} not found")
    d = dict(row)
    if edit and g.user["role"] == "MANAGER" and not d["constituency_id"]:
        forbid("State-wide and national documents are managed by strategists.")
    return d


@app.get("/api/copilot/meta")
@require("ADMIN", "MANAGER")
def copilot_meta():
    u = g.user
    consts = cp_kb.known_constituencies(conn())
    if u["role"] == "MANAGER":
        consts = [c for c in consts if c["state"] == u["state"] and c["name"].lower() == u["constituency"].lower()]
    where, params = _cp_scope_sql()
    st = conn().execute(f"""SELECT COUNT(DISTINCT d.doc_id) docs, COUNT(c.chunk_id) chunks, SUM(c.embedding IS NOT NULL) emb
                            FROM copilot_documents d LEFT JOIN copilot_chunks c USING(doc_id) WHERE d.active = 1 AND {where}""",
                        params).fetchone()
    return {"constituencies": consts, "topics": list(cp_kb.TOPICS), "doc_types": cp_kb.DOC_TYPES,
            "event_types": cp_gen.EVENT_TYPES, "audiences": {k: v[0] for k, v in cp_gen.AUDIENCES.items()},
            "languages": cp_gen.LANGUAGES, "llm": provider_status(),
            "search": "hybrid (keyword + embeddings)" if cp_kb.get_embedder() else "keyword (BM25)",
            "stats": {"documents": st["docs"] or 0, "chunks": st["chunks"] or 0, "embedded_chunks": st["emb"] or 0}}


@app.post("/api/copilot/ingest")
@require("ADMIN", "MANAGER")
def copilot_ingest():
    """multipart: one or more `file` fields (PDF, TXT, MD, JSON) + optional `meta` JSON
    (state, constituency_id, scope, doc_type, topics, source_citation, source_url, published_on, booth_ids);
    or JSON: {"documents": [{title, text, state, constituency, doc_type, topics, source_citation, ...}]}."""
    c, u = conn(), g.user
    enforce = {"state": u["state"], "constituency": u["constituency"]} if u["role"] == "MANAGER" else None
    embedder = cp_kb.get_embedder()
    results = []
    if request.files:
        meta = json.loads(request.form.get("meta") or "{}")
        for f in request.files.getlist("file"):
            name = f.filename or "upload"
            try:
                for raw in cp_kb.parse_file(f.read(), name):
                    results.append({"file": name, **cp_kb.ingest(c, raw, meta, u["user_id"], name, embedder, enforce)})
            except ValueError as e:
                results.append({"file": name, "status": "error", "error": str(e)})
    else:
        body = request.get_json(force=True, silent=True) or {}
        docs = body.get("documents") if isinstance(body.get("documents"), list) else [body]
        for i, d in enumerate(docs):
            name = str(d.get("file_name") or d.get("title") or f"document-{i + 1}")
            try:
                for raw in cp_kb._parse_json(json.dumps(d), name):
                    results.append({"file": name, **cp_kb.ingest(c, raw, {}, u["user_id"], name, embedder, enforce)})
            except ValueError as e:
                results.append({"file": name, "status": "error", "error": str(e)})
    if not results:
        raise ValueError("send at least one file or document")
    summary = {k: sum(r["status"] == k for r in results) for k in ("indexed", "duplicate", "error")}
    privacy.audit(c, u, "copilot_ingest", {"files": [r["file"] for r in results][:20], **summary})
    c.commit()
    return {"results": results, "summary": summary}


@app.get("/api/copilot/documents")
@require("ADMIN", "MANAGER")
def copilot_documents():
    where, params = _cp_scope_sql()
    clauses = [where, "d.active = 1"]
    for col, arg in (("d.constituency_id", "constituency_id"), ("d.state", "state"), ("d.doc_type", "doc_type")):
        if request.args.get(arg):
            clauses.append(f"{col} = ?"); params.append(request.args[arg])
    if request.args.get("topic"):
        clauses.append("(',' || d.topics || ',') LIKE ?"); params.append(f"%,{request.args['topic']},%")
    if request.args.get("q"):
        clauses.append("(d.title LIKE ? OR d.source_citation LIKE ?)"); params += [f"%{request.args['q']}%"] * 2
    rows = db.rows(conn().execute(f"""SELECT d.*, (SELECT COUNT(*) FROM copilot_chunks c WHERE c.doc_id = d.doc_id) chunks,
        u.display_name uploader FROM copilot_documents d LEFT JOIN users u ON u.user_id = d.uploaded_by
        WHERE {' AND '.join(clauses)} ORDER BY d.uploaded_at DESC, d.title LIMIT 500""", params))
    for r in rows:
        r.pop("sha256", None)
        r["topics"] = [t for t in (r["topics"] or "").split(",") if t]
        r["detected"] = json.loads(r["detected"] or "{}") if isinstance(r["detected"], str) else r["detected"]
        r["editable"] = g.user["role"] == "ADMIN" or bool(r["constituency_id"])
    return jsonify(rows)


@app.get("/api/copilot/documents/<doc_id>")
@require("ADMIN", "MANAGER")
def copilot_document(doc_id):
    d = _cp_doc(doc_id)
    d.pop("sha256", None)
    d["topics"] = [t for t in (d["topics"] or "").split(",") if t]
    d["chunks"] = [dict(r) for r in conn().execute(
        "SELECT chunk_id, seq, page, text, embedding IS NOT NULL AS embedded FROM copilot_chunks WHERE doc_id = ? ORDER BY seq", (doc_id,))]
    return d


@app.patch("/api/copilot/documents/<doc_id>")
@require("ADMIN", "MANAGER")
def copilot_document_update(doc_id):
    d = _cp_doc(doc_id, edit=True)
    body = request.get_json(force=True, silent=True) or {}
    sets, params = [], []
    for k in ("title", "source_citation", "source_url", "published_on"):
        if k in body:
            sets.append(f"{k} = ?"); params.append(str(body[k] or "")[:300] or None)
    if "doc_type" in body:
        if body["doc_type"] not in cp_kb.DOC_TYPES:
            raise ValueError(f"doc_type must be one of {list(cp_kb.DOC_TYPES)}")
        sets.append("doc_type = ?"); params.append(body["doc_type"])
    if "topics" in body:
        t = cp_kb._norm_topics(body["topics"])
        sets += ["topics = ?", "primary_topic = ?"]; params += [",".join(t), t[0] if t else None]
    if g.user["role"] == "ADMIN" and any(k in body for k in ("constituency_id", "scope", "state")):
        if body.get("scope") == "central":
            st, const, cid = None, None, None
        elif body.get("scope") == "state":
            st, const, cid = body.get("state") or d["state"], None, None
        else:
            st, const, cid = cp_kb._resolve_place(conn(), body.get("state") or d["state"], None, body.get("constituency_id"))
        sets += ["state = ?", "constituency = ?", "constituency_id = ?"]; params += [st, const, cid]
    if not sets:
        raise ValueError("nothing to update")
    conn().execute(f"UPDATE copilot_documents SET {', '.join(sets)}, updated_at = ? WHERE doc_id = ?", [*params, db.now_iso(), doc_id])
    privacy.audit(conn(), g.user, "copilot_document_updated", {"doc_id": doc_id, "fields": list(body)[:10]})
    conn().commit()
    return copilot_document(doc_id)


@app.delete("/api/copilot/documents/<doc_id>")
@require("ADMIN", "MANAGER")
def copilot_document_delete(doc_id):
    d = _cp_doc(doc_id, edit=True)
    conn().execute("DELETE FROM copilot_documents WHERE doc_id = ?", (doc_id,))
    privacy.audit(conn(), g.user, "copilot_document_deleted", {"doc_id": doc_id, "title": d["title"][:80]})
    conn().commit()
    return {"deleted": doc_id}


@app.post("/api/copilot/query")
@require("ADMIN", "MANAGER")
def copilot_query():
    body = request.get_json(force=True, silent=True) or {}
    query = str(body.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    u = g.user
    if u["role"] == "MANAGER" or body.get("constituency_id"):
        place = _cp_place(body.get("constituency_id"))
        state, cid = place["state"], place["id"]
    else:
        state, cid = body.get("state") or None, None
        if state and state not in STATES:
            raise ValueError(f"unknown state {state}")
    embedder = cp_kb.get_embedder()
    translated = cp_gen.translate_query(query)
    q = f"{query} {translated}" if translated else query
    types = body.get("doc_types") or ([body["doc_type"]] if body.get("doc_type") else None)
    results = cp_kb.search(conn(), q, state, cid, topic=body.get("topic") or None, doc_types=types,
                           k=max(1, min(int(body.get("k", 8)), 20)), embedder=embedder)
    out = {"query": query, "translated": translated, "search": "hybrid" if embedder else "keyword",
           "results": [{**{k: r[k] for k in ("chunk_id", "doc_id", "title", "doc_type", "topics", "state", "constituency",
                                              "page", "snippet", "score", "citation", "published_on", "source_url")},
                        "id": f"S{i}"} for i, r in enumerate(results, 1)]}
    if body.get("answer", True):
        out["answer"] = cp_gen.answer(conn(), query, place if cid else {"state": state, "name": state or "all"}, results,
                                      u["user_id"], str(body.get("language") or "en"))
    return out


@app.post("/api/copilot/generate-speech-brief")
@require("ADMIN", "MANAGER")
def copilot_brief():
    body = request.get_json(force=True, silent=True) or {}
    place = _cp_place(body.get("constituency_id"), body.get("booth_ids") or None)
    out = cp_gen.brief(conn(), place, str(body.get("event_type") or "rally_speech"), str(body.get("target_audience") or "farmers"),
                       str(body.get("language") or "en"), g.user["user_id"], party_name=str(body.get("party_name") or "")[:60],
                       opponent=str(body.get("opponent") or "")[:60], focus=str(body.get("focus") or "")[:200],
                       embedder=cp_kb.get_embedder())
    privacy.audit(conn(), g.user, "copilot_brief", {"output_id": out["output_id"], "constituency": place["id"],
                                                    "event_type": out["event_type"], "generator": out["generator"]})
    conn().commit()
    return out


@app.post("/api/copilot/generate-manifesto")
@require("ADMIN", "MANAGER")
def copilot_manifesto():
    body = request.get_json(force=True, silent=True) or {}
    place = _cp_place(body.get("constituency_id"), body.get("booth_ids") or None)
    out = cp_gen.manifesto(conn(), place, str(body.get("language") or "en"), g.user["user_id"],
                           party_name=str(body.get("party_name") or "")[:60], topics=body.get("topics") or None,
                           embedder=cp_kb.get_embedder())
    privacy.audit(conn(), g.user, "copilot_manifesto", {"output_id": out["output_id"], "constituency": place["id"],
                                                        "generator": out["generator"]})
    conn().commit()
    return out


def _cp_output(output_id: str) -> dict:
    row = conn().execute("SELECT * FROM copilot_outputs WHERE output_id = ?", (output_id,)).fetchone()
    if not row:
        raise KeyError(f"draft {output_id} not found")
    if g.user["role"] == "MANAGER":
        _cp_place(row["constituency_id"])          # raises if not theirs
    return dict(row)


@app.get("/api/copilot/outputs")
@require("ADMIN", "MANAGER")
def copilot_outputs():
    clauses, params = ["1=1"], []
    if g.user["role"] == "MANAGER":
        clauses.append("o.constituency_id = ?")
        params.append(f"{g.user['state']}-{electoral_roll.constituency_code(g.user['constituency'])}")
    if request.args.get("kind"):
        clauses.append("o.kind = ?"); params.append(request.args["kind"])
    rows = conn().execute(f"""SELECT o.output_id, o.kind, o.constituency_id, o.language, o.params, o.created_at,
        json_extract(o.payload, '$.generator') generator, u.display_name author FROM copilot_outputs o
        LEFT JOIN users u ON u.user_id = o.created_by WHERE {' AND '.join(clauses)} ORDER BY o.created_at DESC, o.rowid DESC LIMIT 30""", params)
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        out.append(d)
    return jsonify(out)


@app.get("/api/copilot/outputs/<output_id>")
@require("ADMIN", "MANAGER")
def copilot_output(output_id):
    if output_id.endswith((".md", ".pdf")):
        oid, fmt = output_id.rsplit(".", 1)
        row = _cp_output(oid)
        payload = json.loads(row["payload"])
        name = f"{row['kind']}-{row['constituency_id'] or 'draft'}-{row['created_at'][:10]}"
        if fmt == "md":
            resp = make_response(cp_export.to_markdown(row["kind"], payload).encode("utf-8"))
            resp.headers["Content-Type"] = "text/markdown; charset=utf-8"
        else:
            resp = make_response(cp_export.to_pdf(row["kind"], payload))
            resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = f'attachment; filename="{name}.{fmt}"'
        privacy.audit(conn(), g.user, "copilot_export", {"output_id": oid, "format": fmt}); conn().commit()
        return resp
    return json.loads(_cp_output(output_id)["payload"])


# ---------------------------------------------------------------------------
# CounterPulse AI — narrative war room
# ---------------------------------------------------------------------------
WR_STATUSES = ("active", "monitoring", "resolved", "dismissed")
WR_MEDIUM_ALIASES = {"tv spokesperson brief": "tv_brief", "tv": "tv_brief", "whatsapp broadcast copy": "whatsapp",
                     "official press release": "press_release", "press release": "press_release",
                     "digital ad text": "digital_ad", "digital ad": "digital_ad"}


def _wr_my_cid() -> str | None:
    u = g.user
    return f"{u['state']}-{electoral_roll.constituency_code(u['constituency'])}" if u["role"] == "MANAGER" else None


def _wr_cluster_scope(alias="n") -> tuple[str, list]:
    """Strategists see every narrative; managers see narratives with items from their constituency."""
    cid = _wr_my_cid()
    if not cid:
        return "1=1", []
    return f"{alias}.cluster_id IN (SELECT cluster_id FROM media_items WHERE constituency_id = ?)", [cid]


def _wr_cluster(cluster_id: str) -> dict:
    where, params = _wr_cluster_scope()
    row = conn().execute(f"SELECT * FROM narrative_clusters n WHERE n.cluster_id = ? AND {where}", [cluster_id, *params]).fetchone()
    if not row:
        raise KeyError(f"narrative {cluster_id} not found")
    return dict(row)


def _wr_cluster_out(r: dict, detail=False) -> dict:
    c = conn()
    b = json.loads(r.get("breakdown") or "{}")
    mix = {x["source_type"]: x["n"] for x in c.execute(
        "SELECT source_type, COUNT(*) n FROM media_items WHERE cluster_id = ? GROUP BY source_type ORDER BY n DESC", (r["cluster_id"],))}
    places = [x[0] for x in c.execute("""SELECT constituency FROM media_items WHERE cluster_id = ? AND constituency IS NOT NULL
                                         GROUP BY constituency ORDER BY COUNT(*) DESC LIMIT 5""", (r["cluster_id"],))]
    channels = [x[0] for x in c.execute("""SELECT channel FROM media_items WHERE cluster_id = ? GROUP BY channel
                                           ORDER BY MAX(COALESCE(reach,0)) DESC LIMIT 6""", (r["cluster_id"],))]
    neg = c.execute("SELECT AVG(negativity), SUM(sentiment_label = 'negative') FROM media_items WHERE cluster_id = ?",
                    (r["cluster_id"],)).fetchone()
    latest = c.execute("""SELECT item_id, channel, source_type, published_at, text FROM media_items WHERE cluster_id = ?
                          ORDER BY published_at DESC LIMIT 1""", (r["cluster_id"],)).fetchone()
    resp = c.execute("""SELECT COUNT(*) n, SUM(status = 'approved') ok FROM warroom_responses WHERE cluster_id = ?""",
                     (r["cluster_id"],)).fetchone()
    out = {"cluster_id": r["cluster_id"], "label": r["label"], "label_source": r["label_source"], "issue": r["issue"],
           "state": r["state"], "status": r["status"], "claim": r["claim"], "threat_score": r["threat_score"],
           "severity": r["severity"], "item_count": r["item_count"], "items_24h": b.pop("items_24h", 0),
           "items_72h": b.pop("items_72h", 0), "trend": b.pop("trend", []), "breakdown": b,
           "first_seen": r["first_seen"], "last_seen": r["last_seen"], "constituencies": places, "channels": channels,
           "source_mix": {wr.SOURCE_TYPES[k]: v for k, v in mix.items()},
           "avg_negativity": round(neg[0] or 0, 2), "negative_items": neg[1] or 0,
           "latest": dict(latest) if latest else None, "responses": resp["n"], "approved_responses": resp["ok"] or 0}
    if detail:
        out["items"] = db.rows(c.execute("""SELECT item_id, state, constituency, source_type, channel, url, published_at, text,
            language, negativity, sentiment_label, reach FROM media_items WHERE cluster_id = ? ORDER BY published_at DESC LIMIT 200""",
                                         (r["cluster_id"],)))
    return out


@app.get("/api/warroom/meta")
@require("ADMIN", "MANAGER")
def warroom_meta():
    c = conn()
    where, params = _wr_cluster_scope()
    counts = {r["severity"]: r["n"] for r in c.execute(
        f"SELECT severity, COUNT(*) n FROM narrative_clusters n WHERE status = 'active' AND {where} GROUP BY severity", params)}
    mwhere, mparams = ("constituency_id = ?", [_wr_my_cid()]) if _wr_my_cid() else ("1=1", [])
    last = c.execute(f"SELECT MAX(ingested_at), COUNT(*), SUM(ingested_by = ?) FROM media_items WHERE {mwhere}",
                     [wr_seed.SAMPLE_ACTOR, *mparams]).fetchone()
    return {"states": STATES, "source_types": wr.SOURCE_TYPES, "mediums": wr_counter.MEDIUMS, "languages": cp_gen.LANGUAGES,
            "severities": [s for _, s in wr.SEVERITY], "statuses": list(WR_STATUSES), "verdicts": wr_counter.VERDICTS,
            "state_significance": wr.STATE_SIGNIFICANCE, "llm": provider_status(),
            "active_by_severity": {s: counts.get(s, 0) for _, s in wr.SEVERITY},
            "media": {"items": last[1] or 0, "sample_items": last[2] or 0, "last_ingested_at": last[0]},
            "can_simulate": g.user["role"] == "ADMIN" and ENV != "production",
            "scope": {"role": g.user["role"], "state": g.user.get("state"), "constituency": g.user.get("constituency")}}


@app.post("/api/warroom/ingest-media")
@require("ADMIN", "MANAGER")
def warroom_ingest():
    """JSON: one item or {"items": [...]}; each item has text, state, constituency, source_type
    (social | print | tv | youtube | survey | other), channel, timestamp, optional url and reach.
    multipart: `file` fields (CSV, JSON or TXT) plus optional `meta` JSON with defaults for every item."""
    c, u = conn(), g.user
    enforce = None
    if u["role"] == "MANAGER":
        enforce = {"state": u["state"], "constituency": u["constituency"], "constituency_id": _wr_my_cid()}
    raw: list[tuple[str, dict]] = []
    if request.files:
        meta = json.loads(request.form.get("meta") or "{}")
        for f in request.files.getlist("file"):
            name = f.filename or "upload"
            for i, it in enumerate(wr.parse_upload(f.read(), name), 1):
                raw.append((f"{name} #{i}", {**meta, **{k: v for k, v in it.items() if v not in (None, "")}}))
    else:
        body = request.get_json(force=True, silent=True) or {}
        items = body.get("items") if isinstance(body.get("items"), list) else [body]
        raw = [(f"item {i}", it) for i, it in enumerate(items, 1) if isinstance(it, dict)]
    if not raw:
        raise ValueError("send at least one media item")
    if len(raw) > 2000:
        raise ValueError("send at most 2,000 items per request")
    results = []
    for ref, it in raw:
        try:
            results.append({"ref": ref, **wr.ingest_item(c, it, u["user_id"], enforce)})
        except ValueError as e:
            results.append({"ref": ref, "status": "error", "error": str(e)})
    touched = list({r["cluster_id"] for r in results if r.get("cluster_id")})
    if touched:
        wr.score_clusters(c, touched)
    for r in results:
        if r.get("cluster_id"):
            row = c.execute("SELECT threat_score, severity FROM narrative_clusters WHERE cluster_id = ?", (r["cluster_id"],)).fetchone()
            r["threat_score"], r["severity"] = row["threat_score"], row["severity"]
    summary = {k: sum(r["status"] == k for r in results) for k in ("ingested", "duplicate", "error")}
    summary["new_clusters"] = sum(bool(r.get("new_cluster")) for r in results)
    privacy.audit(c, u, "warroom_ingest", {**summary, "clusters": touched[:20]})
    c.commit()
    return {"results": results, "summary": summary}


@app.get("/api/warroom/threats")
@require("ADMIN", "MANAGER")
def warroom_threats():
    c = conn()
    wr.rescore_if_stale(c)
    where, params = _wr_cluster_scope()
    clauses = [where]
    status = request.args.get("status", "active")
    if status != "all":
        clauses.append("n.status IN (%s)" % ",".join("?" * len(status.split(","))))
        params += status.split(",")
    if request.args.get("state"):
        sts = [x for x in request.args["state"].split(",") if x]
        clauses.append("n.state IN (%s)" % ",".join("?" * len(sts))); params += sts
    if request.args.get("severity"):
        sev = [x.capitalize() for x in request.args["severity"].split(",") if x]
        clauses.append("n.severity IN (%s)" % ",".join("?" * len(sev))); params += sev
    if request.args.get("min_score"):
        clauses.append("n.threat_score >= ?"); params.append(int(request.args["min_score"]))
    if request.args.get("q"):
        clauses.append("(n.label LIKE ? OR n.claim LIKE ? OR n.issue LIKE ?)"); params += [f"%{request.args['q']}%"] * 3
    if request.args.get("since_hours"):
        since = (datetime.now(timezone.utc) - timedelta(hours=float(request.args["since_hours"]))).isoformat(timespec="seconds")
        clauses.append("n.last_seen >= ?"); params.append(since)
    limit = max(1, min(int(request.args.get("limit", 50)), 200))
    rows = conn().execute(f"""SELECT * FROM narrative_clusters n WHERE {' AND '.join(clauses)}
                              ORDER BY n.threat_score DESC, n.last_seen DESC LIMIT ?""", [*params, limit]).fetchall()
    return {"generated_at": db.now_iso(), "clusters": [_wr_cluster_out(dict(r)) for r in rows]}


@app.get("/api/warroom/threats/<cluster_id>")
@require("ADMIN", "MANAGER")
def warroom_threat(cluster_id):
    return _wr_cluster_out(_wr_cluster(cluster_id), detail=True)


@app.patch("/api/warroom/threats/<cluster_id>")
@require("ADMIN")
def warroom_threat_update(cluster_id):
    _wr_cluster(cluster_id)
    body = request.get_json(force=True, silent=True) or {}
    sets, params = [], []
    if "label" in body:
        label = str(body["label"] or "").strip()[:80]
        if label:
            sets += ["label = ?", "label_source = 'manual'"]; params.append(label)
        else:
            sets.append("label_source = 'auto'")
    if "status" in body:
        if body["status"] not in WR_STATUSES:
            raise ValueError(f"status must be one of {list(WR_STATUSES)}")
        sets.append("status = ?"); params.append(body["status"])
    if not sets:
        raise ValueError("send label or status")
    conn().execute(f"UPDATE narrative_clusters SET {', '.join(sets)}, updated_at = ? WHERE cluster_id = ?",
                   [*params, db.now_iso(), cluster_id])
    if "label" in body and not str(body["label"] or "").strip():
        wr.refresh_cluster(conn(), cluster_id)          # back to the automatic label
    privacy.audit(conn(), g.user, "warroom_narrative_updated", {"cluster_id": cluster_id, **{k: body[k] for k in ("label", "status") if k in body}})
    conn().commit()
    return warroom_threat(cluster_id)


@app.post("/api/warroom/generate-counter")
@require("ADMIN", "MANAGER")
def warroom_generate_counter():
    body = request.get_json(force=True, silent=True) or {}
    cid = str(body.get("narrative_cluster_id") or body.get("cluster_id") or "")
    _wr_cluster(cid)
    medium = str(body.get("target_medium") or "tv_brief")
    medium = WR_MEDIUM_ALIASES.get(medium.lower(), medium.lower().replace(" ", "_"))
    out = wr_counter.generate(conn(), cid, medium, str(body.get("language") or "en"), g.user["user_id"],
                              party_name=str(body.get("party_name") or "")[:60], spokesperson=str(body.get("spokesperson") or "")[:80],
                              embedder=cp_kb.get_embedder())
    privacy.audit(conn(), g.user, "warroom_counter", {"response_id": out["response_id"], "cluster_id": cid, "medium": medium,
                                                      "generator": out["generator"], "safety": out["safety_check"]["status"]})
    conn().commit()
    return out


def _wr_response(response_id: str) -> dict:
    row = conn().execute("SELECT * FROM warroom_responses WHERE response_id = ?", (response_id,)).fetchone()
    if not row:
        raise KeyError(f"response {response_id} not found")
    _wr_cluster(row["cluster_id"])                        # scope check
    d = dict(row)
    p = json.loads(d["payload"])
    p.update({"status": d["status"], "reviewed_by": d["reviewed_by"], "reviewed_at": d["reviewed_at"]})
    d["payload"] = p
    return d


@app.get("/api/warroom/responses")
@require("ADMIN", "MANAGER")
def warroom_responses():
    where, params = _wr_cluster_scope("r")
    clauses = [where]
    for col in ("cluster_id", "medium", "status"):
        if request.args.get(col):
            clauses.append(f"r.{col} = ?"); params.append(request.args[col])
    rows = conn().execute(f"""SELECT r.response_id, r.cluster_id, r.medium, r.language, r.status, r.created_at, r.reviewed_at,
        n.label, json_extract(r.payload, '$.generator') generator, json_extract(r.payload, '$.safety_check.status') safety,
        u.display_name author, rv.display_name reviewer FROM warroom_responses r
        JOIN narrative_clusters n USING(cluster_id) LEFT JOIN users u ON u.user_id = r.created_by
        LEFT JOIN users rv ON rv.user_id = r.reviewed_by
        WHERE {' AND '.join(clauses)} ORDER BY r.created_at DESC, r.rowid DESC LIMIT 50""", params)
    return jsonify([{**dict(r), "medium_label": wr_counter.MEDIUMS.get(r["medium"], r["medium"])} for r in rows])


@app.get("/api/warroom/responses/<response_id>")
@require("ADMIN", "MANAGER")
def warroom_response(response_id):
    if response_id.endswith((".md", ".pdf")):
        rid, fmt = response_id.rsplit(".", 1)
        d = _wr_response(rid)
        name = f"counter-{d['medium']}-{d['cluster_id']}-{d['created_at'][:10]}"
        if fmt == "md":
            resp = make_response(wr_export.to_markdown(d["payload"]).encode("utf-8"))
            resp.headers["Content-Type"] = "text/markdown; charset=utf-8"
        else:
            resp = make_response(wr_export.to_pdf(d["payload"]))
            resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = f'attachment; filename="{name}.{fmt}"'
        privacy.audit(conn(), g.user, "warroom_export", {"response_id": rid, "format": fmt}); conn().commit()
        return resp
    return _wr_response(response_id)["payload"]


@app.patch("/api/warroom/responses/<response_id>")
@require("ADMIN")
def warroom_response_review(response_id):
    d = _wr_response(response_id)
    body = request.get_json(force=True, silent=True) or {}
    status = body.get("status")
    if status not in ("draft", "approved", "rejected"):
        raise ValueError("status must be draft, approved or rejected")
    if status == "approved" and d["payload"]["safety_check"]["status"] == "blocked":
        raise ValueError("This draft failed the safety check (no verified facts). Add sources and generate again.")
    conn().execute("UPDATE warroom_responses SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE response_id = ?",
                   (status, None if status == "draft" else g.user["user_id"], None if status == "draft" else db.now_iso(), response_id))
    privacy.audit(conn(), g.user, "warroom_review", {"response_id": response_id, "status": status,
                                                     "note": str(body.get("note") or "")[:200]})
    conn().commit()
    return _wr_response(response_id)["payload"]


def _wr_media_query():
    clauses, params = ["1=1"], []
    if _wr_my_cid():
        clauses.append("m.constituency_id = ?"); params.append(_wr_my_cid())
    a = request.args
    for col in ("state", "constituency_id", "source_type", "cluster_id", "language"):
        if a.get(col):
            vals = [x for x in a[col].split(",") if x]
            clauses.append(f"m.{col} IN ({','.join('?' * len(vals))})"); params += vals
    if a.get("sentiment"):
        clauses.append("m.sentiment_label = ?"); params.append(a["sentiment"])
    if a.get("severity"):
        sev = [x.capitalize() for x in a["severity"].split(",") if x]
        clauses.append(f"n.severity IN ({','.join('?' * len(sev))})"); params += sev
    if a.get("q"):
        clauses.append("(m.text LIKE ? OR m.channel LIKE ?)"); params += [f"%{a['q']}%"] * 2
    if a.get("since_hours"):
        since = (datetime.now(timezone.utc) - timedelta(hours=float(a["since_hours"]))).isoformat(timespec="seconds")
        clauses.append("m.published_at >= ?"); params.append(since)
    return " AND ".join(clauses), params


@app.get("/api/warroom/media")
@require("ADMIN", "MANAGER")
def warroom_media():
    where, params = _wr_media_query()
    csv_out = request.args.get("format") == "csv"
    limit = 5000 if csv_out else max(1, min(int(request.args.get("limit", 100)), 500))
    offset = 0 if csv_out else max(0, int(request.args.get("offset", 0)))
    base = f"FROM media_items m LEFT JOIN narrative_clusters n USING(cluster_id) WHERE {where}"
    total = conn().execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows = db.rows(conn().execute(f"""SELECT m.item_id, m.published_at, m.state, m.constituency, m.source_type, m.channel, m.url,
        m.text, m.language, m.negativity, m.sentiment_label, m.reach, m.cluster_id, n.label cluster_label,
        n.threat_score, n.severity, (m.ingested_by = ?) sample {base}
        ORDER BY m.published_at DESC, m.rowid DESC LIMIT ? OFFSET ?""", [wr_seed.SAMPLE_ACTOR, *params, limit, offset]))
    if csv_out:
        import csv
        import io
        buf = io.StringIO()
        cols = ["published_at", "state", "constituency", "source_type", "channel", "text", "sentiment_label", "negativity",
                "reach", "cluster_label", "threat_score", "severity", "url"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "source_type": wr.SOURCE_TYPES.get(r["source_type"], r["source_type"])})
        resp = make_response(buf.getvalue().encode("utf-8-sig"))
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = f'attachment; filename="media-log-{datetime.now(timezone.utc):%Y-%m-%d}.csv"'
        privacy.audit(conn(), g.user, "warroom_media_export", {"rows": len(rows)}); conn().commit()
        return resp
    return {"total": total, "limit": limit, "offset": offset, "items": rows}


@app.post("/api/warroom/recluster")
@require("ADMIN")
def warroom_recluster():
    out = wr.recluster(conn())
    wr.score_clusters(conn())
    privacy.audit(conn(), g.user, "warroom_recluster", out)
    conn().commit()
    return out


@app.post("/api/warroom/sample-feed")
@require("ADMIN")
def warroom_sample_feed():
    """Demo only: {"simulate": n} adds n fresh synthetic items; {"reset": true} reloads the sample feed;
    {"remove": true} deletes every sample item (do this before monitoring real media)."""
    if ENV == "production":
        forbid("The sample feed is disabled in production.")
    body = request.get_json(force=True, silent=True) or {}
    c = conn()
    if body.get("remove") or body.get("reset"):
        c.execute("DELETE FROM media_items WHERE ingested_by = ?", (wr_seed.SAMPLE_ACTOR,))
        c.execute("DELETE FROM narrative_clusters WHERE cluster_id NOT IN (SELECT DISTINCT cluster_id FROM media_items WHERE cluster_id IS NOT NULL)")
        for (cid,) in c.execute("SELECT cluster_id FROM narrative_clusters").fetchall():
            wr.refresh_cluster(c, cid)
        out = {"removed": True}
        if body.get("reset"):
            out["seeded"] = wr_seed.seed_feed(c, force=True)
    else:
        n = max(1, min(int(body.get("simulate", 3)), 20))
        res = wr_seed.simulate(c, n, wr_seed.SAMPLE_ACTOR)
        out = {"simulated": sum(r["status"] == "ingested" for r in res), "clusters": list({r.get("cluster_id") for r in res})}
    wr.score_clusters(c)
    privacy.audit(c, g.user, "warroom_sample_feed", {k: v for k, v in out.items() if k != "clusters"})
    c.commit()
    return out


# ---------------------------------------------------------------------------
# Suite: pitch demo workspace, command center, executive briefing
# ---------------------------------------------------------------------------

def _set_cookie(resp, name, value, max_age):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=COOKIE_SECURE, samesite="Lax", path="/")


def _demo_status() -> dict:
    meta = suite_pitch.demo_meta() if suite_pitch.demo_allowed() else None
    user = g.get("user")
    return {"allowed": suite_pitch.demo_allowed(), "active": g.workspace == "demo",
            "built_at": meta.get("built_at") if meta else None,
            "stale": suite_pitch.is_stale(meta) if meta else True,
            "stats": json.loads(meta["stats"]) if meta and meta.get("stats") else None,
            "can_enable": bool(user and user["role"] == "ADMIN" and suite_pitch.demo_allowed()),
            "can_rebuild": bool(user and user["role"] == "ADMIN" and suite_pitch.demo_allowed())}


@app.get("/api/demo/status")
def demo_status():
    return _demo_status()


@app.post("/api/demo/exit")
def demo_exit():
    """Leave the pitch demo. Restores the strategist's real session if it is still valid; otherwise signs out."""
    restored = False
    main_token = request.cookies.get(MAIN_TOKEN_COOKIE)
    resp = make_response()
    if main_token:
        try:
            claims = auth.decode_token(main_token)
            if claims.get("ws", "main") == "main":
                left = int(claims["exp"] - datetime.now(timezone.utc).timestamp())
                _set_cookie(resp, AUTH_COOKIE, main_token, max(left, 60))
                restored = True
        except AuthError:
            pass
    if not restored:
        resp.delete_cookie(AUTH_COOKIE, path="/", samesite="Lax")
    resp.delete_cookie(DEMO_COOKIE, path="/", samesite="Lax")
    resp.delete_cookie(MAIN_TOKEN_COOKIE, path="/", samesite="Lax")
    resp.set_data(json.dumps({"active": False, "restored_session": restored}))
    resp.mimetype = "application/json"
    return resp


@app.post("/api/demo/pitch")
@require()
def demo_pitch():
    """{"enabled": true} switches this browser session into the pitch-demo workspace (building it if needed);
    {"enabled": true, "rebuild": true} rebuilds it; {"enabled": false} leaves it."""
    body = request.get_json(force=True, silent=True) or {}
    if not body.get("enabled", True):
        return demo_exit()
    if g.user["role"] != "ADMIN":
        forbid("Only a strategist can start the pitch demo.")
    if not suite_pitch.demo_allowed():
        forbid("The pitch demo is turned off on this server (VOTERENGINE_PITCH_DEMO).")
    meta, built = suite_pitch.demo_meta(), None
    if body.get("rebuild") or suite_pitch.is_stale(meta):
        if not _PITCH_LOCK.acquire(blocking=False):
            raise ValueError("The demo is being prepared. Try again in a few seconds.")
        try:
            built = suite_pitch.build()
        finally:
            _PITCH_LOCK.release()
    resp = make_response()
    if g.workspace == "main":
        uid = suite_pitch.ensure_user(g.user)
        privacy.audit(conn(), g.user, "pitch_demo_on", {"rebuilt": bool(built)}); conn().commit()
        current = request.cookies.get(AUTH_COOKIE)
        if current and g.auth_via == "cookie":
            _set_cookie(resp, MAIN_TOKEN_COOKIE, current, JWT_TTL_HOURS * 3600)
    else:
        uid = g.user["user_id"]
    db.set_workspace("demo")
    try:
        with db.session() as dc:
            row = dc.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone() or \
                dc.execute("SELECT * FROM users WHERE user_id = ?", (suite_pitch.STRATEGIST,)).fetchone()
            user = dict(row)
            token = auth.issue_token(user)            # stamped ws=demo: useless against the real workspace
            privacy.audit(dc, user, "pitch_demo_session", {"rebuilt": bool(built)})
    finally:
        db.set_workspace(g.workspace)
    _set_cookie(resp, AUTH_COOKIE, token, JWT_TTL_HOURS * 3600)
    _set_cookie(resp, DEMO_COOKIE, "1", JWT_TTL_HOURS * 3600)
    g.workspace = "demo"
    status = _demo_status()
    status.update(active=True, user=auth.public_user(user), built=built, built_at=(built or meta or {}).get("built_at"))
    resp.set_data(json.dumps(status))
    resp.mimetype = "application/json"
    return resp


def _area() -> suite_command.Area:
    u = g.user
    if u["role"] == "MANAGER":
        return suite_command.Area([u["state"]], u["constituency"])
    body = request.get_json(silent=True) if request.method == "POST" else None
    st = request.args.get("state") or (body or {}).get("state")
    if st:
        if st not in STATES:
            raise ValueError(f"unknown state {st}")
        return suite_command.Area([st])
    return suite_command.Area(list(STATES))


@app.get("/api/command/overview")
@require("ADMIN", "MANAGER")
def command_overview():
    area = _area()
    out = suite_command.overview(conn(), area, include_states=g.user["role"] == "ADMIN" and len(area.states) > 1)
    out.update(generated_at=db.now_iso(), workspace=g.workspace, llm=provider_status(),
               states=[{"code": k, "name": v["name"]} for k, v in STATES.items() if g.scope.allows_state(k)])
    return out


@app.post("/api/command/dispatch-routes")
@require("ADMIN", "MANAGER")
def command_dispatch():
    area = _area()
    body = request.get_json(force=True, silent=True) or {}
    res = suite_command.dispatch(conn(), area, max(5, min(int(body.get("max_stops", 20)), 40)))
    privacy.audit(conn(), g.user, "dispatch_routes", {"area": area.label, "dispatched": res["dispatched"]})
    conn().commit()
    res["field"] = suite_command.field_today(conn(), area)
    return res


@app.get("/api/reports/executive-briefing")
@require("ADMIN", "MANAGER")
def executive_briefing():
    area = _area()
    data = suite_briefing.gather(conn(), area, g.workspace)
    if request.args.get("format") == "json":
        out = {k: v for k, v in data.items() if k not in ("area", "generated")}
        out.update(area=area.label, generated=data["generated"].isoformat(timespec="seconds"),
                   summary=suite_briefing.summary(data), next_actions=suite_briefing.next_actions(data))
        return out
    pdf = suite_briefing.to_pdf(data)
    privacy.audit(conn(), g.user, "executive_briefing", {"area": area.label, "workspace": g.workspace}); conn().commit()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", area.label).strip("-")
    resp = make_response(pdf)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = (f'attachment; filename="executive-briefing-{slug}-{today_local()}'
                                           f'{"-PITCH-DEMO" if g.workspace == "demo" else ""}.pdf"')
    return resp


def ensure_seeded():
    with db.session() as c:
        db.init_schema(c)
     #   if c.execute("SELECT COUNT(*) FROM voters").fetchone()[0] == 0 and ENV == "production":
      #      print("Production: empty database. Create logins with `python -m scripts.create_user` and import rolls; "
       #           "no synthetic data is generated.")
        #    return
        if c.execute("SELECT COUNT(*) FROM voters").fetchone()[0] == 0:
            print("Empty database — generating mock data and demo logins…")
            print(json.dumps(seed_database(c)))
            print(json.dumps(auth.seed_demo_users(c, force=True)))
        elif c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            print(json.dumps(auth.seed_demo_users(c)))   # upgraded v1 database: add logins, re-score
            run_scoring(c)
        knowledge.seed_knowledge(c)                      # upgraded v2 database: add the starter knowledge base
        if ENV != "production":
            cp_kb.seed_corpus(c)                         # JanVani Copilot sample corpus (synthetic, labelled)
            wr_seed.seed_feed(c)                         # CounterPulse sample media feed (synthetic, labelled)


if __name__ == "__main__":
    ensure_seeded()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=bool(os.environ.get("DEBUG")),
            threaded=True)
