import io
import re

import pytest
from pypdf import PdfReader

from app import db
from app.suite import pitch


def pdf_text(data: bytes) -> tuple[int, str]:
    r = PdfReader(io.BytesIO(data))
    return len(r.pages), "\n".join(p.extract_text() for p in r.pages)


# --- command center ------------------------------------------------------

def test_overview_all_states(admin):
    o = admin.get("/api/command/overview").get_json()
    assert o["area"]["label"] == "All states" and o["workspace"] == "main"
    k = o["kpis"]
    assert k["active_booths"] > 0 and k["persuade"] > 0
    assert {"manifestos", "critical_threats", "booths_covered_today"} <= set(k)
    assert [s["state"] for s in o["by_state"]] == ["UP", "PB", "UK", "GA"]
    assert sum(s["persuade"] for s in o["by_state"]) == k["persuade"]
    assert o["voterengine"]["top_booths"][0]["priority_score"] >= o["voterengine"]["top_booths"][-1]["priority_score"]
    assert o["actions"]["speech"]["constituency_id"] and o["actions"]["threat"]["cluster_id"]
    up = admin.get("/api/command/overview?state=UP").get_json()
    assert up["by_state"] is None and up["kpis"]["active_booths"] < k["active_booths"]
    assert all(n["state"] == "UP" for n in up["warroom"]["narratives"])


def test_overview_scope(make_client):
    m = make_client("mgr.sardhana").get("/api/command/overview?state=PB").get_json()
    assert m["area"]["constituency"] == "Sardhana" and m["by_state"] is None      # state param ignored for managers
    assert all(b["constituency"] == "Sardhana" for b in m["voterengine"]["top_booths"])
    assert make_client("cad-up-sar-001").get("/api/command/overview").status_code == 403
    assert make_client("cad-up-sar-001").get("/api/reports/executive-briefing").status_code == 403


def test_dispatch_routes(admin):
    before = admin.get("/api/command/overview?state=GA").get_json()["voterengine"]["field"]
    r = admin.post("/api/command/dispatch-routes", json={"state": "GA"}).get_json()
    assert r["dispatched"] == before["cadres"] - before["cadres_dispatched"] > 0
    assert r["field"]["cadres_dispatched"] == r["field"]["cadres"] and r["field"]["planned"] > 0
    assert admin.post("/api/command/dispatch-routes", json={"state": "GA"}).get_json()["dispatched"] == 0


# --- executive briefing --------------------------------------------------

@pytest.mark.parametrize("q", ["", "?state=UP", "?state=GA"])
def test_executive_briefing_is_two_pages(admin, q):
    r = admin.get(f"/api/reports/executive-briefing{q}")
    assert r.status_code == 200 and r.headers["Content-Type"] == "application/pdf"
    pages, text = pdf_text(r.data)
    assert pages == 2
    for heading in ("Target voter segments and booth turnout priorities", "Top constituency grievances",
                    "Media sentiment and active crisis counter-strategies", "Next 72 hours"):
        assert heading in text.replace("\n", " ")


def test_briefing_has_no_personal_data(admin, make_client):
    names = [r["name"] for r in db.connect(db.workspace_path("main")).execute("SELECT name FROM voters ORDER BY random() LIMIT 300")]
    _, text = pdf_text(admin.get("/api/reports/executive-briefing").data)
    assert not any(n in text for n in names)
    assert not re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", text)
    m = make_client("mgr.sardhana").get("/api/reports/executive-briefing")
    pages, mt = pdf_text(m.data)
    assert pages == 2 and "Sardhana" in mt and "Sangrur" not in mt
    j = admin.get("/api/reports/executive-briefing?format=json").get_json()
    assert j["summary"] and j["next_actions"] and j["kpis"]["persuade"] > 0
    audit = admin.get("/api/admin/audit").get_json()
    rows = audit if isinstance(audit, list) else audit.get("items", audit.get("entries", []))
    assert any(e["action"] == "executive_briefing" for e in rows)


# --- pitch demo ----------------------------------------------------------

def test_pitch_demo_is_isolated(admin, make_client):
    main_media = admin.get("/api/warroom/meta").get_json()["media"]["items"]
    main_outputs = admin.get("/api/command/overview").get_json()["kpis"]["manifestos"]

    r = admin.post("/api/demo/pitch", json={"enabled": True})
    assert r.status_code == 200, r.get_json()
    s = r.get_json()
    assert s["active"] and s["built"]["stats"]["voters"] > 5000 and s["built"]["stats"]["manifestos"] == 4
    assert admin.get("/api/auth/me").get_json()["workspace"] == "demo"
    o = admin.get("/api/command/overview").get_json()
    assert o["workspace"] == "demo" and o["kpis"]["manifestos"] == 4 and o["kpis"]["critical_threats"] >= 1
    assert o["voterengine"]["field"]["progress"] > 0 and o["voterengine"]["field"]["consents_granted_today"] > 0
    assert {s["state"] for s in o["by_state"] if s["active_narratives"]} == {"UP", "PB", "UK", "GA"}
    assert any(n["response"] and n["response"]["status"] == "approved" for n in o["warroom"]["narratives"])
    _, text = pdf_text(admin.get("/api/reports/executive-briefing").data)
    assert "PITCH DEMO" in text

    # the real workspace is untouched, and other users never see the demo
    other = make_client("admin")
    assert other.get("/api/auth/me").get_json()["workspace"] == "main"
    assert other.get("/api/warroom/meta").get_json()["media"]["items"] == main_media
    assert other.get("/api/command/overview").get_json()["kpis"]["manifestos"] == main_outputs

    # a token minted inside the demo is rejected by the real workspace
    demo_token = admin.c.get_cookie("ve_token").value
    thief = make_client()
    thief.c.set_cookie("ve_token", demo_token)
    assert thief.get("/api/auth/me").status_code == 401

    # role switching inside the demo uses demo accounts
    admin.post("/api/auth/logout")
    admin.login("mgr.sardhana")
    assert admin.get("/api/auth/me").get_json()["workspace"] == "demo"
    assert admin.post("/api/demo/pitch", json={"enabled": True}).status_code == 403

    out = admin.post("/api/demo/exit").get_json()
    assert out["active"] is False and out["restored_session"] is False      # logout dropped the stashed session
    assert admin.get("/api/auth/me").status_code == 401


def test_pitch_exit_restores_real_session(admin):
    admin.post("/api/demo/pitch", json={"enabled": True})
    assert admin.get("/api/auth/me").get_json()["workspace"] == "demo"
    out = admin.post("/api/demo/pitch", json={"enabled": False}).get_json()
    assert out["restored_session"] is True
    me = admin.get("/api/auth/me").get_json()
    assert me["workspace"] == "main" and me["user"]["role"] == "ADMIN"
    st = admin.get("/api/demo/status").get_json()
    assert st["active"] is False and st["built_at"] and st["can_enable"]
    # a second start is instant (no rebuild the same day)
    again = admin.post("/api/demo/pitch", json={"enabled": True}).get_json()
    assert again["active"] and again["built"] is None
    rebuilt = admin.post("/api/demo/pitch", json={"enabled": True, "rebuild": True}).get_json()
    assert rebuilt["built"] and admin.get("/api/auth/me").get_json()["workspace"] == "demo"


def test_pitch_demo_permissions(make_client, monkeypatch):
    assert make_client("mgr.sardhana").post("/api/demo/pitch", json={"enabled": True}).status_code == 403
    monkeypatch.setattr(pitch, "ENV", "production")
    monkeypatch.delenv("VOTERENGINE_PITCH_DEMO", raising=False)
    a = make_client("admin")
    assert a.get("/api/demo/status").get_json()["allowed"] is False
    assert a.post("/api/demo/pitch", json={"enabled": True}).status_code == 403
    monkeypatch.setenv("VOTERENGINE_PITCH_DEMO", "1")
    assert a.get("/api/demo/status").get_json()["allowed"] is True


def test_readiness_check_flags_synthetic_material(app_db):
    from scripts import readiness
    rows = {item: (status, detail) for status, item, detail in readiness.run()}
    assert rows["Synthetic voters"][0] == "FAIL" and rows["Demo passwords"][0] == "FAIL"
    assert rows["Sample Copilot documents"][0] == "FAIL" and rows["Sample media feed"][0] == "FAIL"
    assert rows["PII masking"][0] == "PASS"
    rows = {item: status for status, item, _ in readiness.run(remove_samples=True)}
    assert rows["Sample Copilot documents"] == "PASS" and rows["Sample media feed"] == "PASS"


def test_production_start_creates_no_synthetic_data(tmp_path, monkeypatch):
    from app import db, main
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "prod.db")
    monkeypatch.setattr(main, "ENV", "production")
    main.ensure_seeded()
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM voters").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM copilot_documents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM media_items").fetchone()[0] == 0
