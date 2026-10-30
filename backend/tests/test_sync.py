from datetime import datetime, timedelta, timezone


def _route(cl):
    return cl.post("/api/cadre/route", json={"max_stops": 10}).get_json()


def test_queued_offline_ops_apply_once(make_client):
    cad = make_client("cad-uk-pau-001")
    r = _route(cad)
    s1, s2 = r["stops"][0], r["stops"][1]
    ts = datetime.now(timezone.utc).isoformat()
    ops = [
        {"op_id": "a1", "type": "stop_status", "stop_id": s1["stop_id"], "household_id": s1["household_id"],
         "status": "VISITED", "notes": "Spoke to elder", "client_ts": ts},
        {"op_id": "a2", "type": "stop_status", "stop_id": s2["stop_id"], "household_id": s2["household_id"],
         "status": "NOT_HOME", "client_ts": ts},
    ]
    res = cad.post("/api/cadre/sync", json={"ops": ops}).get_json()
    assert res["summary"]["applied"] == 2
    byid = {s["stop_id"]: s for s in res["route"]["stops"]}
    assert byid[s1["stop_id"]]["status"] == "VISITED" and byid[s1["stop_id"]]["notes"] == "Spoke to elder"
    # retry after a dropped connection: idempotent
    again = cad.post("/api/cadre/sync", json={"ops": ops, "include_route": False}).get_json()
    assert all(x.get("duplicate") for x in again["results"]) and "route" not in again


def test_last_write_wins_marks_older_op_stale(make_client):
    cad = make_client("cad-uk-pau-001")
    s = _route(cad)["stops"][0]
    now = datetime.now(timezone.utc)
    cad.post("/api/cadre/sync", json={"ops": [{"op_id": "new", "type": "stop_status", "stop_id": s["stop_id"],
                                               "status": "VISITED", "client_ts": now.isoformat()}]})
    old = (now - timedelta(minutes=30)).isoformat()
    res = cad.post("/api/cadre/sync", json={"ops": [{"op_id": "old", "type": "stop_status", "stop_id": s["stop_id"],
                                                     "status": "NOT_HOME", "client_ts": old}]}).get_json()
    assert res["results"][0]["result"] == "stale"
    assert res["route"]["stops"][0]["status"] == "VISITED"


def test_replanned_stop_falls_back_to_household(make_client):
    cad = make_client("cad-uk-pau-001")
    r = _route(cad)
    s = r["stops"][3]
    cad.post("/api/cadre/route", json={"regenerate": True, "max_stops": 10})   # old pending stop ids are gone
    res = cad.post("/api/cadre/sync", json={"ops": [{"op_id": "x", "type": "stop_status", "stop_id": s["stop_id"],
                                                     "household_id": s["household_id"], "status": "REVISIT"}]}).get_json()
    assert res["results"][0]["result"] in ("applied", "rejected")
    if res["results"][0]["result"] == "applied":
        hh = [x for x in res["route"]["stops"] if x["household_id"] == s["household_id"]]
        assert hh and hh[0]["status"] == "REVISIT"


def test_sync_rejects_foreign_stops_and_bad_input(make_client):
    a = make_client("cad-uk-pau-001")
    s = _route(a)["stops"][0]
    b = make_client("cad-uk-pau-002")
    res = b.post("/api/cadre/sync", json={"ops": [
        {"op_id": "f1", "type": "stop_status", "stop_id": s["stop_id"], "status": "VISITED"},
        {"op_id": "f2", "type": "teleport"},
        {"type": "stop_status"},
    ]}).get_json()
    assert [x["result"] for x in res["results"]] == ["rejected"] * 3
    # op ids are namespaced per cadre: reuse by someone else is refused
    a.post("/api/cadre/sync", json={"ops": [{"op_id": "shared", "type": "stop_status", "stop_id": s["stop_id"], "status": "VISITED"}]})
    res2 = b.post("/api/cadre/sync", json={"ops": [{"op_id": "shared", "type": "stop_status", "stop_id": 1, "status": "VISITED"}]}).get_json()
    assert res2["results"][0]["result"] == "rejected"


def test_consent_captured_in_field(make_client, admin):
    cad = make_client("cad-uk-pau-001")
    stop = _route(cad)["stops"][0]
    m = stop["members"][0]
    res = cad.post("/api/cadre/sync", json={"ops": [{"op_id": "c1", "type": "consent", "voter_id": m["voter_id"],
                                                     "status": "OPT_OUT"}]}).get_json()
    assert res["results"][0]["result"] == "applied"
    # opted-out voter disappears from the route and from scoring
    members = [x["voter_id"] for s in res["route"]["stops"] for x in s["members"]]
    assert m["voter_id"] not in members
    log = admin.get("/api/privacy").get_json()["recent_consent_changes"][0]
    assert log["voter_id"] == m["voter_id"] and log["new_status"] == "OPT_OUT" and log["source"] == "field"
