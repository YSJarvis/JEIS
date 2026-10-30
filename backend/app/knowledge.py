"""Campaign knowledge base, daily directives and retrieval.

The retriever is a small TF-IDF ranker with tag boosts: no extra
dependencies, deterministic and explainable. The `Retriever` protocol
lets a vector store (pgvector, a hosted embedding index) replace it
without touching the prompt builder or the API.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

from .db import now_iso

SEED_PATH = Path(__file__).with_name("knowledge_seed.json")
KINDS = ("scheme", "issue", "turnout", "context", "brief")
MAX_DIRECTIVE_LEN = 280


# ---------------- knowledge base ----------------

def seed_knowledge(conn, force=False) -> int:
    if not force and conn.execute("SELECT COUNT(*) FROM kb_documents").fetchone()[0]:
        return 0
    docs = json.loads(SEED_PATH.read_text())["documents"]
    ts = now_iso()
    conn.executemany("""INSERT OR REPLACE INTO kb_documents (doc_id, state, constituency, kind, title, body, ask, tags,
                        source, active, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,'seed',?)""",
                     [(d["doc_id"], d.get("state"), d.get("constituency"), d["kind"], d["title"], d["body"],
                       d.get("ask"), d.get("tags", ""), d.get("source"), ts) for d in docs])
    conn.commit()
    return len(docs)


def kb_version(conn) -> str:
    r = conn.execute("SELECT COUNT(*) n, MAX(updated_at) m FROM kb_documents WHERE active = 1").fetchone()
    return f"{r['n']}:{r['m']}"


def list_docs(conn, state: str | None = None, constituency: str | None = None, include_inactive=False) -> list[dict]:
    clauses, params = [], []
    if not include_inactive:
        clauses.append("active = 1")
    if state:
        clauses.append("(state IS NULL OR state = ?)"); params.append(state)
    if constituency:
        clauses.append("(constituency IS NULL OR lower(constituency) = lower(?))"); params.append(constituency)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM kb_documents {where} ORDER BY state IS NOT NULL, state, constituency, kind, title", params)]


def save_doc(conn, body: dict, actor: str, doc_id: str | None = None) -> dict:
    fields = {k: body.get(k) for k in ("state", "constituency", "kind", "title", "body", "ask", "tags", "source", "active")
              if k in body}
    if doc_id is None:
        for req in ("kind", "title", "body"):
            if not str(body.get(req) or "").strip():
                raise ValueError(f"{req} is required")
        doc_id = re.sub(r"[^a-z0-9]+", "-", f"{body.get('kind')}-{body.get('state') or 'all'}-{body['title']}".lower()).strip("-")[:60]
    if "kind" in fields and fields["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    if len(str(fields.get("body") or "")) > 2000:
        raise ValueError("body is limited to 2,000 characters")
    exists = conn.execute("SELECT 1 FROM kb_documents WHERE doc_id = ?", (doc_id,)).fetchone()
    ts = now_iso()
    if exists:
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_by = ?, updated_at = ?"
        conn.execute(f"UPDATE kb_documents SET {sets} WHERE doc_id = ?", [*fields.values(), actor, ts, doc_id])
    else:
        conn.execute("""INSERT INTO kb_documents (doc_id, state, constituency, kind, title, body, ask, tags, source,
                        active, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)""",
                     (doc_id, fields.get("state"), fields.get("constituency"), fields["kind"], fields["title"],
                      fields["body"], fields.get("ask"), fields.get("tags") or "", fields.get("source"), actor, ts))
    return dict(conn.execute("SELECT * FROM kb_documents WHERE doc_id = ?", (doc_id,)).fetchone())


# ---------------- directives ----------------

def directives_for(conn, date: str, state: str | None, constituency: str | None) -> list[dict]:
    """Active directives that apply to a place on a date: all-state, state-wide, then constituency."""
    rows = conn.execute("""SELECT d.*, u.display_name AS author FROM directives d LEFT JOIN users u ON u.user_id = d.created_by
        WHERE d.active = 1 AND d.for_date = ?
          AND (d.state IS NULL OR d.state = ?)
          AND (d.constituency IS NULL OR lower(d.constituency) = lower(?))
        ORDER BY (d.state IS NOT NULL) + (d.constituency IS NOT NULL) DESC, d.directive_id DESC""",
                        (date, state, constituency or "")).fetchall()
    return [dict(r) for r in rows]


def add_directive(conn, text: str, date: str, state: str | None, constituency: str | None, actor: str) -> dict:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("directive text is required")
    if len(text) > MAX_DIRECTIVE_LEN:
        raise ValueError(f"keep directives under {MAX_DIRECTIVE_LEN} characters")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise ValueError("for_date must be YYYY-MM-DD")
    cur = conn.execute("""INSERT INTO directives (state, constituency, for_date, text, created_by, created_at)
                          VALUES (?,?,?,?,?,?)""", (state, constituency, date, text, actor, now_iso()))
    return dict(conn.execute("SELECT * FROM directives WHERE directive_id = ?", (cur.lastrowid,)).fetchone())


# ---------------- retrieval ----------------

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were will with "
            "ask whether their they them any each".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(str(text).lower()) if t not in _STOP and len(t) > 1]


class Retriever(Protocol):
    def search(self, query: str, tags: set[str], docs: list[dict], k: int) -> list[dict]: ...


class TfidfRetriever:
    """Cosine similarity on TF-IDF over title+body+tags, plus a boost for exact
    tag matches (scheme names, crops, terrain) and for constituency briefs."""

    def search(self, query: str, tags: set[str], docs: list[dict], k: int = 4) -> list[dict]:
        if not docs:
            return []
        doc_toks = [tokens(f"{d['title']} {d['body']} {d.get('tags') or ''}") for d in docs]
        df = Counter(t for toks in doc_toks for t in set(toks))
        n = len(docs)
        idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}

        def vec(toks):
            tf = Counter(toks)
            v = {t: (1 + math.log(c)) * idf.get(t, math.log(n + 1) + 1) for t, c in tf.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1
            return {t: x / norm for t, x in v.items()}

        q = vec(tokens(query))
        want = {t.lower() for t in tags}
        scored = []
        for d, toks in zip(docs, doc_toks):
            dv = vec(toks)
            score = sum(q.get(t, 0) * x for t, x in dv.items())
            doc_tags = {t.strip().lower() for t in (d.get("tags") or "").split(",") if t.strip()}
            score += 0.35 * len(doc_tags & want)
            if d.get("constituency"):
                score += 0.15
            if score > 0.05:
                scored.append((score, d))
        scored.sort(key=lambda x: -x[0])
        return [{**d, "score": round(s, 3)} for s, d in scored[:k]]


RETRIEVER: Retriever = TfidfRetriever()
