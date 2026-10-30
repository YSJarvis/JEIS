from conftest import wait_job


def test_config_describes_defaults(admin):
    cfg = admin.get("/api/scoring/config").get_json()
    up = cfg["states"]["UP"]
    assert set(up["current"]["swing_weights"]) == {"neutrality", "scheme", "age", "community"}
    assert "dera" in cfg["states"]["PB"]["current"]["swing_weights"]
    assert cfg["labels"]["swing"]["scheme"] == "Welfare scheme reliance"


def test_recalibrate_runs_in_background_and_changes_scores(admin):
    before = admin.get("/api/dashboard?state=UP").get_json()["quadrants"]
    r = admin.post("/api/scoring/recalibrate", json={
        "state": "UP", "swing_weights": {"neutrality": 20, "scheme": 60, "age": 10, "community": 10}})
    assert r.status_code == 202
    body = r.get_json()
    assert body["changes"]["UP"]["swing_weights"]["scheme"] == 0.6      # stored as shares
    job = wait_job(admin, body["job"]["job_id"])
    assert job["status"] == "DONE" and job["scope"] == "UP"
    assert set(job["result"]["before"]) == {"UP"} and job["result"]["model_version"].endswith("+cfg1")
    after = admin.get("/api/dashboard?state=UP").get_json()["quadrants"]
    assert after != before
    cfg = admin.get("/api/scoring/config").get_json()["states"]["UP"]
    assert cfg["customised"] and cfg["updated"]["updated_by"] == "U-admin"
    assert admin.get("/api/admin/audit").get_json()[0]["action"] == "scoring_config_changed"


def test_recalibrate_validates_and_resets(admin):
    bad = admin.post("/api/scoring/recalibrate", json={"state": "UP", "swing_weights": {"dera": 50}})
    assert bad.status_code == 400 and "unknown features" in bad.get_json()["error"]
    zero = admin.post("/api/scoring/recalibrate", json={"state": "GA", "turnout_weights": {"history": 0, "booth": 0, "age": 0, "local": 0}})
    assert zero.status_code == 400
    j = admin.post("/api/scoring/recalibrate", json={"state": "GA", "swing_weights": {"family": 90, "neutrality": 10}}).get_json()
    wait_job(admin, j["job"]["job_id"])
    j2 = admin.post("/api/scoring/recalibrate", json={"state": "GA", "reset": True}).get_json()
    wait_job(admin, j2["job"]["job_id"])
    assert admin.get("/api/scoring/config").get_json()["states"]["GA"]["customised"] is False


def test_threshold_change_rescores_all_states(admin):
    j = admin.post("/api/scoring/recalibrate", json={"thresholds": {"swing_high": 70}}).get_json()
    job = wait_job(admin, j["job"]["job_id"])
    assert job["scope"] == "ALL" and set(job["result"]["after"]) == {"UP", "PB", "UK", "GA"}
    assert admin.get("/api/meta").get_json()["thresholds"]["swing_high"] == 70
