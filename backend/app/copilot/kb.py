"""JanVani Copilot knowledge base: ingestion, metadata detection, chunking, search.

Documents (PDF / TXT / Markdown / JSON) are split into ~900-character chunks
that keep their page number, so every generated claim can cite "title, p. N".

Search is hybrid:
  * BM25 over chunk terms (always; works for English, Hindi and Punjabi script)
  * cosine similarity over embeddings, when an embedding provider is configured
    (OPENAI_API_KEY with VOTERENGINE_EMBEDDINGS unset or "openai"). Chunks
    ingested before embeddings were enabled simply rank on BM25 alone.
The `Embedder` protocol is the seam for any other vector backend.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import unicodedata
import uuid
from collections import Counter
from pathlib import Path
from typing import Protocol

from ..config import STATES
from ..db import now_iso
from ..generator import constituency_code

SEED_PATH = Path(__file__).with_name("seed.json")
DOC_TYPES = {"budget": "Budget allocation", "legislative": "Legislative question", "news": "Local news",
             "survey": "Ground survey", "party_promise": "Party promise", "other": "Other"}
TOPICS = ("Agriculture", "Infrastructure", "Youth", "Welfare")
MAX_BYTES = 20 * 1024 * 1024
CHUNK, OVERLAP = 900, 150

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r"[0-9a-zऀ-ॣ०-ॿ਀-੿]+")
_STOP = set("""a an and are as at be by for from has have in is it its of on or that the this to was were will with
not no but than then there their they them which who whom what when where while also into over under per
का के की है में से को और पर भी यह वह एक था थे थी हैं""".split())


def ascii_digits(s: str) -> str:
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and not ch.isascii() else ch for ch in s)


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(ascii_digits(str(text)).lower()) if t not in _STOP and (len(t) > 1 or t.isdigit())]


TOPIC_LEXICON = {
    "Agriculture": "farmer farmers farm crop crops sugarcane cane wheat paddy mandi msp irrigation fertiliser fertilizer kisan "
                   "procurement tubewell canal cattle dairy horticulture fishing harvest seed seeds agriculture agricultural "
                   "mill किसान गन्ना फसल सिंचाई मंडी खाद ਕਿਸਾਨ ਫਸਲ",
    "Infrastructure": "road roads bridge drain drainage water supply electricity power sewer streetlight streetlights bus rail "
                      "construction pwd culvert flyover pipeline hospital building connectivity internet waterlogging potholes "
                      "सड़क पुल बिजली पानी नाली ਸੜਕ ਬਿਜਲੀ",
    "Youth": "youth young job jobs employment unemployment unemployed skill skilling college colleges exam exams recruitment "
             "sports startup internship students student coaching iti युवा रोजगार नौकरी भर्ती ਨੌਜਵਾਨ ਰੁਜ਼ਗਾਰ",
    "Welfare": "pension pensions ration rations scheme schemes pmay housing ujjwala ayushman health anganwadi women widow "
               "scholarship beneficiary beneficiaries welfare self help shg nutrition children child mothers pregnant "
               "malnutrition midday disability elderly पेंशन राशन योजना आवास महिला आंगनवाड़ी ਪੈਨਸ਼ਨ ਰਾਸ਼ਨ",
}
TOPIC_WORDS = {t: set(v.split()) for t, v in TOPIC_LEXICON.items()}
TYPE_LEXICON = {
    "budget": "budget allocation allocated crore lakh sanctioned expenditure outlay released utilised utilized unspent grant",
    "legislative": "question starred unstarred assembly vidhan sabha minister replied reply mla house tabled",
    "news": "reported according said residents protest newspaper correspondent published yesterday",
    "survey": "survey respondents respondent feedback ground interviewed sample households said percent",
    "party_promise": "we will our party commitment sankalp manifesto pledge promise guarantee",
}
TYPE_WORDS = {t: set(v.split()) for t, v in TYPE_LEXICON.items()}
STATE_PATTERNS = {code: re.compile(rf"\b({re.escape(cfg['name'])}|{code})\b", re.IGNORECASE)
                  for code, cfg in STATES.items()}
STATE_PATTERNS["UP"] = re.compile(r"(?i:\buttar pradesh\b)|\bU\.P\.|\bUP\b|उत्तर प्रदेश")   # bare "UP" only in capitals
STATE_PATTERNS["PB"] = re.compile(r"\b(punjab)\b|ਪੰਜਾਬ|पंजाब", re.IGNORECASE)
STATE_PATTERNS["UK"] = re.compile(r"\b(uttarakhand|uttaranchal)\b|उत्तराखंड", re.IGNORECASE)
STATE_PATTERNS["GA"] = re.compile(r"\b(goa)\b|गोवा", re.IGNORECASE)
DATE_PATTERNS = [
    (re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"), lambda m: f"{m[1]}-{m[2]}-{m[3]}"),
    (re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\b"), lambda m: f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"),
    (re.compile(r"\b(\d{1,2}) (January|February|March|April|May|June|July|August|September|October|November|December) (20\d{2})\b", re.I),
     lambda m: f"{m[3]}-{['january','february','march','april','may','june','july','august','september','october','november','december'].index(m[2].lower()) + 1:02d}-{int(m[1]):02d}"),
]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_file(data: bytes, filename: str) -> list[dict]:
    """Returns a list of raw documents: {title, pages: [(page_no, text)], meta}."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"{filename}: larger than {MAX_BYTES // (1024 * 1024)} MB")
    name = filename.lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        return [_parse_pdf(data, filename)]
    text = _decode(data)
    if name.endswith(".json") or text.lstrip()[:1] in ("[", "{"):
        return _parse_json(text, filename)
    if "\x00" in text[:2000]:
        raise ValueError(f"{filename}: unsupported binary file (use PDF, TXT, Markdown or JSON)")
    return [{"title": _guess_title(text, filename), "pages": [(1, text)], "meta": {}}]


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _parse_pdf(data: bytes, filename: str) -> dict:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [(i + 1, (p.extract_text() or "").strip()) for i, p in enumerate(reader.pages)]
    except Exception as e:  # pypdf raises many types on malformed files
        raise ValueError(f"{filename}: could not read this PDF ({type(e).__name__})") from None
    info = reader.metadata or {}
    title = (getattr(info, "title", None) or "").strip() or _guess_title(next((t for _, t in pages if t), ""), filename)
    return {"title": title, "pages": pages, "meta": {}}


def _parse_json(text: str, filename: str) -> list[dict]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{filename}: invalid JSON ({e.msg}, line {e.lineno})") from None
    items = obj.get("documents") if isinstance(obj, dict) and isinstance(obj.get("documents"), list) else obj
    items = items if isinstance(items, list) else [items]
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        meta = {k: it.get(k) for k in ("state", "constituency", "constituency_id", "topic", "topics", "doc_type", "type",
                                       "source", "source_citation", "source_url", "published_on", "date", "booth_ids")
                if it.get(k) not in (None, "")}
        if isinstance(it.get("pages"), list):
            pages = [(p.get("page", n + 1), str(p.get("text", ""))) for n, p in enumerate(it["pages"]) if isinstance(p, dict)]
        elif it.get("text") or it.get("body") or it.get("content"):
            pages = [(1, str(it.get("text") or it.get("body") or it.get("content")))]
        else:
            # survey-style record: turn fields into readable lines
            pages = [(1, "\n".join(f"{k.replace('_', ' ')}: {v}" for k, v in it.items() if k not in meta and k != "title"))]
        out.append({"title": str(it.get("title") or f"{Path(filename).stem} #{i + 1}"), "pages": pages, "meta": meta})
    if not out:
        raise ValueError(f"{filename}: no documents found in JSON")
    return out


def _guess_title(text: str, filename: str) -> str:
    for line in text.splitlines():
        line = line.strip().strip("#").strip()
        if 8 <= len(line) <= 140:
            return line
    return Path(filename).stem.replace("_", " ").replace("-", " ")[:140] or "Untitled"


# ---------------------------------------------------------------------------
# Metadata detection
# ---------------------------------------------------------------------------

def known_constituencies(conn) -> list[dict]:
    rows = {(r["state"], r["constituency"]) for r in conn.execute("SELECT DISTINCT state, constituency FROM booths")}
    for code, cfg in STATES.items():
        rows.update((code, name) for name in cfg["constituencies"])
    return sorted(({"id": f"{s}-{constituency_code(c)}", "name": c, "state": s} for s, c in rows
                   if c and c != "Unassigned"), key=lambda x: (x["state"], x["name"]))


def detect_metadata(conn, title: str, text: str) -> dict:
    sample = f"{title}\n{text[:20000]}"
    low = ascii_digits(sample).lower()
    toks = Counter(tokens(sample))
    # state
    state_hits = {code: len(p.findall(sample)) for code, p in STATE_PATTERNS.items()}
    # constituency: names of known constituencies found in the text
    const_hits = {}
    for c in known_constituencies(conn):
        n = len(re.findall(rf"\b{re.escape(c['name'].lower())}\b", low))
        if n:
            const_hits[c["id"]] = (n, c)
    constituency = max(const_hits.values(), key=lambda x: x[0])[1] if const_hits else None
    state = constituency["state"] if constituency else (max(state_hits, key=state_hits.get) if max(state_hits.values()) else None)
    # topics
    stemmed = Counter()
    for tok, n in toks.items():          # light plural folding: "rations" -> "ration"
        stemmed[tok] += n
        if len(tok) > 4 and tok.endswith("s"):
            stemmed[tok[:-1]] += n
    topic_scores = {t: sum(stemmed[w] for w in words) for t, words in TOPIC_WORDS.items()}
    best = max(topic_scores.values())
    ranked = sorted(topic_scores.items(), key=lambda x: -x[1])
    if best >= 2:
        topics = [t for t, s in ranked if s >= max(2, best * 0.5)]
    else:   # a single keyword is weak evidence: keep only the top topic
        topics = [ranked[0][0]] if best == 1 else []
    # type
    type_scores = {t: sum(toks[w] for w in words) for t, words in TYPE_WORDS.items()}
    doc_type = max(type_scores, key=type_scores.get) if max(type_scores.values()) >= 2 else "other"
    # date
    published = None
    for pat, fmt in DATE_PATTERNS:
        m = pat.search(sample)
        if m:
            try:
                published = fmt(m)
            except (ValueError, IndexError):
                published = None
            break
    return {"state": state, "constituency": constituency["name"] if constituency else None,
            "constituency_id": constituency["id"] if constituency else None,
            "topics": topics or [], "primary_topic": topics[0] if topics else None, "doc_type": doc_type,
            "published_on": published,
            "evidence": {"state_hits": {k: v for k, v in state_hits.items() if v}, "topic_scores": topic_scores,
                         "type_scores": type_scores}}


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
_SENT = re.compile(r"(?<=[.!?।])\s+|\n{2,}")


def chunk_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for page, text in pages:
        text = re.sub(r"[ \t]+", " ", text or "").strip()
        if not text:
            continue
        parts = [p.strip() for p in _SENT.split(text) if p and p.strip()]
        buf = ""
        for part in parts:
            while len(part) > CHUNK:                       # very long run-on text
                out.append((page, (buf + " " + part[:CHUNK]).strip())); buf = ""
                part = part[CHUNK - OVERLAP:]
            if len(buf) + len(part) + 1 > CHUNK and buf:
                out.append((page, buf.strip()))
                tail = buf[-OVERLAP:]
                buf = tail[tail.find(" ") + 1:] if " " in tail else ""
            buf = f"{buf} {part}".strip()
        if buf:
            out.append((page, buf.strip()))
    return out


# ---------------------------------------------------------------------------
# Embeddings (optional)
# ---------------------------------------------------------------------------

class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, key: str, model: str):
        self.key, self.model, self.name = key, model, f"openai:{model}"
        self.url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/embeddings"

    def embed(self, texts: list[str]) -> list[list[float]]:
        from ..llm import _post_json
        out = []
        for i in range(0, len(texts), 64):
            res = _post_json(self.url, {"Authorization": f"Bearer {self.key}"},
                             {"model": self.model, "input": [t[:6000] for t in texts[i:i + 64]]}, 30)
            out += [d["embedding"] for d in sorted(res["data"], key=lambda d: d["index"])]
        return out


def get_embedder() -> Embedder | None:
    choice = os.environ.get("VOTERENGINE_EMBEDDINGS", "auto").lower()
    key = os.environ.get("OPENAI_API_KEY")
    if choice in ("off", "none") or not key:
        return None
    return OpenAIEmbedder(key, os.environ.get("VOTERENGINE_EMBEDDING_MODEL", "text-embedding-3-small"))


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def _norm_topics(v) -> list[str]:
    items = v if isinstance(v, list) else re.split(r"[,;|]", str(v or ""))
    out = []
    for x in items:
        x = str(x).strip().title()
        if x in TOPICS and x not in out:
            out.append(x)
    return out


def _resolve_place(conn, state, constituency, constituency_id):
    consts = known_constituencies(conn)
    if constituency_id:
        c = next((c for c in consts if c["id"].lower() == str(constituency_id).lower()), None)
        if not c:
            raise ValueError(f"unknown constituency id {constituency_id}")
        return c["state"], c["name"], c["id"]
    if constituency:
        c = next((c for c in consts if c["name"].lower() == str(constituency).lower()
                  and (not state or c["state"] == state)), None)
        if c:
            return c["state"], c["name"], c["id"]
        if not state:
            raise ValueError(f"unknown constituency {constituency}: also give its state")
        return state, constituency, f"{state}-{constituency_code(constituency)}"
    return state, None, None


def ingest(conn, raw: dict, overrides: dict, actor: str, filename: str, embedder: Embedder | None = None,
           enforce_place: dict | None = None) -> dict:
    """Store one parsed document. `overrides` (from the form or JSON) beat detection.
    `enforce_place` pins state/constituency for constituency managers."""
    text_pages = raw["pages"]
    full = "\n\n".join(t for _, t in text_pages if t)
    pages_without_text = sum(1 for _, t in text_pages if not t.strip())
    if len(full.strip()) < 40:
        raise ValueError(f"{filename}: no readable text"
                         + (" (scanned PDF: run OCR first)" if pages_without_text else ""))
    sha = hashlib.sha256(full.encode()).hexdigest()
    dup = conn.execute("SELECT doc_id, title FROM copilot_documents WHERE sha256 = ?", (sha,)).fetchone()
    if dup:
        return {"status": "duplicate", "doc_id": dup["doc_id"], "title": dup["title"]}

    meta = {**raw.get("meta", {}), **{k: v for k, v in overrides.items() if v not in (None, "", [])}}
    det = detect_metadata(conn, raw["title"], full)
    state = meta.get("state") or det["state"]
    state = state.upper() if isinstance(state, str) else state
    if state and state not in STATES:
        raise ValueError(f"unknown state {state}")
    # precedence: explicit constituency > explicit state-wide/central scope > detection (same state only)
    if meta.get("constituency") or meta.get("constituency_id"):
        st, const, cid = _resolve_place(conn, state, meta.get("constituency"), meta.get("constituency_id"))
    elif meta.get("scope") in ("state", "central"):
        st, const, cid = (None if meta["scope"] == "central" else state), None, None
    elif det["constituency_id"] and (not state or state == det["state"]):
        st, const, cid = det["state"], det["constituency"], det["constituency_id"]
    else:
        st, const, cid = state, None, None
    if enforce_place:
        if (st and st != enforce_place["state"]) or (const and const.lower() != enforce_place["constituency"].lower()):
            raise ValueError("you can add documents for your own constituency only")
        st, const, cid = _resolve_place(conn, enforce_place["state"], enforce_place["constituency"], None)
    doc_type = str(meta.get("doc_type") or meta.get("type") or det["doc_type"]).lower()
    if doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type must be one of {list(DOC_TYPES)}")
    topics = _norm_topics(meta.get("topics") or meta.get("topic")) or det["topics"]
    title = str(meta.get("title") or raw["title"])[:200]
    published = meta.get("published_on") or meta.get("date") or det["published_on"]
    citation = str(meta.get("source_citation") or meta.get("source") or
                   f"{title} ({DOC_TYPES[doc_type]}{', ' + str(published) if published else ''})")[:300]

    chunks = chunk_pages(text_pages)
    doc_id = f"doc-{uuid.uuid4().hex[:10]}"
    ts = now_iso()
    conn.execute("""INSERT INTO copilot_documents (doc_id, title, state, constituency, constituency_id, booth_ids, doc_type,
                    topics, primary_topic, source_citation, source_url, published_on, file_name, pages, pages_without_text,
                    chars, sha256, detected, active, uploaded_by, uploaded_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                 (doc_id, title, st, const, cid, ",".join(meta.get("booth_ids") or []) if isinstance(meta.get("booth_ids"), list)
                  else str(meta.get("booth_ids") or ""), doc_type, ",".join(topics), topics[0] if topics else None, citation,
                  meta.get("source_url"), published, filename[:200], len(text_pages), pages_without_text, len(full), sha,
                  json.dumps({k: det[k] for k in ("state", "constituency", "topics", "doc_type", "published_on")}),
                  actor, ts, ts))
    vectors = None
    if embedder and chunks:
        try:
            vectors = embedder.embed([t for _, t in chunks])
        except Exception:      # embeddings are an enhancement; BM25 still works
            vectors = None
    rows = []
    for i, (page, text) in enumerate(chunks):
        toks = tokens(text)
        rows.append((f"{doc_id}-{i:04d}", doc_id, i, page, text, json.dumps(Counter(toks)), len(toks),
                     json.dumps(vectors[i]) if vectors else None))
    conn.executemany("INSERT INTO copilot_chunks (chunk_id, doc_id, seq, page, text, tf, length, embedding) VALUES (?,?,?,?,?,?,?,?)", rows)
    return {"status": "indexed", "doc_id": doc_id, "title": title, "state": st, "constituency": const,
            "constituency_id": cid, "doc_type": doc_type, "topics": topics, "published_on": published,
            "source_citation": citation, "pages": len(text_pages), "pages_without_text": pages_without_text,
            "chunks": len(rows), "embedded": bool(vectors), "detected": det}


def seed_corpus(conn) -> int:
    if conn.execute("SELECT COUNT(*) FROM copilot_documents").fetchone()[0]:
        return 0
    docs = json.loads(SEED_PATH.read_text())["documents"]
    n = 0
    for d in docs:
        raw = {"title": d["title"], "pages": [(p["page"], p["text"]) for p in d["pages"]], "meta": {}}
        res = ingest(conn, raw, {k: d.get(k) for k in ("state", "constituency", "doc_type", "topics", "source_citation",
                                                        "published_on", "booth_ids", "scope")}, "seed", "seed.json")
        if res["status"] == "indexed":
            conn.execute("UPDATE copilot_documents SET detected = '{}' WHERE doc_id = ?", (res["doc_id"],))
        n += res["status"] == "indexed"
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Scope + search
# ---------------------------------------------------------------------------

def place_filter(state: str | None, constituency_id: str | None, include_wider=True, alias="d") -> tuple[str, list]:
    """Documents for a constituency, plus (by default) its state-wide and central material."""
    if constituency_id:
        clause = f"({alias}.constituency_id = ?"
        params: list = [constituency_id]
        if include_wider:
            clause += f" OR ({alias}.constituency_id IS NULL AND ({alias}.state = ? OR {alias}.state IS NULL))"
            params.append(state)
        return clause + ")", params
    if state:
        return f"({alias}.state = ? OR {alias}.state IS NULL)" if include_wider else f"{alias}.state = ?", [state]
    return "1=1", []


def search(conn, query: str, state: str | None = None, constituency_id: str | None = None, topic: str | None = None,
           doc_types: list[str] | None = None, booth_ids: list[str] | None = None, k: int = 8,
           embedder: Embedder | None = None) -> list[dict]:
    where, params = place_filter(state, constituency_id)
    clauses = ["d.active = 1", where]
    if topic:
        clauses.append("(',' || d.topics || ',') LIKE ?"); params.append(f"%,{topic},%")
    if doc_types:
        clauses.append(f"d.doc_type IN ({','.join('?' * len(doc_types))})"); params += doc_types
    rows = [dict(r) for r in conn.execute(f"""SELECT c.chunk_id, c.doc_id, c.page, c.text, c.tf, c.length, c.embedding,
            d.title, d.doc_type, d.topics, d.state, d.constituency, d.constituency_id, d.source_citation, d.source_url,
            d.published_on, d.booth_ids FROM copilot_chunks c JOIN copilot_documents d USING(doc_id)
            WHERE {' AND '.join(clauses)}""", params)]
    if booth_ids:
        want = set(booth_ids)
        rows = [r for r in rows if not r["booth_ids"] or want & set(r["booth_ids"].split(","))]
    if not rows:
        return []
    q = tokens(query)
    n = len(rows)
    avg = sum(r["length"] for r in rows) / n or 1
    tfs = [json.loads(r["tf"]) for r in rows]
    df = Counter(t for tf in tfs for t in tf)
    k1, b = 1.5, 0.75
    bm = []
    for r, tf in zip(rows, tfs):
        s = 0.0
        for t in set(q):
            f = tf.get(t, 0)
            if f:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * r["length"] / avg))
        bm.append(s)
    top = max(bm) or 1
    scores = [s / top for s in bm]
    if embedder and any(r["embedding"] for r in rows):
        try:
            qv = embedder.embed([query])[0]
            qn = math.sqrt(sum(x * x for x in qv)) or 1
            for i, r in enumerate(rows):
                if r["embedding"]:
                    v = json.loads(r["embedding"])
                    cos = sum(a * b2 for a, b2 in zip(qv, v)) / (qn * (math.sqrt(sum(x * x for x in v)) or 1))
                    scores[i] = 0.5 * scores[i] + 0.5 * max(cos, 0)
        except Exception:
            pass
    for i, r in enumerate(rows):           # small boosts: local beats state-wide, topic match
        if constituency_id and r["constituency_id"] == constituency_id:
            scores[i] *= 1.15
    ranked = sorted(zip(scores, rows), key=lambda x: -x[0])
    out, per_doc = [], Counter()
    for s, r in ranked:
        if s <= 0.02 or per_doc[r["doc_id"]] >= 3:
            continue
        per_doc[r["doc_id"]] += 1
        r = {k: v for k, v in r.items() if k not in ("tf", "embedding", "length")}
        r["score"] = round(s, 3)
        r["snippet"] = snippet(r["text"], q)
        r["citation"] = f"{r['source_citation']}{', p. ' + str(r['page']) if r['page'] else ''}"
        out.append(r)
        if len(out) >= k:
            break
    return out


def snippet(text: str, q: list[str], width: int = 300) -> str:
    sents = [s.strip() for s in _SENT.split(text) if s.strip()]
    if not sents:
        return text[:width]
    qs = set(q)
    best = max(range(len(sents)), key=lambda i: len(qs & set(tokens(sents[i]))) + (0.5 if re.search(r"\d", sents[i]) else 0))
    out = sents[best]
    j = best + 1
    while len(out) < width * 0.7 and j < len(sents):
        out += " " + sents[j]; j += 1
    return out if len(out) <= width else out[:width].rsplit(" ", 1)[0] + "…"


def stats(conn, state=None, constituency_id=None) -> dict:
    where, params = place_filter(state, constituency_id)
    r = conn.execute(f"""SELECT COUNT(DISTINCT d.doc_id) docs, COUNT(c.chunk_id) chunks, SUM(c.embedding IS NOT NULL) embedded
                         FROM copilot_documents d LEFT JOIN copilot_chunks c USING(doc_id) WHERE d.active = 1 AND {where}""",
                     params).fetchone()
    return {"documents": r["docs"] or 0, "chunks": r["chunks"] or 0, "embedded_chunks": r["embedded"] or 0}
