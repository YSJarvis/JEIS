import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.warroom import counter as wr_counter
from app.warroom import engine as wr

from test_copilot import FakeLLM


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(wr_counter, "get_provider", lambda: None)
    monkeypatch.setattr(wr, "_last_rescore", [0.0])


def ago(h):
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat(timespec="seconds")


def top(cl, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return cl.get(f"/api/warroom/threats?{q}").get_json()["clusters"]


def cane(cl):
    return next(c for c in top(cl) if c["issue"] == "Delayed cane payments")


# --- ingestion -----------------------------------------------------------

def test_sample_feed_is_seeded_and_ranked(admin):
    cs = top(admin)
    assert len(cs) >= 5
    assert cs == sorted(cs, key=lambda c: -c["threat_score"])
    c = cs[0]
    assert c["issue"] == "Delayed cane payments" and c["state"] == "UP" and c["severity"] in ("Critical", "High")
    assert c["item_count"] == 8 and set(c["source_mix"]) >= {"TV", "Social", "Local print", "YouTube"}
    assert len(c["trend"]) == 12 and set(c["breakdown"]) >= {"volume", "negativity", "reach", "state_significance"}
    pb = next(x for x in cs if x["state"] == "PB")
    assert pb["issue"] == "Slow mandi procurement and payments"
    meta = admin.get("/api/warroom/meta").get_json()
    assert meta["media"]["sample_items"] == meta["media"]["items"] == 22 and meta["can_simulate"]
    assert meta["mediums"]["whatsapp"] == "WhatsApp broadcast copy"


def test_ingest_joins_cluster_scrubs_and_dedupes(admin):
    before = cane(admin)
    item = {"text": "Farmers at the Daurala mill say cane dues are still pending; call 98765 43210 or mail kisan@example.com",
            "state": "UP", "constituency": "Sardhana", "source_type": "Local Print", "channel": "Test Daily", "timestamp": ago(0.5)}
    r = admin.post("/api/warroom/ingest-media", json=item).get_json()
    res = r["results"][0]
    assert res["status"] == "ingested" and res["cluster_id"] == before["cluster_id"] and not res["new_cluster"]
    assert res["source_type"] == "print" and res["constituency_id"] == "UP-SAR" and res["sentiment"] == "negative"
    assert res["threat_score"] >= before["threat_score"]
    media = admin.get(f"/api/warroom/media?cluster_id={res['cluster_id']}&q=Test%20Daily").get_json()["items"][0]
    assert "98765" not in media["text"] and "@example.com" not in media["text"] and "[phone removed]" in media["text"]
    again = admin.post("/api/warroom/ingest-media", json=item).get_json()
    assert again["results"][0]["status"] == "duplicate" and again["summary"]["duplicate"] == 1


def test_new_narrative_and_validation(admin):
    r = admin.post("/api/warroom/ingest-media", json={"items": [
        {"text": "Students protest after the police recruitment exam paper leak in Meerut; exam cancelled again.",
         "state": "UP", "constituency": "Sardhana", "source_type": "tv", "channel": "Test TV", "timestamp": ago(1)},
        {"text": "too short", "source_type": "tv", "channel": "x"},
        {"text": "A long enough text about something happening somewhere", "source_type": "radio", "channel": "x"},
        {"text": "A long enough text about something happening somewhere", "source_type": "tv"},
    ]}).get_json()
    st = [x["status"] for x in r["results"]]
    assert st == ["ingested", "error", "error", "error"] and r["summary"]["new_clusters"] == 1
    assert r["results"][0]["issue"] == "Exam disruption"
    assert "source_type" in r["results"][2]["error"] and "channel" in r["results"][3]["error"]


def test_csv_upload_with_defaults(admin):
    csv = ("text,channel,timestamp,views\n"
           "\"Fatorda residents say water supply cut for the fourth day, tankers late again\",Fatorda Live,%s,4000\n"
           "\"Pipeline work in Margao stalled, water supply irregular in Borda ward\",Salcete Daily,%s,9000\n") % (ago(2), ago(3))
    r = admin.post("/api/warroom/ingest-media", data={"file": [(io.BytesIO(csv.encode()), "goa.csv")],
                                                      "meta": json.dumps({"state": "GA", "constituency": "Margao", "source_type": "social"})},
                   content_type="multipart/form-data").get_json()
    assert r["summary"]["ingested"] == 2
    assert {x["cluster_label"].split(":")[0] for x in r["results"]} == {"Irregular water supply"}
    assert all(x["state"] == "GA" for x in r["results"])


def test_manager_scope(make_client):
    m = make_client("mgr.sardhana")
    cs = top(m)
    assert cs and all("Sardhana" in c["constituencies"] for c in cs)
    assert not any(c["state"] == "PB" for c in cs)
    pb = [c for c in top(make_client("admin"), state="PB")]
    assert m.get(f"/api/warroom/threats/{pb[0]['cluster_id']}").status_code == 404
    bad = m.post("/api/warroom/ingest-media", json={"text": "Paddy lifting slow at Sangrur mandi, farmers upset with delays",
                                                    "state": "PB", "constituency": "Sangrur", "source_type": "social", "channel": "x"}).get_json()
    assert bad["results"][0]["status"] == "error"
    ok = m.post("/api/warroom/ingest-media", json={"text": "Waterlogging again in Jani Khurd lanes after the evening rain, drains overflow",
                                                   "source_type": "social", "channel": "Manager test"}).get_json()["results"][0]
    assert ok["status"] == "ingested" and ok["constituency_id"] == "UP-SAR"
    assert all(i["constituency"] == "Sardhana" for i in m.get("/api/warroom/media").get_json()["items"])
    assert m.patch(f"/api/warroom/threats/{cs[0]['cluster_id']}", json={"status": "resolved"}).status_code == 403
    assert m.post("/api/warroom/sample-feed", json={"simulate": 2}).status_code == 403
    cad = make_client("cad-up-sar-001")
    for url in ("/api/warroom/threats", "/api/warroom/media", "/api/warroom/meta"):
        assert cad.get(url).status_code == 403
    assert cad.post("/api/warroom/generate-counter", json={"narrative_cluster_id": cs[0]["cluster_id"]}).status_code == 403


def test_filters(admin):
    assert {c["state"] for c in top(admin, state="PB,GA")} == {"PB", "GA"}
    crit = top(admin, severity="critical")
    assert crit and all(c["severity"] == "Critical" for c in crit)
    m = admin.get("/api/warroom/media?source_type=tv&state=UP").get_json()
    assert m["total"] >= 2 and all(i["source_type"] == "tv" and i["state"] == "UP" for i in m["items"])
    assert all(i["severity"] for i in admin.get("/api/warroom/media?severity=Critical").get_json()["items"])
    csv = admin.get("/api/warroom/media?format=csv&state=GA")
    assert csv.status_code == 200 and csv.data.decode("utf-8-sig").startswith("published_at,state")


def test_threat_score_components():
    now = datetime.now(timezone.utc)

    def items(n, hours, src="tv", neg=0.8, reach=100000):
        return [{"published_at": (now - timedelta(hours=hours)).isoformat(), "negativity": neg, "source_type": src,
                 "reach": reach, "channel": f"c{i}", "constituency_id": "UP-SAR"} for i in range(n)]
    hot = wr.threat(items(10, 2), "UP", now)
    cold = wr.threat(items(10, 200), "UP", now)
    calm = wr.threat(items(10, 2, neg=0.1), "UP", now)
    small = wr.threat(items(10, 2), "GA", now)
    assert hot["score"] > cold["score"] and hot["score"] > calm["score"] and hot["score"] > small["score"]
    assert 1 <= cold["score"] <= 100 and hot["severity"] in ("Critical", "High")
    assert wr.score_sentiment("Farmers protest delayed dues, anger grows")[1] == "negative"
    assert wr.score_sentiment("Mill cleared all dues, farmers welcomed the relief")[1] != "negative"


def test_narrative_label_and_status(admin):
    c = cane(admin)
    r = admin.patch(f"/api/warroom/threats/{c['cluster_id']}", json={"label": "Sugarcane Mill Dues Delay"}).get_json()
    assert r["label"] == "Sugarcane Mill Dues Delay" and r["label_source"] == "manual" and len(r["items"]) == 8
    admin.post("/api/warroom/ingest-media", json={"text": "Cane dues still pending at Daurala mill, farmers to march on Monday",
                                                  "state": "UP", "constituency": "Sardhana", "source_type": "social", "channel": "X"})
    assert cane(admin)["label"] == "Sugarcane Mill Dues Delay"          # manual label survives new items
    admin.patch(f"/api/warroom/threats/{c['cluster_id']}", json={"status": "resolved"})
    assert c["cluster_id"] not in [x["cluster_id"] for x in top(admin)]
    assert c["cluster_id"] in [x["cluster_id"] for x in top(admin, status="resolved")]
    assert admin.patch(f"/api/warroom/threats/{c['cluster_id']}", json={"status": "gone"}).status_code == 400


# --- counter-responses ---------------------------------------------------

@pytest.mark.parametrize("medium", ["TV Spokesperson Brief", "WhatsApp Broadcast Copy", "Official Press Release", "Digital Ad Text"])
def test_offline_counter_all_mediums(admin, medium):
    c = cane(admin)
    r = admin.post("/api/warroom/generate-counter", json={"narrative_cluster_id": c["cluster_id"], "target_medium": medium,
                                                          "party_name": "Jan Morcha"})
    assert r.status_code == 200, r.get_json()
    p = r.get_json()
    assert p["generator"] == "offline" and p["status"] == "draft"
    assert p["threat_summary"] and p["core_claim"] and p["assessment"]["verdict"] == "supported"
    assert p["counter_facts"] and all(f["sources"] for f in p["counter_facts"])
    used = {s["id"] for s in p["sources"] if s["used"]}
    assert all(set(f["sources"]) <= used for f in p["counter_facts"])
    assert p["narrative"] and all(n["id"].startswith("M") for n in p["narrative"])
    assert p["safety_check"]["status"] in ("clear", "clear_with_fixes")
    rules = {x["rule"] for x in p["safety_check"]["checks"]}
    assert {"No personal attacks or private-life remarks", "No appeals to caste, religion, community or language",
            "48-hour silence period"} <= rules
    copy = p["copy"]
    if p["medium"] == "whatsapp":
        assert "\n" in copy["text"] and len(copy["text"]) <= 700 and "[S" not in copy["text"]
    if p["medium"] == "digital_ad":
        assert copy["variants"] and all(len(v["headline"]) <= 40 and len(v["body"]) <= 125 for v in copy["variants"])
        assert any(x["rule"] == "Pre-certification" and x["status"] == "action" for x in p["safety_check"]["checks"])
    if p["medium"] == "tv_brief":
        assert copy["talking_points"] and copy["likely_questions"]
    if p["medium"] == "press_release":
        assert copy["headline"].startswith("Jan Morcha") and copy["body"]


def test_counter_without_sources_is_blocked(admin):
    r = admin.post("/api/warroom/ingest-media", json={
        "text": "Students protest after the police recruitment exam paper leak in Meerut; exam cancelled again.",
        "state": "UP", "constituency": "Sardhana", "source_type": "tv", "channel": "Test TV"}).get_json()["results"][0]
    p = admin.post("/api/warroom/generate-counter", json={"narrative_cluster_id": r["cluster_id"], "target_medium": "whatsapp"}).get_json()
    assert p["safety_check"]["status"] == "blocked" and not p["counter_facts"]
    assert "checking" in p["copy"]["text"]
    a = admin.patch(f"/api/warroom/responses/{p['response_id']}", json={"status": "approved"})
    assert a.status_code == 400


def test_live_counter_is_verified(admin, monkeypatch):
    c = cane(admin)
    reply = json.dumps({
        "threat_summary": "Cane dues delay is the top story across 8 channels.",
        "core_claim": "The government has failed cane farmers; Rs 100 crore unpaid.",
        "assessment": {"verdict": "supported", "explanation": "Our survey confirms delayed payments are the top concern [S1]."},
        "counter_facts": [
            {"fact": "68% of farmers named delayed cane payments as their top concern [S1].", "sources": ["S1"]},
            {"fact": "Dues of Rs 999 crore were cleared last week [S1].", "sources": ["S1"]},
            {"fact": "The opposition leader is a corrupt thief.", "sources": ["S1"]},
            {"fact": "Jat farmers deserve better [S1].", "sources": ["S1"]},
            {"fact": "Viral claim proves it [M1].", "sources": ["M1"]},
        ],
        "copy": {"headline_message": "The cane dues claim is completely false and baseless.",
                 "talking_points": ["We hear the farmers: 68% named cane payments as their top concern [S1].",
                                    "His family runs the mill, look at his private life."],
                 "bridging_lines": ["Let's talk about what we are doing."],
                 "likely_questions": [{"q": "Are dues pending?", "a": "Yes, and we have a plan [S1]."}]},
        "avoid": ["Calling it propaganda"]})
    fake = FakeLLM(reply=reply)
    monkeypatch.setattr(wr_counter, "get_provider", lambda: fake)
    p = admin.post("/api/warroom/generate-counter", json={"narrative_cluster_id": c["cluster_id"], "target_medium": "tv_brief"}).get_json()
    assert p["generator"] == "fake:copilot" and p["live"]
    system, user = fake.calls[0]
    assert "rapid-response" in system and "[M1]" in user and "[S1]" in user and "claims, not evidence" in user
    facts = [f["fact"] for f in p["counter_facts"]]
    assert facts == ["68% of farmers named delayed cane payments as their top concern [S1]."]
    assert "100" in p["core_claim"]                                   # opposition figure allowed when describing their claim
    assert p["copy"]["headline_message"] == ""                         # denial of a documented problem removed
    assert len(p["copy"]["talking_points"]) == 1
    checks = {x["rule"]: x["status"] for x in p["safety_check"]["checks"]}
    assert checks["No personal attacks or private-life remarks"] == "fixed"
    assert checks["No appeals to caste, religion, community or language"] == "fixed"
    assert checks["No denial of documented facts"] == "fixed"
    assert checks["Facts and figures are sourced"] == "fixed"
    assert p["safety_check"]["status"] == "clear_with_fixes"


def test_llm_failure_falls_back(admin, monkeypatch):
    from app.llm import LLMError  # noqa: F401
    monkeypatch.setattr(wr_counter, "get_provider", lambda: FakeLLM(error="HTTP 529: overloaded"))
    p = admin.post("/api/warroom/generate-counter", json={"narrative_cluster_id": cane(admin)["cluster_id"],
                                                          "target_medium": "press_release", "language": "hi"}).get_json()
    assert p["generator"] == "offline" and p["generated_language"] == "en"
    assert any("unavailable" in w for w in p["warnings"]) and any("Hindi" in w for w in p["warnings"])


def test_review_and_exports(admin, make_client):
    c = cane(admin)
    p = admin.post("/api/warroom/generate-counter", json={"narrative_cluster_id": c["cluster_id"], "target_medium": "press_release"}).get_json()
    rid = p["response_id"]
    lst = admin.get(f"/api/warroom/responses?cluster_id={c['cluster_id']}").get_json()
    assert lst[0]["response_id"] == rid and lst[0]["medium_label"] == "Official press release"
    md = admin.get(f"/api/warroom/responses/{rid}.md")
    text = md.data.decode()
    assert md.status_code == 200 and "## 4. ECI / MCC safety check" in text and "[S1]" in text
    pdf = admin.get(f"/api/warroom/responses/{rid}.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    assert make_client("mgr.sardhana").patch(f"/api/warroom/responses/{rid}", json={"status": "approved"}).status_code == 403
    ok = admin.patch(f"/api/warroom/responses/{rid}", json={"status": "approved"}).get_json()
    assert ok["status"] == "approved" and ok["reviewed_by"]
    assert make_client("mgr.sardhana").get(f"/api/warroom/responses/{rid}").get_json()["status"] == "approved"
    assert cane(admin)["approved_responses"] == 1


def test_simulate_and_recluster(admin):
    n = admin.get("/api/warroom/meta").get_json()["media"]["items"]
    r = admin.post("/api/warroom/sample-feed", json={"simulate": 3}).get_json()
    assert r["simulated"] == 3
    assert admin.get("/api/warroom/meta").get_json()["media"]["items"] == n + 3
    before = len(top(admin, status="all"))
    rc = admin.post("/api/warroom/recluster").get_json()
    assert rc["items"] == n + 3 and abs(rc["clusters"] - before) <= 2
    gone = admin.post("/api/warroom/sample-feed", json={"remove": True}).get_json()
    assert gone["removed"] and admin.get("/api/warroom/meta").get_json()["media"]["items"] == 0
    assert top(admin) == []
