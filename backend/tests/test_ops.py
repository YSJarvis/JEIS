from datetime import datetime, timezone


def _work_a_route(make_client, username, n_visit=3):
    cad = make_client(username)
    route = cad.post("/api/cadre/route", json={"max_stops": 10}).get_json()
    ts = datetime.now(timezone.utc).isoformat()
    ops = []
    for i, s in enumerate(route["stops"][:n_visit]):
        ops.append({"op_id": f"{username}-v{i}", "type": "stop_status", "stop_id": s["stop_id"],
                    "household_id": s["household_id"], "status": "VISITED", "client_ts": ts})
    ops.append({"op_id": f"{username}-nh", "type": "stop_status", "stop_id": route["stops"][n_visit]["stop_id"],
                "status": "NOT_HOME", "client_ts": ts})
    pending_consent = [m for m in route["stops"][0]["members"] if m["consent_status"] == "PENDING"]
    if pending_consent:
        ops.append({"op_id": f"{username}-c", "type": "consent", "voter_id": pending_consent[0]["voter_id"],
                    "status": "GRANTED", "client_ts": ts})
    cad.post("/api/cadre/sync", json={"ops": ops, "client": {"remaining": 0, "app_version": "test"}})
    return route, bool(pending_consent)


def test_manager_summary_tracks_progress_and_consent(make_client):
    route, consent_captured = _work_a_route(make_client, "cad-up-sar-001")
    mgr = make_client("mgr.sardhana")
    mgr.post("/api/routes/generate", json={"cadre_id": "CAD-UP-SAR-002", "max_stops": 8})   # built, never synced
    s = mgr.get("/api/ops/summary").get_json()
    t = s["totals"]
    assert s["scope"] == "Sardhana" and t["targeted"] == len(route["stops"]) + 8
    assert t["completed"] == 4 and t["visited"] == 3 and t["not_home"] == 1
    assert t["remaining"] == t["targeted"] - 4
    assert t["stale_pending"] == 8                                   # open doors on a route with no phone contact
    cadres = {c["cadre_id"]: c for c in s["cadres"]}
    assert cadres["CAD-UP-SAR-001"]["status"] == "On route" and cadres["CAD-UP-SAR-002"]["status"] == "Not started"
    assert cadres["CAD-UP-SAR-003"]["status"] == "No route"
    assert s["consent"]["households_visited"] == 3
    assert 0 <= s["consent"]["rate"] <= 100
    assert s["consent"]["captured_today"] == (1 if consent_captured else 0)
    assert {b["booth_id"] for b in s["booths"]} == {"UP-SAR-001", "UP-SAR-002", "UP-SAR-003"}
    assert sum(h["completed"] for h in s["hourly"]) <= 4


def test_csv_and_pdf_exports(make_client):
    _work_a_route(make_client, "cad-up-sar-001")
    mgr = make_client("mgr.sardhana")
    mgr.post("/api/directives", json={"text": "आज पहली बार के मतदाताओं पर ध्यान दें"})
    r = mgr.get("/api/ops/summary.csv")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    text = r.data.decode("utf-8")
    assert text.startswith("﻿Date,Booth,") and "TOTAL" in text and "पहली बार" in text
    assert "Sunita" not in text and "+91" not in text                # aggregates only
    p = mgr.get("/api/ops/summary.pdf")
    assert p.status_code == 200 and p.data[:4] == b"%PDF" and len(p.data) > 2000


def test_ops_scope_rules(make_client, admin):
    assert admin.get("/api/ops/summary").status_code == 400          # strategist must pick a state
    assert admin.get("/api/ops/summary?state=GA").get_json()["scope"] == "Goa"
    mgr = make_client("mgr.sardhana")
    assert mgr.get("/api/ops/summary?booth_id=UP-MC-001").status_code == 403
    assert mgr.get("/api/ops/summary?booth_id=UP-SAR-002").get_json()["booths"][0]["booth_id"] == "UP-SAR-002"
    assert make_client("cad-up-sar-001").get("/api/ops/summary").status_code == 403
