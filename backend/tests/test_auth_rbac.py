import jwt

from app.config import AUTH_COOKIE, JWT_SECRET
from conftest import H, PW


def test_unauthenticated_requests_are_rejected(make_client):
    anon = make_client()
    assert anon.get("/api/health").status_code == 200
    for url in ["/api/meta", "/api/dashboard", "/api/voters", "/api/cadre/route", "/api/scoring/config"]:
        assert anon.get(url).status_code == 401, url


def test_login_sets_http_only_cookie_and_valid_jwt(make_client):
    cl = make_client()
    r = cl.c.post("/api/auth/login", json={"username": "admin", "password": PW}, headers=H)
    assert r.status_code == 200
    cookie = r.headers["Set-Cookie"]
    assert f"{AUTH_COOKIE}=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "token" not in r.get_json()          # web clients never see the token
    token = cookie.split(f"{AUTH_COOKIE}=")[1].split(";")[0]
    claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert claims["role"] == "ADMIN" and claims["exp"] > claims["iat"]


def test_bearer_token_flow(make_client):
    cl = make_client()
    tok = cl.c.post("/api/auth/login", json={"username": "mgr.sardhana", "password": PW, "issue_bearer": True},
                    headers=H).get_json()["token"]
    other = make_client()
    r = other.c.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.get_json()["user"]["role"] == "MANAGER"
    # bearer clients are exempt from the CSRF header
    assert other.c.post("/api/auth/logout", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_bad_password_and_throttle(make_client):
    cl = make_client()
    for _ in range(5):
        assert cl.c.post("/api/auth/login", json={"username": "admin", "password": "wrong"}, headers=H).status_code == 401
    r = cl.c.post("/api/auth/login", json={"username": "admin", "password": PW}, headers=H)
    assert r.status_code == 429


def test_csrf_header_required_for_cookie_writes(admin):
    r = admin.c.post("/api/scoring/run")   # no X-Requested-With
    assert r.status_code == 403


def test_tampered_or_expired_token_rejected(make_client):
    cl = make_client()
    forged = jwt.encode({"sub": "U-admin", "role": "ADMIN", "exp": 9999999999}, "an-attacker-guessed-secret-of-enough-length!", algorithm="HS256")
    assert cl.c.get("/api/meta", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    expired = jwt.encode({"sub": "U-admin", "role": "ADMIN", "exp": 1}, JWT_SECRET, algorithm="HS256")
    assert cl.c.get("/api/meta", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_admin_sees_all_states(admin):
    assert {s["code"] for s in admin.get("/api/meta").get_json()["states"]} == {"UP", "PB", "UK", "GA"}
    assert admin.get("/api/dashboard?state=GA").status_code == 200


def test_manager_is_confined_to_constituency(make_client):
    m = make_client("mgr.sardhana")
    meta = m.get("/api/meta").get_json()
    assert [s["code"] for s in meta["states"]] == ["UP"] and meta["constituency"] == "Sardhana"
    d = m.get("/api/dashboard").get_json()
    assert {b["constituency"] for b in d["booths"]} == {"Sardhana"}
    assert m.get("/api/dashboard?state=PB").status_code == 403
    assert m.get("/api/dashboard?booth_id=UP-MC-001").status_code == 403      # Meerut Cantt booth
    assert all(v["booth_id"].startswith("UP-SAR") for v in m.get("/api/voters?limit=200").get_json()["items"])
    assert {c["state"] for c in m.get("/api/cadres").get_json()} == {"UP"}
    # admin-only features
    for url in ["/api/scoring/config", "/api/privacy", "/api/admin/audit"]:
        assert m.get(url).status_code == 403
    assert m.post("/api/ingest/json", json=[]).status_code == 403
    assert m.post("/api/scoring/recalibrate", json={}).status_code == 403


def test_manager_cadre_assignment_scoped(make_client):
    m = make_client("mgr.sardhana")
    r = m.patch("/api/cadres/CAD-UP-SAR-001", json={"booth_ids": ["UP-SAR-001", "UP-SAR-002"]})
    assert r.status_code == 200 and r.get_json()["booth_ids"] == ["UP-SAR-001", "UP-SAR-002"]
    assert m.patch("/api/cadres/CAD-UP-SAR-001", json={"booth_ids": ["UP-MC-001"]}).status_code == 403
    assert m.patch("/api/cadres/CAD-UP-MC-001", json={"booth_ids": ["UP-SAR-001"]}).status_code == 403


def test_cadre_only_reaches_own_route(make_client):
    cad = make_client("cad-up-sar-001")
    for url in ["/api/dashboard", "/api/voters", "/api/clusters", "/api/cadres", "/api/booths"]:
        assert cad.get(url).status_code == 403, url
    r = cad.post("/api/cadre/route", json={"max_stops": 10}).get_json()
    assert r["cadre_id"] == "CAD-UP-SAR-001" and r["stops"]
    assert {s["booth_id"] for s in r["stops"]} == {"UP-SAR-001"}
    # another cadre cannot touch these stops
    other = make_client("cad-up-sar-002")
    res = other.patch(f"/api/stops/{r['stops'][0]['stop_id']}", json={"status": "VISITED"})
    assert res.status_code == 400 and "not on your route" in res.get_json()["error"]


def test_deactivated_user_loses_access_immediately(admin, make_client, app_db):
    from app import db
    m = make_client("mgr.dhuri")
    with db.session(app_db) as c:
        c.execute("UPDATE users SET active = 0 WHERE username = 'mgr.dhuri'")
    assert m.get("/api/meta").status_code == 401
