import io
import json

import pytest

from app.copilot import generate as cp_gen
from app.copilot import kb as cp_kb
from app.llm import LLMError

TXT = ("Sardhana farmers report delays in tubewell connections. The irrigation department allocated Rs 7.5 crore "
       "for 120 new tubewell connections in 2026-27, but only 18 had been energised by October. Farmers in Kaili say "
       "the crop was hit because canal water did not reach the tail end. Published 14 October 2026.")


def upload(cl, files, meta=None):
    data = {"file": [(io.BytesIO(b), n) for n, b in files]}
    if meta:
        data["meta"] = json.dumps(meta)
    return cl.post("/api/copilot/ingest", data=data, content_type="multipart/form-data")


def make_pdf(pages: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for text in pages:
        y = 800
        for line in [text[i:i + 90] for i in range(0, len(text), 90)]:
            c.drawString(40, y, line); y -= 14
        c.showPage()
    c.save()
    return buf.getvalue()


class FakeLLM:
    name = "fake:copilot"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, system, user, max_tokens=900):
        self.calls.append((system, user))
        if self.error:
            raise LLMError(self.error)
        return self.reply if isinstance(self.reply, str) else self.reply(system, user)


def test_meta_is_scoped(admin, make_client):
    a = admin.get("/api/copilot/meta").get_json()
    assert {c["state"] for c in a["constituencies"]} == {"UP", "PB", "UK", "GA"}
    assert a["stats"]["documents"] >= 12 and a["search"] == "keyword (BM25)"
    m = make_client("mgr.sardhana").get("/api/copilot/meta").get_json()
    assert [c["id"] for c in m["constituencies"]] == ["UP-SAR"]
    assert make_client("cad-up-sar-001").get("/api/copilot/meta").status_code == 403


def test_txt_ingest_detects_metadata_and_dedupes(admin):
    r = upload(admin, [("tubewell_note.txt", TXT.encode())]).get_json()
    d = r["results"][0]
    assert d["status"] == "indexed" and d["state"] == "UP" and d["constituency_id"] == "UP-SAR"
    assert d["topics"][0] == "Agriculture" and d["doc_type"] == "budget" and d["published_on"] == "2026-10-14"
    again = upload(admin, [("copy.txt", TXT.encode())]).get_json()["results"][0]
    assert again["status"] == "duplicate" and again["doc_id"] == d["doc_id"]


def test_pdf_ingest_keeps_pages_for_citations(admin):
    pdf = make_pdf(["Pauri district health review. Uttarakhand.",
                    "In Pauri, the community health centre at Kot has 2 of 6 doctor posts filled and patients travel 40 km for a scan."])
    r = upload(admin, [("pauri_health.pdf", pdf)], {"source_citation": "District health review, Pauri, 2026", "doc_type": "legislative"}).get_json()
    d = r["results"][0]
    assert d["status"] == "indexed" and d["pages"] == 2 and d["constituency_id"] == "UK-PAU" and d["doc_type"] == "legislative"
    q = admin.post("/api/copilot/query", json={"query": "doctor posts health centre scan", "constituency_id": "UK-PAU"}).get_json()
    top = q["results"][0]
    assert top["doc_id"] == d["doc_id"] and top["page"] == 2 and top["citation"].endswith("p. 2")
    assert "[S1]" in q["answer"]["text"]


def test_scanned_pdf_and_bad_files_are_explained(admin):
    blank = make_pdf(["", ""])
    r = upload(admin, [("scan.pdf", blank), ("broken.json", b"{not json"), ("x.bin", b"\x00\x01\x02" * 50)]).get_json()
    errs = [x["error"] for x in r["results"]]
    assert "OCR" in errs[0] and "invalid JSON" in errs[1] and "binary" in errs[2]
    assert r["summary"] == {"indexed": 0, "duplicate": 0, "error": 3}


def test_json_documents_with_explicit_scope(admin):
    body = {"documents": [
        {"title": "Punjab youth employment notes", "state": "PB", "scope": "state", "doc_type": "survey", "topics": "Youth",
         "source": "State youth survey 2026", "text": "Across Punjab, 44% of graduates surveyed were looking for work for over a year."},
        {"title": "Dhuri drain works", "constituency": "Dhuri", "state": "PB", "doc_type": "news",
         "text": "Residents of Dhuri say the drain on the bazaar road has been blocked for 3 months."}]}
    r = admin.post("/api/copilot/ingest", json=body).get_json()
    a, b = r["results"]
    assert a["state"] == "PB" and a["constituency_id"] is None and a["topics"] == ["Youth"]
    assert b["constituency_id"] == "PB-DHU"


def test_manager_ingest_and_edit_rules(make_client, admin):
    m = make_client("mgr.sardhana")
    bad = upload(m, [("goa.txt", b"Margao, Goa: water supply is irregular in 61 wards of the town, residents say.")], {"constituency_id": "GA-MAR"})
    assert "own constituency" in bad.get_json()["results"][0]["error"]
    ok = upload(m, [("note.txt", b"Ward 7 residents asked for a speed breaker near the school; 230 signatures were collected.")]).get_json()
    doc = ok["results"][0]
    assert doc["constituency_id"] == "UP-SAR"
    assert m.patch(f"/api/copilot/documents/{doc['doc_id']}", json={"topics": ["Infrastructure"]}).get_json()["topics"] == ["Infrastructure"]
    docs = m.get("/api/copilot/documents").get_json()
    assert all(d["constituency_id"] in ("UP-SAR", None) and d["state"] in ("UP", None) for d in docs)
    state_doc = next(d for d in docs if d["constituency_id"] is None)
    assert m.delete(f"/api/copilot/documents/{state_doc['doc_id']}").status_code == 403
    assert m.delete(f"/api/copilot/documents/{doc['doc_id']}").get_json()["deleted"] == doc["doc_id"]
    goa = admin.get("/api/copilot/documents?constituency_id=GA-MAR").get_json()[0]
    assert m.get(f"/api/copilot/documents/{goa['doc_id']}").status_code == 404


def test_offline_brief_is_grounded(admin, monkeypatch):
    monkeypatch.setattr(cp_gen, "get_provider", lambda: None)
    for event, title in [("rally_speech", "Speech outline"), ("press_conference", "Key messages and likely questions"),
                         ("door_to_door", "Talking points at the door"), ("micro_manifesto", "Micro-manifesto")]:
        b = admin.post("/api/copilot/generate-speech-brief", json={
            "constituency_id": "UP-SAR", "event_type": event, "target_audience": "farmers", "language": "en",
            "opponent": "the sitting MLA"}).get_json()
        assert b["generator"] == "offline" and b["event_section"]["title"] == title
    assert len(b["grievances"]) == 3 and {g["title"] for g in b["grievances"]} >= {"Delayed cane payments"}
    quotes = {s["id"]: s["quote"] for s in b["sources"]}
    for g in b["grievances"]:
        assert g["sources"] and g["stats"]
        for st in g["stats"]:                                    # every figure is in the source it cites
            assert cp_gen.numbers_in(st["figure"]) <= cp_gen.numbers_in(quotes[st["source"]])
    assert all(c["sources"] for c in b["opponent_critique"])
    assert {c["type"] for c in b["commitments"]} == {"party", "local"}
    assert all("SAMPLE" in s["citation"] for s in b["sources"] if s["used"])
    md = admin.get(f"/api/copilot/outputs/{b['output_id']}.md")
    assert md.status_code == 200 and "## Sources" in md.data.decode() and "[S1]" in md.data.decode()
    pdf = admin.get(f"/api/copilot/outputs/{b['output_id']}.pdf")
    assert pdf.data[:4] == b"%PDF"
    assert admin.get("/api/copilot/outputs").get_json()[0]["output_id"] == b["output_id"]


def test_live_brief_is_verified(admin, monkeypatch):
    def reply(system, user):
        assert "S1" in user and "caste" in system
        def sid(needle):   # find the source id whose text contains `needle`
            block = next(b for b in user.split("\n\n[") if needle in b)
            return block.lstrip("[").split("]")[0]
        cane, canal, party = sid("Rs 62 crore"), sid("Desilting"), sid("monitoring cell")
        return json.dumps({
            "executive_summary": f"Cane growers are owed money [{cane}]. Turnout will be 99% this time.",
            "grievances": [
                {"title": "Cane dues", "detail": f"Rs 62 crore remains unpaid to cane growers [{cane}].",
                 "stats": [{"figure": "Rs 62 crore", "context": "unpaid dues", "source": cane},
                           {"figure": "Rs 999 crore", "context": "made up", "source": cane}], "sources": [cane]},
                {"title": "Irrigation", "detail": f"Canal desilting is incomplete [{canal}].", "stats": [], "sources": [canal]},
                {"title": "Invented", "detail": "Unemployment is 73% in every village.", "stats": [], "sources": []}],
            "opponent_critique": [{"point": f"Dues pending since July [{cane}].", "sources": [cane]},
                                  {"point": "The MLA is corrupt and a thief.", "sources": [cane]}],
            "commitments": [{"commitment": f"Clear dues within 14 days [{party}]", "delivery": "Payment monitoring cell", "type": "party", "sources": [party]},
                            {"commitment": "Special help for one caste group", "delivery": "x", "type": "local", "sources": []}],
            "event_section": {"title": "Speech outline", "items": [{"text": "Greet the crowd", "sources": []}]}})
    fake = FakeLLM(reply=reply)
    monkeypatch.setattr(cp_gen, "get_provider", lambda: fake)
    b = admin.post("/api/copilot/generate-speech-brief", json={"constituency_id": "UP-SAR", "event_type": "rally_speech",
                                                                "target_audience": "farmers", "language": "en"}).get_json()
    assert b["generator"] == "fake:copilot" and b["live"]
    assert len(b["grievances"]) == 2 and [s["figure"] for s in b["grievances"][0]["stats"]] == ["Rs 62 crore"]
    assert len(b["opponent_critique"]) == 1 and len(b["commitments"]) == 1
    assert "99" not in b["executive_summary"]["text"]
    w = " ".join(b["warnings"])
    assert "999" in w and "attack language" in w and "caste" in w


def test_hindi_brief_exports_markdown_not_pdf(admin, monkeypatch):
    reply = json.dumps({"executive_summary": "गन्ना किसानों का बकाया [S1]।",
                        "grievances": [{"title": "गन्ना बकाया", "detail": "बकाया भुगतान लंबित है [S1]।", "stats": [], "sources": ["S1"]},
                                       {"title": "सिंचाई", "detail": "नहर की सफाई अधूरी है [S2]।", "stats": [], "sources": ["S2"]}],
                        "opponent_critique": [], "commitments": [{"commitment": "भुगतान निगरानी प्रकोष्ठ [S6]", "delivery": "पार्टी", "type": "party", "sources": ["S6"]}],
                        "event_section": {"title": "भाषण", "items": []}})
    monkeypatch.setattr(cp_gen, "get_provider", lambda: FakeLLM(reply=reply))
    b = admin.post("/api/copilot/generate-speech-brief", json={"constituency_id": "UP-SAR", "event_type": "rally_speech",
                                                                "target_audience": "farmers", "language": "hi"}).get_json()
    assert b["generated_language"] == "hi" and b["grievances"][0]["title"] == "गन्ना बकाया"
    assert "गन्ना" in admin.get(f"/api/copilot/outputs/{b['output_id']}.md").data.decode()
    r = admin.get(f"/api/copilot/outputs/{b['output_id']}.pdf")
    assert r.status_code == 400 and "Print" in r.get_json()["error"]


def test_llm_failure_falls_back(admin, monkeypatch):
    monkeypatch.setattr(cp_gen, "get_provider", lambda: FakeLLM(error="HTTP 529: overloaded"))
    b = admin.post("/api/copilot/generate-speech-brief", json={"constituency_id": "GA-MAR", "target_audience": "youth"}).get_json()
    assert b["generator"] == "offline" and "AI draft unavailable" in b["warnings"][0] and b["grievances"]


def test_manifesto_offline_and_booth_cluster(admin, make_client, monkeypatch):
    monkeypatch.setattr(cp_gen, "get_provider", lambda: None)
    m = admin.post("/api/copilot/generate-manifesto", json={"constituency_id": "UP-SAR", "party_name": "Jan Morcha"}).get_json()
    assert m["title"].startswith("Sardhana") and 3 <= len(m["sections"]) <= 4
    assert any(c["type"] == "party" for s in m["sections"] for c in s["commitments"])
    assert all(s["context_sources"] for s in m["sections"])
    md = admin.get(f"/api/copilot/outputs/{m['output_id']}.md").data.decode()
    assert "Jan Morcha" in md and "_What we found:_" in md
    assert admin.get(f"/api/copilot/outputs/{m['output_id']}.pdf").data[:4] == b"%PDF"
    cl = admin.post("/api/copilot/generate-manifesto", json={"constituency_id": "UP-SAR", "booth_ids": ["UP-SAR-001", "UP-SAR-002"]}).get_json()
    assert "booths UP-SAR-001" in cl["constituency"]["name"]
    assert admin.post("/api/copilot/generate-manifesto", json={"constituency_id": "UP-SAR", "booth_ids": ["UP-MC-001"]}).status_code == 400
    mgr = make_client("mgr.sardhana")
    assert mgr.post("/api/copilot/generate-manifesto", json={"constituency_id": "GA-MAR"}).status_code == 403
    assert mgr.get(f"/api/copilot/outputs/{m['output_id']}").status_code == 200


def test_figure_checks():
    allowed = cp_gen.numbers_in("Rs 62 crore to 9,400 growers; 68% said so")
    assert cp_gen.unsupported_numbers("Rs 62 crore owed to 9,400 growers, 68%", allowed) == []
    assert cp_gen.unsupported_numbers("about 75% of people, 3 issues, in 2027", allowed) == ["75"]
    assert cp_gen.numbers_in("६८% किसान") == {"68"}


def test_embeddings_enable_hybrid_search(admin, monkeypatch):
    class FakeEmb:
        name = "fake-emb"
        def embed(self, texts):
            return [[1.0 if "water" in t.lower() else 0.0, 1.0] for t in texts]
    monkeypatch.setattr(cp_kb, "get_embedder", lambda: FakeEmb())
    r = admin.post("/api/copilot/ingest", json={"title": "Water", "constituency": "Margao", "state": "GA",
                                                "text": "Piped water reaches Borda ward only on alternate days, residents say."}).get_json()
    assert r["results"][0]["embedded"] is True
    q = admin.post("/api/copilot/query", json={"query": "water supply Borda", "constituency_id": "GA-MAR"}).get_json()
    assert q["search"] == "hybrid" and q["results"][0]["title"] == "Water"


def test_live_answer_keeps_valid_sentences(admin, monkeypatch):
    reply = json.dumps({"answer": "Will the dues be cleared? Rs 62 crore is unpaid [S1]. Also 777 tractors were seized."})
    monkeypatch.setattr(cp_gen, "get_provider", lambda: FakeLLM(reply=reply))
    q = admin.post("/api/copilot/query", json={"query": "cane dues unpaid", "constituency_id": "UP-SAR"}).get_json()
    a = q["answer"]
    assert a["generator"] == "fake:copilot" and "Rs 62 crore is unpaid [S1]." in a["text"] and "777" not in a["text"]
