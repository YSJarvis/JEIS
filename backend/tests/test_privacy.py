import io

from app.privacy import mask_address, mask_name


def test_mask_helpers():
    assert mask_name("Sunita Devi") == "S. D."
    assert mask_address("H.No. 370, Rohta") == "H.No. •••, Rohta"
    assert mask_address("Rohta") == "Rohta"


def test_masking_toggle_changes_dashboard_views(admin):
    on = admin.get("/api/voters?state=UP&limit=5").get_json()
    assert on["pii_masked"] is True
    v = on["items"][0]
    assert v["name"].endswith(".") and v["phone"] in (None, "•••••• ••••") and "•••" in v["address"]

    r = admin.patch("/api/privacy", json={"pii_masking": False}).get_json()
    assert r["pii_masking"] is False
    off = admin.get("/api/voters?state=UP&limit=5").get_json()["items"][0]
    assert not off["name"].endswith(".") and "•••" not in off["address"]
    assert off["phone"] is None or off["phone"].startswith("••••••")   # never a full number in dashboards

    audit = admin.get("/api/admin/audit").get_json()
    assert audit[0]["action"] == "pii_masking_changed"


def test_name_search_disabled_while_masked(admin):
    name = admin.get("/api/voters?state=UP&limit=1").get_json()["items"][0]
    # masked: searching by a real name returns nothing
    admin.patch("/api/privacy", json={"pii_masking": False})
    real = admin.get("/api/voters?state=UP&limit=1").get_json()["items"][0]["name"]
    admin.patch("/api/privacy", json={"pii_masking": True})
    assert admin.get(f"/api/voters?state=UP&q={real.split()[0]}").get_json()["total"] == 0
    assert admin.get(f"/api/voters?state=UP&q={name['voter_id']}").get_json()["total"] == 1


def test_cadre_essential_view_respects_consent(make_client, admin):
    cad = make_client("cad-ga-mar-001")
    r = cad.post("/api/cadre/route", json={"max_stops": 15}).get_json()
    members = [m for s in r["stops"] for m in s["members"]]
    assert members and all(not m["name"].endswith(".") for m in members)          # full names on own route
    assert all(m["phone"] for m in members if m["consent_status"] == "GRANTED")
    assert all(m["phone"] is None for m in members if m["consent_status"] != "GRANTED")
    assert not any(m["consent_status"] == "OPT_OUT" for m in members)


def test_opt_out_erases_phone_and_scores(admin):
    vid = admin.get("/api/voters?state=PB&consent=GRANTED&limit=1").get_json()["items"][0]["voter_id"]
    r = admin.patch(f"/api/voters/{vid}/consent", json={"consent_status": "opt-out"}).get_json()
    assert r["consent_status"] == "OPT_OUT" and r["previous"] == "GRANTED"
    d = admin.get(f"/api/voters/{vid}").get_json()
    assert d["phone"] is None and d["swing_score"] is None
    assert d["consent_log"][0]["new_status"] == "OPT_OUT"
    assert admin.get(f"/api/voters?state=PB&q={vid}").get_json()["total"] == 0


def test_import_maps_consent_and_preserves_it_when_absent(admin):
    csv = ("EPIC,Name,Age,Part No,State,Consent,Latitude,Longitude\n"
           "CNS0000001,Test One,40,UP-NEW-9,UP,Yes,29.1,77.6\n"
           "CNS0000002,Test Two,41,UP-NEW-9,UP,withdrawn,29.1,77.6\n")
    rep = admin.post("/api/ingest/csv", data={"file": (io.BytesIO(csv.encode()), "c.csv")},
                     content_type="multipart/form-data").get_json()
    assert rep["accepted"] == 2
    assert admin.get("/api/voters/CNS0000001").get_json()["consent_status"] == "GRANTED"
    assert admin.get("/api/voters/CNS0000002").get_json()["consent_status"] == "OPT_OUT"
    # re-import without a consent column keeps the recorded status
    csv2 = "EPIC,Name,Age,Part No,State,Latitude,Longitude\nCNS0000001,Test One,42,UP-NEW-9,UP,29.1,77.6\n"
    admin.post("/api/ingest/csv", data={"file": (io.BytesIO(csv2.encode()), "c2.csv")}, content_type="multipart/form-data")
    v = admin.get("/api/voters/CNS0000001").get_json()
    assert v["consent_status"] == "GRANTED" and v["age"] == 42
    sources = {x["source"] for x in v["consent_log"]}
    assert sources == {"import"}
