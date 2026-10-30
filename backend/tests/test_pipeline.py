import io
import json

from app.config import STATES
from app.routing import order_stops, _path_len


def test_mock_data_covers_all_states(client):
    meta = client.get("/api/meta").get_json()
    assert {s["code"] for s in meta["states"]} == set(STATES)
    assert all(s["voters"] > 1500 for s in meta["states"])


def test_scores_in_range_and_quadrants_present(client):
    for st in STATES:
        d = client.get(f"/api/dashboard?state={st}").get_json()
        assert 0 <= d["totals"]["avg_swing"] <= 100 and 0 <= d["totals"]["avg_turnout"] <= 100
        assert set(d["quadrants"]) == {"PERSUADE", "GOTV", "OPPOSITION", "LOW"}
        assert all(0 <= p["x"] <= 100 and 0 <= p["y"] <= 100 for p in d["scatter"])


def test_voter_detail_has_explainable_drivers(client):
    v = client.get("/api/voters?state=UP&quadrant=PERSUADE&limit=1").get_json()["items"][0]
    assert v["phone"].startswith("••")  # masked in list views
    d = client.get(f"/api/voters/{v['voter_id']}").get_json()
    assert {"scheme", "neutrality"} <= set(d["drivers"]["swing"])


def test_route_generation_and_status_update(make_client):
    cadre = make_client("cad-pb-san-001")
    r = cadre.post("/api/cadre/route", json={"max_stops": 15}).get_json()
    assert 0 < len(r["stops"]) <= 15
    assert [s["seq"] for s in r["stops"]] == list(range(1, len(r["stops"]) + 1))
    assert all(s["talking_points"] for s in r["stops"])
    stop = r["stops"][0]
    upd = cadre.patch(f"/api/stops/{stop['stop_id']}", json={"status": "VISITED", "notes": "Met family"}).get_json()
    assert upd["result"] == "applied"
    # rebuilding keeps worked stops and never re-queues a visited household
    r2 = cadre.post("/api/cadre/route", json={"regenerate": True, "max_stops": 15}).get_json()
    assert r2["stops"][0]["household_id"] == stop["household_id"] and r2["stops"][0]["status"] == "VISITED"
    pending = [s["household_id"] for s in r2["stops"] if s["status"] == "PENDING"]
    assert stop["household_id"] not in pending and len(pending) == 15
    assert len(set(s["household_id"] for s in r2["stops"])) == len(r2["stops"])
    assert cadre.patch(f"/api/stops/{stop['stop_id']}", json={"status": "BAD"}).status_code == 400


def test_two_opt_never_worse_than_input_order():
    start = (30.0, 75.0)
    pts = [(30.0 + (i % 5) * 0.003, 75.0 + (i // 5) * 0.004) for i in range(20)]
    assert _path_len(start, pts, order_stops(start, pts)) <= _path_len(start, pts, list(range(20))) + 1e-9


def test_csv_ingestion_with_aliases_and_rejections(client):
    csv = (
        "EPIC,Voter Name,Age,Sex,Part No,State,Caste,Schemes,Voting History,Lean,NRI,Mobile,Latitude,Longitude,Ward Notes\n"
        "XYZ0000001,Asha Devi,44,F,UP-NEW-001,Uttar Pradesh,OBC,PM-KISAN;PMAY,80%,neutral,no,+91 9000000001,29.10,77.60,near temple\n"
        "XYZ0000002,Ravi Kumar,23,M,UP-NEW-001,UP,SC,,40,supporter,yes,+91 9000000002,,,\n"
        "XYZ0000003,Too Young,15,M,UP-NEW-001,UP,SC,,40,,,,29.1,77.6,\n"
        ",No Id,40,M,UP-NEW-001,UP,SC,,40,,,,29.1,77.6,\n"
    )
    rep = client.post("/api/ingest/csv", data={"file": (io.BytesIO(csv.encode()), "v.csv")},
                      content_type="multipart/form-data").get_json()
    assert rep["accepted"] == 2 and rep["rejected"] == 2 and rep["booths_created"] == 1
    v = client.get("/api/voters/XYZ0000001").get_json()
    assert v["scheme_types"] == "PM-KISAN,PMAY" and v["voting_history_index"] == 0.8
    assert v["attributes"]["ward_notes"] == "near temple"
    v2 = client.get("/api/voters/XYZ0000002").get_json()
    assert v2["attributes"]["geo_fallback"] == "booth" and v2["party_affinity"] == 0.7


def test_json_ingestion_replace_mode(client):
    payload = {"state": "GA", "mode": "replace", "voters": [
        {"voter_id": "GOA1", "name": "Test", "age": 30, "booth_id": "GA-X-1", "lat": 15.28, "lng": 73.96}]}
    rep = client.post("/api/ingest/json", data=json.dumps(payload), content_type="application/json").get_json()
    assert rep["accepted"] == 1
    meta = {s["code"]: s["voters"] for s in client.get("/api/meta").get_json()["states"]}
    assert meta["GA"] == 1 and meta["UP"] > 1500
