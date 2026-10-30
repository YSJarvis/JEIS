import io
import json

from conftest import wait_job

HINDI_CSV = (
    "क्रम संख्या,एपिक संख्या,निर्वाचक का नाम,पिता/पति का नाम,संबंध,उम्र,लिंग,मकान संख्या,भाग संख्या\n"
    "1,ABC1234567,राम कुमार,श्याम लाल,पिता,४५,पुरुष,12,1\n"
    "2,ABC1234568,सीता देवी,राम कुमार,पति,42,महिला,12,1\n"
)

EN_CSV = (
    "Sl No,EPIC No,Elector Name,Father/Husband Name,Sex,Age,House No,Part No\n"
    "1,XYZ0000001,Asha Verma,Mohan Verma,F,34,7/2,45\n"
    "2,XYZ0000002,Ravi Verma,Mohan Verma,M,31,7/2,45\n"
    "3,XYZ0000001,Asha Verma,Mohan Verma,F,34,7/2,45\n"         # duplicate EPIC in file
    "4,,Kamla Devi,Hari Ram,F,61,9,45\n"
    "5,,Kamla Devi,Hari Ram,F,61,9,45\n"                        # duplicate name + house + booth, no EPIC
    "6,XYZ0000006,Too Young,Someone,M,15,10,45\n"               # invalid age
    "7,XYZ0000007,,Someone,M,40,11,45\n"                        # empty name
    "8,BADEPIC,Odd Format,Someone,M,40,12,45\n"                 # non-standard EPIC: warning only
)


def upload(cl, url, text, name="roll.csv", **fields):
    data = {"file": (io.BytesIO(text.encode("utf-8")), name)}
    data.update({k: json.dumps(v) for k, v in fields.items()})
    return cl.post(url, data=data, content_type="multipart/form-data")


def test_hindi_headers_are_auto_mapped(admin):
    r = upload(admin, "/api/import/electoral-roll/preview", HINDI_CSV, options={"state": "UP", "constituency": "Sardhana"}).get_json()
    m, conf = r["suggested"], r["confidence"]
    assert m["epic"] == "एपिक संख्या" and m["name"] == "निर्वाचक का नाम" and m["relative_name"] == "पिता/पति का नाम"
    assert m["age"] == "उम्र" and m["gender"] == "लिंग" and m["house_no"] == "मकान संख्या" and m["booth"] == "भाग संख्या"
    assert all(conf[k] == "high" for k in ("epic", "name", "age", "booth"))
    rep = r["report"]["stats"]
    assert rep["valid"] == 2 and rep["created"] == 2 and r["report"]["dry_run"] is True


def test_preview_is_dry_run_and_reports_issues(admin):
    r = upload(admin, "/api/import/electoral-roll/preview", EN_CSV, options={"state": "UP", "constituency": "Sardhana"}).get_json()
    s = r["report"]["stats"]
    assert s["rows"] == 8 and s["invalid"] == 2 and s["duplicates_in_file"] == 2 and s["nonstandard_epic"] == 1
    assert s["created"] == 4 and s["booths_created"] == 1
    kinds = {(i["row"], i["kind"]) for i in r["report"]["issues"]}
    assert (4, "duplicate") in kinds and (6, "duplicate") in kinds and (7, "error") in kinds and (8, "error") in kinds
    assert r["report"]["new_booths"][0]["booth_id"] == "UP-SAR-045"
    assert admin.get("/api/voters/XYZ0000001").status_code == 404          # nothing written


def test_part_number_needs_constituency(admin):
    r = upload(admin, "/api/import/electoral-roll/preview", EN_CSV, options={"state": "UP"}).get_json()
    assert r["report"]["stats"]["invalid"] == 8
    assert "choose the constituency" in r["report"]["issues"][0]["message"]


def test_commit_creates_voters_households_and_scores(admin):
    r = upload(admin, "/api/import/electoral-roll", EN_CSV, options={"state": "UP", "constituency": "Sardhana"}).get_json()
    s = r["stats"]
    assert s["created"] == 4 and s["households_created"] == 3 and s["booths_created"] == 1 and s["needs_geocoding"] == 1
    job = wait_job(admin, r["scoring"]["job_id"])
    assert job["status"] == "DONE" and job["scope"] == "UP"
    v = admin.get("/api/voters/XYZ0000002").get_json()
    assert v["booth_id"] == "UP-SAR-045" and v["household_id"] == "UP-SAR-045-H-7-2"
    assert v["consent_status"] == "PENDING" and v["swing_score"] is not None
    assert v["attributes"]["relative_name"] == "Mohan Verma" and v["attributes"]["geo_fallback"] == "constituency_centre"
    assert any(x["action"] == "electoral_roll_import" for x in admin.get("/api/admin/audit").get_json())


def test_existing_epic_update_preserves_enrichment(admin):
    v = admin.get("/api/voters?state=UP&booth_id=UP-SAR-001&consent=GRANTED&limit=1").get_json()["items"][0]
    before = admin.get(f"/api/voters/{v['voter_id']}").get_json()
    csv = f"EPIC,Name,Age,Gender,House No,Part No\n{v['voter_id']},Renamed Person,{before['age'] + 1},F,99,1\n"
    r = upload(admin, "/api/import/electoral-roll", csv, options={"state": "UP", "constituency": "Sardhana"}).get_json()
    assert r["stats"]["updated"] == 1 and r["stats"]["created"] == 0
    wait_job(admin, r["scoring"]["job_id"])
    after = admin.get(f"/api/voters/{v['voter_id']}").get_json()
    assert after["age"] == before["age"] + 1 and after["booth_id"] == "UP-SAR-001"
    # roll refresh never wipes survey, scheme or consent data
    for k in ("scheme_types", "party_affinity", "consent_status", "voting_history_index"):
        assert after[k] == before[k], k
    # skip mode leaves the record untouched
    csv2 = f"EPIC,Name,Age,House No,Part No\n{v['voter_id']},Third Name,70,99,1\n"
    r2 = upload(admin, "/api/import/electoral-roll", csv2, options={"state": "UP", "constituency": "Sardhana", "on_existing": "skip"}).get_json()
    assert r2["stats"]["skipped_existing"] == 1


def test_dedup_against_database_and_epic_linking(admin):
    opts = {"state": "PB", "constituency": "Sangrur"}
    no_epic = "Name,Relative,Age,House No,Part No\nGurpreet Sandhu,Harjit Sandhu,40,21,1\n"
    r1 = upload(admin, "/api/import/electoral-roll", no_epic, options=opts).get_json()
    assert r1["stats"]["created"] == 1
    # same person arrives later with an EPIC: linked to the existing record, not duplicated
    with_epic = "EPIC,Name,Relative,Age,House No,Part No\nPBS1234567,Shri Gurpreet Sandhu,Harjit Sandhu,40,21,1\n"
    r2 = upload(admin, "/api/import/electoral-roll", with_epic, options=opts).get_json()
    assert r2["stats"]["linked_epic"] == 1 and r2["stats"]["created"] == 0
    assert admin.get("/api/voters/PBS1234567").status_code == 200
    # a different EPIC for the same name/house/booth is flagged, not inserted
    other = "EPIC,Name,Relative,Age,House No,Part No\nPBS7654321,Gurpreet Sandhu,Harjit Sandhu,40,21,1\n"
    r3 = upload(admin, "/api/import/electoral-roll/preview", other, options=opts).get_json()
    assert r3["report"]["stats"]["possible_duplicates"] == 1 and r3["report"]["stats"]["created"] == 0


def test_json_from_pdf_parser_with_parts(admin):
    payload = {"state": "GA", "ac_name": "Margao", "parts": [
        {"part_no": 7, "electors": [{"serial_no": 1, "epic_no": "GAM0000001", "name": "Maria Fernandes",
                                     "relation_name": "Joseph Fernandes", "age": 52, "gender": "F", "house_no": "3"}]},
        {"part_no": 8, "electors": [{"serial_no": 1, "epic_no": "GAM0000002", "name": "Rohan Naik",
                                     "relation_name": "Anant Naik", "age": 29, "gender": "M", "house_no": "14B"}]}]}
    r = admin.post("/api/import/electoral-roll", json=payload).get_json()
    assert r["stats"]["created"] == 2 and r["stats"]["booths_created"] == 2
    assert admin.get("/api/voters/GAM0000002").get_json()["booth_id"] == "GA-MAR-008"


def test_pdf_upload_explains_conversion(admin):
    r = admin.post("/api/import/electoral-roll/preview", data={"file": (io.BytesIO(b"%PDF-1.7 ..."), "roll.pdf")},
                   content_type="multipart/form-data")
    assert r.status_code == 400 and "convert" in r.get_json()["error"].lower()


def test_manual_mapping_override_and_required_fields(admin):
    csv = "a,b,c,d\nAnil,44,5,3\n"
    r = upload(admin, "/api/import/electoral-roll/preview", csv, options={"state": "UK", "constituency": "Pauri"}).get_json()
    assert r["blocking"]                                     # nothing recognisable: required fields unmapped
    r2 = upload(admin, "/api/import/electoral-roll/preview", csv, options={"state": "UK", "constituency": "Pauri"},
                mapping={"name": "a", "age": "b", "house_no": "c", "booth": "d"}).get_json()
    assert r2["report"]["stats"]["created"] == 1


def test_only_admin_can_import(make_client):
    m = make_client("mgr.sardhana")
    assert upload(m, "/api/import/electoral-roll/preview", EN_CSV).status_code == 403
