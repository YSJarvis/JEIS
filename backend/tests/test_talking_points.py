import json

import pytest

from app import talking_points
from app.llm import LLMError


class FakeProvider:
    name = "fake:test-model"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, system, user, max_tokens=900):
        self.calls.append((system, user))
        if self.error:
            raise LLMError(self.error)
        return self.reply


@pytest.fixture()
def cadre_route(make_client):
    cad = make_client("cad-up-sar-001")
    route = cad.post("/api/cadre/route", json={"max_stops": 10}).get_json()
    return cad, route


def test_offline_points_use_directives_and_knowledge(make_client, monkeypatch):
    monkeypatch.setattr(talking_points, "get_provider", lambda: None)
    mgr = make_client("mgr.sardhana")
    d = mgr.post("/api/directives", json={"text": "Highlight yesterday's sugarcane price announcement", "state": "PB"}).get_json()
    assert d["state"] == "UP" and d["constituency"] == "Sardhana"            # managers are pinned to their area
    cad = make_client("cad-up-sar-001")
    route = cad.post("/api/cadre/route", json={"max_stops": 10}).get_json()
    assert route["directives"][0]["text"].startswith("Highlight yesterday")
    stop = route["stops"][0]
    assert stop["talking_points"][0]["topic"] == "Today’s focus"
    r = cad.post("/api/cadre/generate-talking-points", json={"stop_id": stop["stop_id"]}).get_json()
    assert r["generator"] == "offline" and r["live"] is False
    assert r["points"][0]["sources"] == [f"directive-{d['directive_id']}"]
    kb_ids = {s["id"] for s in r["sources"] if s["kind"] != "directive"}
    assert kb_ids, "at least one knowledge-base brief should be cited"


def test_prompt_is_anonymised(cadre_route, monkeypatch, app_db):
    cad, route = cadre_route
    fake = FakeProvider(reply=json.dumps({"points": [
        {"topic": "Ration", "point": "Ask whether the full ration arrives each month.", "reason": "Ration card holder", "sources": ["scheme-ration"]},
        {"topic": "Booth", "point": "Confirm everyone knows their polling booth.", "reason": "Turnout", "sources": ["turnout-check-name"]}]}))
    monkeypatch.setattr(talking_points, "get_provider", lambda: fake)
    stop = route["stops"][0]
    cad.post("/api/cadre/generate-talking-points", json={"household_id": stop["household_id"]})
    system, user = fake.calls[0]
    for m in stop["members"]:
        assert m["name"] not in user and m["voter_id"] not in user
        assert m["community_group"] not in user
        if m.get("phone"):
            assert m["phone"][-6:] not in user
    payload = json.loads(user.split("\n\n", 1)[1])
    assert payload["members"][0]["ref"] == "A" and "age_band" in payload["members"][0]
    assert "caste" in system.lower()          # the rules forbid caste/religion appeals


def test_live_provider_result_is_guarded_and_cached(cadre_route, monkeypatch):
    cad, route = cadre_route
    fake = FakeProvider(reply="Sure! " + json.dumps({"points": [
        {"topic": "Ration", "point": "Ask whether the full ration arrives each month.", "reason": "Ration card", "sources": ["scheme-ration", "made-up-doc"]},
        {"topic": "Booth", "point": "Confirm everyone knows their polling booth and serial number.", "reason": "Turnout", "sources": []},
        {"topic": "Community", "point": "Remind them our party stands with their caste.", "reason": "x", "sources": []},
        {"topic": "Gift", "point": "Mention the gift hampers coming next week.", "reason": "x", "sources": []}]}))
    monkeypatch.setattr(talking_points, "get_provider", lambda: fake)
    hid = route["stops"][1]["household_id"]
    r = cad.post("/api/cadre/generate-talking-points", json={"household_id": hid, "language": "hi"}).get_json()
    assert r["generator"] == "fake:test-model" and r["live"] and r["language"] == "hi"
    assert len(r["points"]) == 2 and "2 suggestion(s) left out" in r["note"]
    assert all("made-up-doc" not in p["sources"] for p in r["points"])
    again = cad.post("/api/cadre/generate-talking-points", json={"household_id": hid, "language": "hi"}).get_json()
    assert again["cached"] is True and len(fake.calls) == 1
    cad.post("/api/cadre/generate-talking-points", json={"household_id": hid, "language": "hi", "refresh": True})
    assert len(fake.calls) == 2


@pytest.mark.parametrize("fake", [
    FakeProvider(error="HTTP 401: invalid x-api-key"),
    FakeProvider(reply="I can't help with that."),
    FakeProvider(reply=json.dumps({"points": [{"topic": "x", "point": "Vote for us because of your religion.", "sources": []}]})),
])
def test_falls_back_to_offline(cadre_route, monkeypatch, fake):
    cad, route = cadre_route
    monkeypatch.setattr(talking_points, "get_provider", lambda: fake)
    r = cad.post("/api/cadre/generate-talking-points", json={"household_id": route["stops"][2]["household_id"]}).get_json()
    assert r["generator"] == "offline" and r["points"] and "AI unavailable" in r["note"]


def test_access_rules(make_client, cadre_route):
    cad, route = cadre_route
    other = make_client("cad-up-sar-002")
    assert other.post("/api/cadre/generate-talking-points",
                      json={"household_id": route["stops"][0]["household_id"]}).status_code == 403
    mgr = make_client("mgr.sardhana")
    assert mgr.post("/api/talking-points/preview", json={"household_id": route["stops"][0]["household_id"]}).status_code == 200
    far = make_client("mgr.margao")
    assert far.post("/api/talking-points/preview", json={"household_id": route["stops"][0]["household_id"]}).status_code == 403


def test_directive_scoping_and_validation(make_client, admin):
    admin.post("/api/directives", json={"text": "Statewide: focus on first-time voters", "state": "UP"})
    admin.post("/api/directives", json={"text": "Goa only", "state": "GA"})
    mgr = make_client("mgr.sardhana")
    texts = [d["text"] for d in mgr.get("/api/directives").get_json()]
    assert "Statewide: focus on first-time voters" in texts and "Goa only" not in texts
    assert mgr.post("/api/directives", json={"text": "x" * 300}).status_code == 400
    goa = admin.get("/api/directives").get_json()
    gid = next(d["directive_id"] for d in goa if d["text"] == "Goa only")
    assert mgr.patch(f"/api/directives/{gid}", json={"active": False}).status_code == 403


def test_knowledge_base_editing(make_client, admin):
    mgr = make_client("mgr.sardhana")
    doc = mgr.post("/api/knowledge", json={"title": "Drain repair on Mill Road", "body": "Municipal work order issued; residents report waterlogging.",
                                           "ask": "Ask whether waterlogging on Mill Road has improved.", "state": "GA"}).get_json()
    assert doc["state"] == "UP" and doc["constituency"] == "Sardhana" and doc["kind"] == "brief"
    assert mgr.patch(f"/api/knowledge/{doc['doc_id']}", json={"active": 0}).status_code == 403
    assert admin.patch(f"/api/knowledge/{doc['doc_id']}", json={"active": 0}).get_json()["active"] == 0
