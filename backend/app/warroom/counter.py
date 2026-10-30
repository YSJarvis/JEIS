"""CounterPulse counter-response generator.

Pipeline: narrative cluster -> the opposition's core claim and recent items (M1..Mn,
treated as claims, never as evidence) -> JanVani knowledge-base retrieval (S1..Sn)
-> LLM draft (or offline template) -> verification -> ECI/MCC safety checklist.

Honesty rules enforced after generation:
  * every figure we assert must appear in our sources (opposition figures may only
    appear where we describe their claim)
  * if our own sources confirm the problem, copy that calls it false/fake/baseless
    is removed: the response must acknowledge and pivot to action
  * no personal or private-life attacks, no caste/religion appeals, no inducements
    or threats, no M# items cited as proof
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter

from ..copilot import kb
from ..copilot.generate import (ATTACK, ISSUE_PAT, LAG_WORDS, LANGUAGES, MAX_TOKENS, Verifier, _clause, _figure, _parse_json,
                                _sentences, gather_sources, numbers_in, source_list, sources_block, within_limit)
from ..db import now_iso
from ..llm import LLMError, get_provider
from .engine import EXTRA_ISSUES, SOURCE_TYPES, threat
from ..copilot.generate import ISSUES

MEDIUMS = {"tv_brief": "TV spokesperson brief", "whatsapp": "WhatsApp broadcast copy",
           "press_release": "Official press release", "digital_ad": "Digital ad text"}
VERDICTS = {"supported": "Our records confirm the problem", "partly_supported": "Partly right: the problem is real, details differ",
            "not_supported": "Our records contradict the claim", "not_addressed": "Our records don't cover this yet"}
DENIAL = re.compile(r"\b(false|fake|baseless|lies?|lying|rumou?rs?|propaganda|misleading|untrue|fabricated|hoax)\b|झूठ|अफवाह|ਝੂਠ", re.I)
PRIVATE = re.compile(r"\b(?:his|her|their) (?:wife|husband|son|daughter|family|relatives)\b|\b(?:personal|private) life\b|\bcharacter\b", re.I)
INDUCE = re.compile(r"\bcash (?:for|to) vot\w*|\bfree liquor\b|\bgifts? (?:for|to) vot\w*|\bteach \w+ a lesson\b|\bpunish\b|\bsee (?:them|you) after\b", re.I)
MREF = re.compile(r"\s*\[M\d+\]")
# extra "the problem is real" phrasings on top of the Copilot lag words
RECORD = re.compile(r"\b(irregular|blocked|shut|no doctor|vacant|unpaid|not been repaired|have not been|has not been|"
                    r"\d+ of \d+ (?:doctor )?posts filled|stalled|scarce)\b", re.I)
ACTION = re.compile(r"\b(we will|directed|allocated|sanctioned|released|cleared|completed|launched|ordered|set up|monitoring)\b", re.I)
LIMITS = {"whatsapp": 700, "ad_headline": 40, "ad_body": 125, "ad_cta": 20}

MEDIUM_SCHEMA = {
    "tv_brief": '{"headline_message": "one line", "talking_points": ["3 points with [S#]"], "bridging_lines": ["2 lines"], '
                '"likely_questions": [{"q": "...", "a": "answer with [S#]"}]}',
    "whatsapp": '{"text": "under 600 characters, short lines, no [S#] markers, ends with a one-line source note", "sources": ["S#"]}',
    "press_release": '{"headline": "...", "body": ["3 short paragraphs with [S#]"], "quote": {"text": "...", "attribution": "name, role"}, "sources": ["S#"]}',
    "digital_ad": '{"variants": [{"headline": "max 40 characters", "body": "max 125 characters", "cta": "max 20 characters"}], "sources": ["S#"]}',
}

SYSTEM = """You are the rapid-response desk of an Indian election campaign. Draft a factual counter-response to a narrative.

Rules:
1. NARRATIVE items [M#] are what others are saying. They are claims, not evidence. Never cite M# as a source and never repeat their figures as fact.
2. Every fact and every number we assert must come from SOURCES and carry [S#] right after it (except in WhatsApp and ad copy, which list sources separately). Copy figures exactly.
3. First assess the core claim against SOURCES: "supported", "partly_supported", "not_supported" or "not_addressed". If our sources confirm the problem, say so plainly and pivot to what is being done or committed. Never call a documented problem false, fake, baseless or propaganda.
4. Critique only policies and the public record. No personal attacks, nothing about anyone's family or private life, no allegations of crime.
5. Never appeal to caste, religion, community or language. No inducements, threats or intimidation (Model Code of Conduct).
6. Calm, respectful, short sentences. Write in {language}; keep [S#] markers unchanged.

Reply with JSON only:
{{"threat_summary": "2 sentences on what is spreading and where",
 "core_claim": "the opposition's central claim in one sentence",
 "assessment": {{"verdict": "supported|partly_supported|not_supported|not_addressed", "explanation": "1-2 sentences with [S#]"}},
 "counter_facts": [{{"fact": "verified fact with [S#]", "sources": ["S#"]}}],
 "copy": {schema},
 "avoid": ["lines or phrasings the team should not use"]}}"""


def _narrative(conn, cluster_id: str) -> tuple[dict, list[dict]]:
    c = conn.execute("SELECT * FROM narrative_clusters WHERE cluster_id = ?", (cluster_id,)).fetchone()
    if not c:
        raise KeyError(f"narrative {cluster_id} not found")
    items = [dict(r) for r in conn.execute("SELECT * FROM media_items WHERE cluster_id = ? ORDER BY published_at DESC", (cluster_id,))]
    return dict(c), items


def _place(conn, cluster: dict, items: list[dict]) -> dict:
    ids = Counter(i["constituency_id"] for i in items if i["constituency_id"])
    if ids:
        cid = ids.most_common(1)[0][0]
        c = next((c for c in kb.known_constituencies(conn) if c["id"] == cid), None)
        if c:
            return {"id": c["id"], "name": c["name"], "state": c["state"]}
    return {"id": None, "name": cluster["state"] or "National", "state": cluster["state"]}


def generate(conn, cluster_id: str, medium: str, language: str, user_id: str, party_name: str = "",
             spokesperson: str = "", embedder=None, offline: bool = False) -> dict:
    if medium not in MEDIUMS:
        raise ValueError(f"target_medium must be one of {list(MEDIUMS)}")
    language = language if language in LANGUAGES else "en"
    cluster, items = _narrative(conn, cluster_id)
    place = _place(conn, cluster, items)
    t = threat(items, cluster["state"])
    top = sorted(items[:30], key=lambda i: -(i["negativity"] + (i["reach"] or 0) / 1e6))[:8]
    narrative = [{"mid": f"M{n}", **i} for n, i in enumerate(top, 1)]
    q = f"{cluster['label']} {cluster['issue'] or ''} {cluster['claim'] or ''}"
    sources = gather_sources(conn, place, [
        (q, {"k": 6}),
        (f"{cluster['issue'] or cluster['label']} we will commitment", {"doc_types": ["party_promise"], "k": 3}),
        (f"{cluster['issue'] or cluster['label']} allocated sanctioned released cleared directed", {"doc_types": ["budget", "legislative"], "k": 3}),
    ], embedder, limit=10)
    narrative_nums = set().union(*(numbers_in(i["text"]) for i in narrative)) if narrative else set()
    # our own monitoring numbers may appear when we describe the threat
    narrative_nums |= {str(t["score"]), str(t["items_72h"]), str(t["items_24h"]), "72", "24", "100",
                       str(len({i["channel"] for i in items}))}

    base = {"cluster_id": cluster_id, "label": cluster["label"], "state": cluster["state"], "place": place, "medium": medium,
            "medium_label": MEDIUMS[medium], "language": language, "party_name": party_name or None,
            "spokesperson": spokesperson or None, "threat": {"score": t["score"], "severity": t["severity"],
                                                             "items_72h": t["items_72h"], "breakdown": t["breakdown"]},
            "narrative": [{"id": n["mid"], "channel": n["channel"], "source_type": SOURCE_TYPES[n["source_type"]],
                           "published_at": n["published_at"], "text": n["text"], "negativity": n["negativity"]} for n in narrative]}
    provider = get_provider() if sources and not offline else None
    draft, note = None, None
    if provider:
        if not within_limit(user_id):
            note = "Hourly AI limit reached; built from the sources directly."
        else:
            try:
                system = SYSTEM.format(language=LANGUAGES[language], schema=MEDIUM_SCHEMA[medium])
                user = (f"Narrative: {cluster['label']} ({cluster['state'] or 'national'}, {place['name']})\n"
                        f"Threat score {t['score']}/100, {t['items_72h']} items in 72 hours\nOur party: {party_name or 'our party'}\n"
                        f"Spokesperson: {spokesperson or 'party spokesperson'}\nMedium: {MEDIUMS[medium]}\n\nNARRATIVE (claims, not evidence)\n"
                        + "\n".join(f"[{n['mid']}] {SOURCE_TYPES[n['source_type']]}, {n['channel']}: {n['text']}" for n in narrative)
                        + f"\n\nSOURCES\n{sources_block(sources)}")
                draft = _parse_json(provider.complete(system, user, MAX_TOKENS))
                draft["_generator"] = provider.name
            except LLMError as e:
                note = f"AI draft unavailable ({str(e)[:100]}); built from the sources directly."
    if draft is None:
        draft = _offline(cluster, items, narrative, sources, medium, party_name, spokesperson, t)
        draft["_generator"] = "offline"
    out = verify(draft, sources, narrative_nums, medium)
    if draft["_generator"] != "offline" and (not out["counter_facts"] and sources):
        # an AI draft that loses every fact in verification is not usable: fall back
        note = "AI draft failed verification; built from the sources directly."
        draft = _offline(cluster, items, narrative, sources, medium, party_name, spokesperson, t)
        draft["_generator"] = "offline"
        out = verify(draft, sources, narrative_nums, medium)
    out["generator"], out["live"] = draft["_generator"], draft["_generator"] != "offline"
    out["generated_language"] = language if out["live"] else "en"
    if not out["live"] and language != "en":
        out["warnings"].insert(0, f"{LANGUAGES[language]} copy needs an AI provider; this draft is in English.")
    if note:
        out["warnings"].insert(0, note)
    if not sources:
        out["warnings"].insert(0, "No knowledge-base material matches this narrative, so there are no verified facts to "
                                  "counter with. Add documents in JanVani Copilot, then generate again.")
    out["safety_check"] = safety_check(out, medium)
    payload = {**base, **out, "generated_at": now_iso()}
    rid = f"r-{uuid.uuid4().hex[:10]}"
    payload["response_id"], payload["status"] = rid, "draft"
    conn.execute("""INSERT INTO warroom_responses (response_id, cluster_id, medium, language, payload, created_by, created_at)
                    VALUES (?,?,?,?,?,?,?)""", (rid, cluster_id, medium, language, json.dumps(payload, ensure_ascii=False),
                                                user_id, payload["generated_at"]))
    conn.commit()
    return payload


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

class _Gate:
    """Wraps the Copilot verifier and records which safety rules had to intervene."""

    def __init__(self, sources, narrative_nums, verdict):
        self.v = Verifier(sources)
        self.claims = Verifier(sources)
        self.claims.allowed = self.v.allowed | narrative_nums     # opposition figures allowed only when describing their claim
        self.verdict = verdict
        self.hits: Counter = Counter()

    def check(self, value, label, claims=False, need_source=False, extra=(), claim_nums=False):
        """claims: text that describes the opposition's claim (their figures allowed, no denial check).
        claim_nums: our own text that may quote their figures (e.g. 'the Rs 100 crore in circulation')."""
        t = MREF.sub("", re.sub(r"\s+", " ", str(value or ""))).strip()
        if not t:
            return None
        if not claims and (ATTACK.search(t) or PRIVATE.search(t)):
            self.hits["attack"] += 1; self.v.warnings.append(f"Removed a {label} with a personal attack."); return None
        if INDUCE.search(t):
            self.hits["inducement"] += 1; self.v.warnings.append(f"Removed a {label} that read as an inducement or threat."); return None
        if not claims and self.verdict in ("supported", "partly_supported") and DENIAL.search(t):
            self.hits["denial"] += 1
            self.v.warnings.append(f"Removed a {label} that called a documented problem false."); return None
        before = len(self.v.warnings) + len(self.claims.warnings)
        ver = self.claims if (claims or claim_nums) else self.v
        r = ver.text(t, label, need_source=need_source, extra_sources=extra)
        if r is None:
            ws = ver.warnings
            last = ws[-1] if len(self.v.warnings) + len(self.claims.warnings) > before and ws else ""
            self.hits["blocked" if "caste" in last else "figure" if "figures" in last else "unsourced"] += 1
        return r


def verify(d: dict, sources: list[dict], narrative_nums: set[str], medium: str) -> dict:
    a = d.get("assessment") or {}
    verdict = a.get("verdict") if a.get("verdict") in VERDICTS else "not_addressed"
    g = _Gate(sources, narrative_nums, verdict)
    summary = g.check(d.get("threat_summary"), "threat summary", claims=True)
    claim = g.check(d.get("core_claim"), "core claim", claims=True)
    expl = g.check(a.get("explanation"), "verdict explanation", claim_nums=verdict != "supported")
    facts = []
    for f in (d.get("counter_facts") or [])[:5]:
        f = f if isinstance(f, dict) else {"fact": f}
        r = g.check(f.get("fact"), "counter-fact", need_source=True, extra=f.get("sources") or [])
        if r:
            facts.append({"fact": r[0], "sources": r[1]})
    copy = _verify_copy(g, d.get("copy") or {}, medium)
    avoid = [str(x)[:160] for x in (d.get("avoid") or [])[:5] if isinstance(x, str)]
    used = g.v.used | g.claims.used
    return {"threat_summary": summary[0] if summary else "", "core_claim": claim[0] if claim else "",
            "assessment": {"verdict": verdict, "verdict_label": VERDICTS[verdict], "explanation": expl[0] if expl else "",
                           "sources": expl[1] if expl else []},
            "counter_facts": facts, "copy": copy, "avoid": avoid,
            "warnings": list(dict.fromkeys(g.v.warnings + g.claims.warnings)), "_hits": dict(g.hits),
            "sources": source_list(sources, used)}


def _trim(text: str, limit: int, g: _Gate, label: str) -> str:
    if len(text) <= limit:
        return text
    g.v.warnings.append(f"Shortened the {label} to {limit} characters.")
    cut = text[:limit]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;: ") + ("…" if limit > 40 else "")


def _verify_copy(g: _Gate, c: dict, medium: str) -> dict:
    srcs = [s for s in (c.get("sources") or []) if s in g.v.by_id]
    g.v.used.update(srcs)
    if medium == "tv_brief":
        head = g.check(c.get("headline_message"), "headline message")
        pts = [r[0] for r in (g.check(p, "talking point") for p in (c.get("talking_points") or [])[:4]) if r]
        bridges = [r[0] for r in (g.check(p, "bridging line") for p in (c.get("bridging_lines") or [])[:3]) if r]
        qa = []
        for x in (c.get("likely_questions") or [])[:4]:
            if isinstance(x, dict):
                qq, aa = g.check(x.get("q"), "question", claims=True), g.check(x.get("a"), "answer")
                if qq and aa:
                    qa.append({"q": qq[0], "a": aa[0]})
        return {"headline_message": head[0] if head else "", "talking_points": pts, "bridging_lines": bridges, "likely_questions": qa}
    if medium == "whatsapp":
        raw = re.sub(r"[ \t]*\[S\d+\]", "", str(c.get("text") or ""))
        body, note = raw.rstrip(), ""
        m = re.search(r"\n\s*(sources?|स्रोत|ਸਰੋਤ)\s*:.*$", body, re.I)
        if m:                                  # the source note names documents; it is not a claim
            body, note = body[:m.start()], body[m.start():].strip()[:200]
        lines = []
        for line in body.split("\n"):
            if not line.strip():
                lines.append(""); continue
            r = g.check(line, "WhatsApp line")
            if r:
                bullet = re.match(r"^\s*([•\-*]\s*)", line)
                lines.append((bullet.group(1) if bullet and not r[0].startswith(bullet.group(1).strip()) else "") + r[0])
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        text = _trim(text, LIMITS["whatsapp"] - len(note) - 2, g, "WhatsApp copy") if text else ""
        text = f"{text}\n\n{note}" if text and note else text
        return {"text": text, "characters": len(text), "sources": srcs}
    if medium == "press_release":
        head = g.check(c.get("headline"), "headline")
        body = [r[0] for r in (g.check(p, "paragraph") for p in (c.get("body") or [])[:5]) if r]
        q = c.get("quote") or {}
        qt = g.check(q.get("text") if isinstance(q, dict) else q, "quote")
        return {"headline": head[0] if head else "", "body": body, "sources": srcs,
                "quote": {"text": qt[0] if qt else "", "attribution": str((q or {}).get("attribution") or "")[:80] if isinstance(q, dict) else ""}}
    variants = []
    for v in (c.get("variants") or [])[:4]:
        if not isinstance(v, dict):
            continue
        h, b, cta = (g.check(re.sub(r"\s*\[S\d+\]", "", str(v.get(k) or "")), f"ad {k}") for k in ("headline", "body", "cta"))
        if h and b:
            variants.append({"headline": _trim(h[0], LIMITS["ad_headline"], g, "ad headline"),
                             "body": _trim(b[0], LIMITS["ad_body"], g, "ad body"),
                             "cta": _trim(cta[0], LIMITS["ad_cta"], g, "call to action") if cta else "Learn more"})
    return {"variants": variants, "sources": srcs}


def safety_check(out: dict, medium: str) -> dict:
    h = out.pop("_hits", {})

    def rule(name, hits_key, ok, fixed):
        n = sum(h.get(k, 0) for k in hits_key)
        return {"rule": name, "status": "fixed" if n else "pass", "detail": fixed.format(n=n) if n else ok}

    checks = [
        rule("Facts and figures are sourced", ["figure", "unsourced"], "Every figure we assert appears in a cited source.",
             "{n} item(s) with unsourced claims or figures were removed."),
        {"rule": "Opposition claims are not used as evidence", "status": "pass",
         "detail": "Narrative items are quoted as claims only; citations point at knowledge-base sources."},
        rule("No denial of documented facts", ["denial"],
             "Where our records confirm the problem, the copy acknowledges it.",
             "{n} line(s) calling a documented problem false were removed."),
        rule("No personal attacks or private-life remarks", ["attack"], "Critique sticks to policy and the public record.",
             "{n} line(s) with personal attacks were removed."),
        rule("No appeals to caste, religion, community or language", ["blocked"], "No identity-based appeals.",
             "{n} line(s) with identity references were removed."),
        rule("No inducements, threats or intimidation", ["inducement"], "None found.", "{n} line(s) were removed."),
    ]
    if medium == "digital_ad":
        checks.append({"rule": "Pre-certification", "status": "action",
                       "detail": "Political ads on social media, TV, radio, cinema and e-papers need MCMC pre-certification before release, and the spend counts toward the candidate's account."})
    elif medium == "whatsapp":
        checks.append({"rule": "Pre-certification", "status": "info",
                       "detail": "Paid or bulk-messaging broadcasts count as election advertising: get MCMC pre-certification and record the expense."})
    elif medium == "tv_brief":
        checks.append({"rule": "Pre-certification", "status": "info",
                       "detail": "News interviews need no pre-certification; any paid TV spot made from this brief does."})
    checks += [
        {"rule": "48-hour silence period", "status": "info",
         "detail": "Do not publish election matter in a constituency during the 48 hours before polling closes there (s.126, Representation of the People Act)."},
        {"rule": "Human approval", "status": "action", "detail": "A war-room lead must approve this draft before release."},
    ]
    if not out["counter_facts"]:
        checks.insert(0, {"rule": "Verified counter-facts available", "status": "fail",
                          "detail": "No verified facts: issue a holding statement only, and add sources."})
    status = "blocked" if any(c["status"] == "fail" for c in checks) else (
        "clear_with_fixes" if any(c["status"] == "fixed" for c in checks) else "clear")
    return {"status": status, "checks": checks}


# ---------------------------------------------------------------------------
# offline drafting
# ---------------------------------------------------------------------------

ISSUE_RX = {}
for _pat, _title, _ in ISSUES + EXTRA_ISSUES:
    ISSUE_RX[_title] = f"{ISSUE_RX[_title]}|{_pat}" if _title in ISSUE_RX else _pat
ISSUE_RX = {k: re.compile(rf"(?<![a-z])(?:{v})", re.I) for k, v in ISSUE_RX.items()}


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _cap(x: str) -> str:
    return x[:1].upper() + x[1:]


def _offline(cluster, items, narrative, sources, medium, party_name, spokesperson, t) -> dict:
    """Extractive draft: the record that matches the narrative, plus our sourced commitments."""
    party = party_name or "our party"
    who = spokesperson or "Party spokesperson"
    issue = (cluster["issue"] or cluster["label"]).lower()
    claim = cluster["claim"] or (items[0]["text"] if items else "")
    claim_nums = {n for n in numbers_in(claim) if not (n.isdigit() and 1990 <= int(n) <= 2040) and float(n) >= 10}
    placewords = set(kb.tokens(" ".join(filter(None, {i["constituency"] for i in items} | {i["state"] for i in items}))))
    want = set(kb.tokens(f"{cluster['label']} {cluster['issue'] or ''} {claim}")) - placewords
    rx = ISSUE_RX.get(cluster["issue"] or "")
    near = rx.pattern if rx else None

    def relevant(x):
        hits = len(want & set(kb.tokens(x)))
        return hits + (3 if rx and rx.search(x) else 0)

    def clause(x):
        f = _figure(x, near)
        x = _clause(x, f) if f else x
        return x if x.endswith((".", "।", "!")) else x + "."

    local = [s for s in sources if s["doc_type"] != "party_promise"]
    promises = [s for s in sources if s["doc_type"] == "party_promise"]
    record = sorted(((relevant(x), x, s) for s in local for x in _sentences(s["text"]) if LAG_WORDS.search(x) or RECORD.search(x)),
                    key=lambda r: -r[0])
    record = [r for r in record if r[0] >= 3]
    facts, seen = [], set()

    def add(x, s):
        x = clause(x)
        if x.lower() not in seen:
            seen.add(x.lower()); facts.append({"fact": f"{x} [{s['sid']}]", "sources": [s["sid"]], "_text": x})

    if record:
        _, rec, rsrc = record[0]
        add(rec, rsrc)
        all_nums = set().union(*(numbers_in(s["text"]) for s in sources))
        if claim_nums and not claim_nums <= all_nums:
            verdict = "partly_supported"
            expl = (f"The problem is real, but the figures in circulation ({', '.join(sorted(claim_nums)[:3])}) are not in our records. "
                    f"{facts[0]['fact']}")
        else:
            verdict, expl = "supported", f"Our own records confirm the problem. {facts[0]['fact']}"
    else:
        verdict, expl = "not_addressed", "Our records neither confirm nor contradict the claim directly."
    steps = sorted(((relevant(x), x, s) for s in local for x in _sentences(s["text"])
                    if ACTION.search(x) and not LAG_WORDS.search(x) and not RECORD.search(x)),
                   key=lambda r: -r[0])
    for score, x, s in steps[:2]:
        if score >= 3:
            add(x, s)
    pledges = sorted(((relevant(x), x, s) for s in promises for x in _sentences(s["text"])), key=lambda r: -r[0])
    pledge = next(((x, s) for score, x, s in pledges if score >= 3), None)
    if pledge:
        add(*pledge)
        pledge_text = facts[-1]["_text"]
    else:
        pledge_text = ""
    for f in facts:
        f.pop("_text")
    if verdict == "not_addressed" and not facts:
        expl = "The knowledge base has nothing on this narrative yet."
    plain = [re.sub(r"\s*\[S\d+\]", "", f["fact"]) for f in facts]
    evidence = plain[:-1] if pledge else plain
    srcs = [f["sources"][0] for f in facts]
    channels = len({i["channel"] for i in items})
    mix = ", ".join(sorted({SOURCE_TYPES[i["source_type"]] for i in items}))
    summary = (f"“{cluster['label']}” is spreading: {_plural(t['items_72h'], 'item')} in 72 hours across "
               f"{_plural(channels, 'channel')} ({mix}), threat {t['score']}/100. The loudest version: {claim}")
    ack = (f"People are right to be worried about {issue}." if verdict in ("supported", "partly_supported")
           else f"Here are the facts on {issue}.")
    cites = "; ".join(dict.fromkeys(re.sub(r"\s*\([^)]*$", "", next(s for s in sources if s["sid"] == x)["citation"].split(",")[0])
                                    for x in srcs))
    if not facts:
        holding = (f"We have seen the reports on {issue}. Our team is checking the facts with the officials concerned and "
                   f"will share a verified update.")
        copy = {"tv_brief": {"headline_message": holding, "talking_points": [], "bridging_lines":
                             ["We will respond with verified facts, not speculation."], "likely_questions": []},
                "whatsapp": {"text": holding, "sources": []},
                "press_release": {"headline": f"Statement on {issue}", "body": [holding], "sources": [],
                                  "quote": {"text": "We take every complaint seriously and will report back with facts.",
                                            "attribution": who}},
                "digital_ad": {"variants": [], "sources": []}}[medium]
    elif medium == "tv_brief":
        copy = {"headline_message": f"{ack} {'Here is the record and what we will do.' if pledge else 'Here is the record.'}",
                "talking_points": [f["fact"] for f in facts[:4]],
                "bridging_lines": [f"The real question is how fast this gets fixed{', and here is our plan' if pledge else ''}.",
                                   "Let's stay with what is on the record."],
                "likely_questions": [{"q": f"Reports say: “{claim}” What is your response?",
                                      "a": f"{ack} {facts[0]['fact']}" + (f" {facts[-1]['fact']}" if pledge and len(facts) > 1 else "")}]}
    elif medium == "whatsapp":
        lines = [f"*{cluster['label'].split(':')[0]}: the facts*", ""] + [f"• {p}" for p in evidence[:3]]
        if pledge_text:
            lines += ["", f"{_cap(party)}'s commitment: {pledge_text}"]
        copy = {"text": "\n".join(lines) + (f"\n\nSource: {cites}" if cites else ""), "sources": srcs}
    elif medium == "press_release":
        copy = {"headline": f"{_cap(party)} sets out the facts and next steps on {issue}",
                "body": [f"{ack} {facts[0]['fact']}"] + [f["fact"] for f in facts[1:-1 if pledge else 3]]
                        + ([f"{_cap(party)} commits: {facts[-1]['fact']}"] if pledge and len(facts) > 1 else []),
                "quote": {"text": f"We will not play politics with {issue}. We will publish progress every week until it is resolved.",
                          "attribution": who}, "sources": srcs}
    else:
        short = issue[:26]

        def fit(x, n=120):
            return x if len(x) <= n else x[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        variants = [{"headline": "Facts, not noise", "body": fit(evidence[0] if evidence else plain[0]), "cta": "See the record"}]
        if pledge_text:
            variants.insert(0, {"headline": fit(f"{_cap(short)}: our plan", 40), "body": fit(pledge_text), "cta": "Read the plan"})
        variants.append({"headline": fit(f"Fixing {short}", 40), "body": fit(f"{_cap(party)} will publish weekly progress on {issue}."),
                         "cta": "Follow updates"})
        copy = {"variants": variants, "sources": srcs}
    return {"threat_summary": summary, "core_claim": claim, "assessment": {"verdict": verdict, "explanation": expl},
            "counter_facts": facts, "copy": copy,
            "avoid": ["Calling the protests staged or politically motivated", "Figures that are not in the cited sources",
                      "Personal remarks about opposition leaders"]}
