"""JanVani Copilot generation: grounded answers, speech/briefing documents and
micro-manifestos.

Every generator works the same way:
  1. retrieve chunks for the constituency (plus state-wide and central material)
  2. number them S1..Sn and give them to the model with strict rules
  3. verify the reply: citations must point at real sources, every figure must
     appear in a source, critique must be cited and free of attack language,
     nothing may appeal to caste or religion
  4. if no LLM is configured, or the reply fails verification, build the same
     structure extractively from the sources (offline generator)
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import defaultdict

from ..db import now_iso
from ..llm import LLMError, get_provider
from . import kb

EVENT_TYPES = {"rally_speech": "Rally speech", "press_conference": "Press conference",
               "door_to_door": "Door-to-door talking points", "micro_manifesto": "Micro-manifesto"}
AUDIENCES = {
    "farmers": ("Farmers", "Agriculture", "farmers crop cane sugarcane mandi irrigation tubewell canal cattle procurement payment"),
    "youth": ("Youth", "Youth", "youth young jobs employment skill iti college recruitment migrate work"),
    "women": ("Women", "Welfare", "women safety street lights ration health self help group anganwadi"),
    "scheme_beneficiaries": ("Scheme beneficiaries", "Welfare", "scheme beneficiaries pension ration pm-kisan e-kyc housing instalment"),
}
LANGUAGES = {"en": "English", "hi": "Hindi", "pa": "Punjabi"}
LAG_WORDS = re.compile(r"\b(pending|delay(?:ed|s)?|unspent|not (?:been )?(?:started|begun|finalised|completed|lifted)|"
                       r"incomplete|lapsed|shortfall|still at|remain(?:s|ed)? (?:unpaid|incomplete|pending)|only|slow|late)\b", re.I)
BLOCKED = re.compile(
    r"\b(caste|castes|jati|biradari|religion|religious|communal|hindus?|muslims?|sikhs?|christians?|dalits?|brahmins?|"
    r"thakurs?|rajputs?|yadavs?|jatavs?|jats?|obc|deras?|mandir|masjid|gurdwara)\b", re.I)
ATTACK = re.compile(r"\b(corrupt(?:ion)?|thie(?:f|ves)|liars?|criminals?|goondas?|chor|looters?|traitors?|anti-national|scam)\b", re.I)
NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])")
LIVE_LIMIT_PER_HOUR = 60
MAX_TOKENS = 3000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def numbers_in(text: str) -> set[str]:
    return {m.replace(",", "") for m in NUM.findall(kb.ascii_digits(str(text or "")))}


def unsupported_numbers(text: str, allowed: set[str]) -> list[str]:
    """Figures in `text` that appear in no source. Years and single digits are exempt
    (counts like 'three issues', 'in 2027')."""
    bad = []
    for n in numbers_in(text):
        try:
            v = float(n)
        except ValueError:
            continue
        if v < 10 and "." not in n:
            continue
        if n.isdigit() and 1990 <= v <= 2040:
            continue
        if n not in allowed and n.rstrip("0").rstrip(".") not in allowed:
            bad.append(n)
    return bad


_MARK = re.compile(r"\[(S\d+)\]")


def markers(text: str) -> list[str]:
    return _MARK.findall(text or "")


def clean_markers(text: str, valid: set[str]) -> str:
    return re.sub(r"\s*\[(S\d+)\]", lambda m: f" [{m.group(1)}]" if m.group(1) in valid else "", text or "").strip()


_calls: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()


def within_limit(user_id: str) -> bool:
    with _lock:
        now = time.time()
        _calls[user_id] = [t for t in _calls[user_id] if now - t < 3600]
        if len(_calls[user_id]) >= LIVE_LIMIT_PER_HOUR:
            return False
        _calls[user_id].append(now)
        return True


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise LLMError("no JSON in model reply")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        raise LLMError("model reply was not valid JSON") from None


def _split(text: str) -> list[str]:
    """Plain sentence split for verifying model text (keeps every sentence)."""
    return [x.strip() for x in re.split(r"(?<=[.!?।])\s+(?![^\[]*\])", text or "") if x.strip()]


def _sentences(text: str) -> list[str]:
    """Sentences worth quoting from a source (drops questions and fragments)."""
    out = []
    for x in re.split(r"(?<=[.!?।])\s+", text or ""):
        x = re.sub(r"^(Reply|Answer|Uttar|उत्तर)\s*:\s*", "", x.strip())
        if len(x) > 20 and not x.endswith("?") and not x.lower().startswith("question"):
            out.append(x)
    return out


_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_FIG_STRONG = re.compile(r"(\bRs\.?\s?\d[\d,.]*(?:\s?(?:crore|lakh))?|₹\s?\d[\d,.]*(?:\s?(?:crore|lakh))?|\d[\d,.]*\s?%|\d[\d,.]*\s?(?:crore|lakh))", re.I)
_FIG_COUNT = re.compile(r"(\d{1,3}(?:,\d{2,3})+|\d{2,})(\s+(?:[a-z-]+\s){0,1}[a-z-]+)?", re.I)


def _figure(sentence: str, near: str | None = None) -> str:
    """The most meaningful figure in a sentence: money / percentages first, then counts
    with their noun ('9,400 cane growers'); never years or calendar dates. With `near`
    (a regex), prefer the figure closest to that phrase — a sentence often carries two stats."""
    strong = list(_FIG_STRONG.finditer(sentence))
    if strong:
        if near and len(strong) > 1:
            k = re.search(near, sentence.lower())
            if k:
                return min(strong, key=lambda m: abs(m.start() - k.start())).group(1).strip()
        return strong[0].group(1).strip()
    for m in _FIG_COUNT.finditer(sentence):
        n = int(m.group(1).replace(",", ""))
        after = sentence[m.end(1):m.end(1) + 12].lower()
        if 1990 <= n <= 2040 or re.match(rf"\s*{_MONTHS}", after) or re.search(rf"{_MONTHS}\s*$", sentence[:m.start()].lower()):
            continue
        noun = re.sub(r"\s+(?:for|to|of|in|at|on|by|with|and|the|a|an|from|were|was|are|is)$", "", m.group(2) or "", flags=re.I)
        return (m.group(1) + noun).strip()
    return ""


def _clause(sentence: str, figure: str) -> str:
    """The clause of a sentence that carries a figure: '41% reported stray cattle damaging crops'."""
    parts = [x.strip(" ,;.") for x in re.split(r";|,\s(?=(?:and|but|while)\b)|\sand\s(?=\S*\d)|\.\s", sentence) if x.strip()]
    hit = next((x for x in parts if figure and figure in x), sentence)
    return hit[0].upper() + hit[1:] if hit else sentence


ISSUES = [  # (pattern, issue title, topic) — used by the offline generator to name grievances
    (r"cane (?:dues|payments?)|mill dues|unpaid", "Delayed cane payments", "Agriculture"),
    (r"canal|desilting|irrigation|tubewell", "Irrigation shortfalls", "Agriculture"),
    (r"stray cattle", "Stray cattle damaging crops", "Agriculture"),
    (r"wild animals?", "Crop damage from wild animals", "Agriculture"),
    (r"lifting|procurement|mandi|grain market", "Slow mandi procurement and payments", "Agriculture"),
    (r"groundwater", "Falling groundwater", "Agriculture"),
    (r"pm-kisan|e-kyc", "Stopped PM-KISAN instalments", "Welfare"),
    (r"ration|fair price shop", "Irregular ration delivery", "Welfare"),
    (r"unlit|unsafe|street ?lights?", "Unsafe, unlit roads at night", "Welfare"),
    (r"health centre|hospital|lady doctor|opd", "Health care too far away", "Welfare"),
    (r"fishing ban|fishing households", "Late support during the fishing ban", "Welfare"),
    (r"drug", "Drug abuse among young people", "Youth"),
    (r"iti|skill centre|skilling|instructors|seats", "Too few skilling seats", "Youth"),
    (r"local jobs|job seekers|employment|placed", "Lack of local jobs", "Youth"),
    (r"migrat|move to|outside goa|abroad|working abroad", "Young people leaving for work", "Youth"),
    (r"drain|waterlogg|overflow", "Drainage and waterlogging", "Infrastructure"),
    (r"water supply|piped water|pipeline", "Irregular water supply", "Infrastructure"),
    (r"garbage|solid waste", "Missed garbage collection", "Infrastructure"),
    (r"roads? block\w*|monsoon", "Roads blocked in the monsoon", "Infrastructure"),
    (r"road", "Unfinished road works", "Infrastructure"),
    (r"sewage", "Pending sewage connection", "Infrastructure"),
]


def issues_of(sentence: str) -> list[tuple[str, str]]:
    low = sentence.lower()
    return [(title, topic) for pat, title, topic in ISSUES if re.search(pat, low)]


ISSUE_PAT = {title: pat for pat, title, _ in ISSUES}


def issue_of(sentence: str) -> tuple[str, str] | None:
    found = issues_of(sentence)
    return found[0] if found else None


# ---------------------------------------------------------------------------
# retrieval + source numbering
# ---------------------------------------------------------------------------

def gather_sources(conn, place: dict, queries: list[tuple[str, dict]], embedder=None, limit: int = 14) -> list[dict]:
    """Run several focused searches, de-duplicate, and number the chunks S1..Sn."""
    seen, out = set(), []
    for q, opts in queries:
        for r in kb.search(conn, q, place["state"], place.get("id"), embedder=embedder,
                           booth_ids=place.get("booth_ids"), **opts):
            if r["chunk_id"] not in seen:
                seen.add(r["chunk_id"]); out.append(r)
    out = out[:limit]
    for i, r in enumerate(out, 1):
        r["sid"] = f"S{i}"
    return out


def source_list(sources: list[dict], used: set[str] | None = None) -> list[dict]:
    return [{"id": s["sid"], "chunk_id": s["chunk_id"], "doc_id": s["doc_id"], "title": s["title"], "doc_type": s["doc_type"],
             "citation": s["citation"], "page": s["page"], "published_on": s["published_on"], "url": s.get("source_url"),
             "snippet": s["snippet"], "quote": s["text"][:600], "used": (used is None or s["sid"] in used)}
            for s in sources]


def sources_block(sources: list[dict]) -> str:
    return "\n\n".join(f"[{s['sid']}] {s['title']} | {kb.DOC_TYPES[s['doc_type']]} | "
                       f"{s['constituency'] or (s['state'] or 'National')} | {s['published_on'] or 'undated'} | "
                       f"{s['citation']}\n{s['text']}" for s in sources)


RULES = """Rules:
1. Ground every factual statement and every number in SOURCES and cite it right after the claim as [S#]. Copy figures exactly. Never invent figures, dates, quotes, events or names. If the sources do not support something, leave it out.
2. Critique only the record and policies of the incumbent or opponent, using sourced facts (delays, unspent or lapsed funds, unmet announcements). No personal attacks, no allegations of crime or corruption, nothing about private life.
3. Never appeal to caste, religion, community or language identity. Never promise money, gifts or goods to individual voters. Commitments must say how they will be delivered or funded (Election Commission manifesto guidelines).
4. Use plain, respectful language suitable for public use. Write in {language}; keep [S#] markers unchanged.
5. Reply with JSON only, in exactly the schema given."""


# ---------------------------------------------------------------------------
# verification shared by briefs and manifestos
# ---------------------------------------------------------------------------

class _Notes(list):
    """Review notes without repeats."""
    def append(self, item):
        if item not in self:
            super().append(item)


class Verifier:
    def __init__(self, sources: list[dict]):
        self.by_id = {s["sid"]: s for s in sources}
        self.allowed = set().union(*(numbers_in(s["text"]) for s in sources)) if sources else set()
        self.warnings: list[str] = _Notes()
        self.used: set[str] = set()

    def text(self, value, label: str, need_source=False, extra_sources=()) -> tuple[str, list[str]] | None:
        t = re.sub(r"\s+", " ", str(value or "")).strip()
        if not t:
            return None
        valid = set(self.by_id)
        srcs = [s for s in dict.fromkeys(markers(t) + [x for x in extra_sources if isinstance(x, str)]) if s in valid]
        t = clean_markers(t, valid)
        if BLOCKED.search(t):
            self.warnings.append(f"Removed a {label} that referred to caste or religion."); return None
        bad = unsupported_numbers(t, self.allowed)
        if bad:
            self.warnings.append(f"Removed a {label} with figures not found in the sources ({', '.join(bad[:3])})."); return None
        if need_source and not srcs:
            self.warnings.append(f"Removed a {label} with no source."); return None
        self.used.update(srcs)
        return t, srcs

    def stat(self, s: dict) -> dict | None:
        fig, src = str(s.get("figure") or "").strip(), s.get("source")
        if src not in self.by_id or not fig:
            return None
        nums = numbers_in(fig)
        if not nums or not nums <= numbers_in(self.by_id[src]["text"]):
            self.warnings.append(f"Dropped the statistic “{fig[:40]}”: not found in {src}."); return None
        ctx = self.text(s.get("context"), "statistic", extra_sources=[src])
        self.used.add(src)
        return {"figure": fig[:60], "context": ctx[0] if ctx else "", "source": src}


def _place_label(place: dict) -> str:
    if place.get("booth_ids"):
        return f"{place['name']} (booths {', '.join(place['booth_ids'][:6])}{'…' if len(place['booth_ids']) > 6 else ''})"
    return place["name"]


# ---------------------------------------------------------------------------
# speech / briefing document
# ---------------------------------------------------------------------------
BRIEF_SCHEMA = """{"executive_summary": "3-4 sentences with [S#] markers",
 "grievances": [{"title": "short", "detail": "1-2 sentences with [S#]", "stats": [{"figure": "exact figure from source", "context": "what it measures", "source": "S#"}], "sources": ["S#"]}],
 "opponent_critique": [{"point": "record-based critique with [S#]", "sources": ["S#"]}],
 "commitments": [{"commitment": "what we will do", "delivery": "how it will be delivered or funded", "type": "party|local", "sources": ["S#"]}],
 "event_section": {"title": "...", "items": [{"text": "...", "sources": ["S#"]}]}}"""

EVENT_GUIDE = {
    "rally_speech": "event_section: title 'Speech outline'; 6-8 items in speaking order: greeting, each grievance as a short spoken passage, the critique, the commitments, a closing call to vote. Spoken, warm, short sentences.",
    "press_conference": "event_section: title 'Key messages and likely questions'; 3 key messages, then 3 questions journalists may ask, each with a short sourced answer.",
    "door_to_door": "event_section: title 'Talking points at the door'; 5 short items a volunteer can say or ask, listening-led.",
    "micro_manifesto": "event_section: title 'Micro-manifesto'; 5-6 numbered local commitments with delivery notes.",
}


def brief(conn, place: dict, event_type: str, audience: str, language: str, user_id: str,
          party_name: str = "", opponent: str = "", focus: str = "", embedder=None, offline: bool = False) -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"event_type must be one of {list(EVENT_TYPES)}")
    if audience not in AUDIENCES:
        raise ValueError(f"target_audience must be one of {list(AUDIENCES)}")
    language = language if language in LANGUAGES else "en"
    a_label, a_topic, a_query = AUDIENCES[audience]
    q = f"{a_query} {place['name']} {focus}".strip()
    sources = gather_sources(conn, place, [
        (q, {"doc_types": ["survey", "news", "legislative", "budget", "other"], "k": 7}),
        (f"pending delayed unspent incomplete lapsed not started {a_query}", {"doc_types": ["budget", "legislative", "news"], "k": 4}),
        (f"{a_query} we will", {"doc_types": ["party_promise"], "k": 3}),
    ], embedder)
    meta = {"constituency": {"id": place.get("id"), "name": _place_label(place), "state": place["state"]},
            "event_type": event_type, "event_label": EVENT_TYPES[event_type], "audience": audience,
            "audience_label": a_label, "language": language, "party_name": party_name or None,
            "opponent": opponent or None, "focus": focus or None}
    if not sources:
        raise ValueError("No knowledge base material for this constituency yet. Upload documents first.")

    provider = None if offline else get_provider()
    result, note = None, None
    if provider:
        if not within_limit(user_id):
            note = "Hourly AI limit reached; built from the sources directly."
        else:
            try:
                system = ("You prepare grounded campaign briefing documents for an Indian election campaign.\n"
                          + RULES.format(language=LANGUAGES[language]) + "\n\nSchema:\n" + BRIEF_SCHEMA
                          + "\n\nExactly 3 grievances, 1-2 critique points, 3-5 commitments. " + EVENT_GUIDE[event_type])
                user = (f"Constituency: {_place_label(place)}, {place['state']}\nEvent: {EVENT_TYPES[event_type]}\n"
                        f"Audience: {a_label}\nOur party: {party_name or 'our party'}\nOpponent: {opponent or 'the incumbent'}\n"
                        f"Campaign focus: {focus or 'none given'}\n\nSOURCES\n{sources_block(sources)}")
                result = _verify_brief(_parse_json(provider.complete(system, user, MAX_TOKENS)), sources)
                if len(result["grievances"]) < 2 or not result["commitments"]:
                    raise LLMError("too little verified content")
                result.update(generator=provider.name, live=True, generated_language=language)
            except LLMError as e:
                note = f"AI draft unavailable ({str(e)[:100]}); built from the sources directly."
                result = None
    if result is None:
        result = _offline_brief(sources, place, event_type, audience, party_name, opponent)
        result.update(generator="offline", live=False, generated_language="en")
        if language != "en":
            result["warnings"].insert(0, f"{LANGUAGES[language]} drafts need an AI provider; this draft is in English.")
    if note:
        result["warnings"].insert(0, note)
    payload = {**meta, **result, "generated_at": now_iso()}
    payload["output_id"] = save(conn, "brief", place.get("id"), language, meta, payload, user_id)
    return payload


def _verify_brief(data: dict, sources: list[dict]) -> dict:
    v = Verifier(sources)
    out = {"warnings": v.warnings}
    s = v.text(data.get("executive_summary"), "summary sentence")
    if not s:   # keep the sentences that pass rather than losing the whole summary
        kept = [x for x in (v.text(t, "summary sentence") for t in _split(str(data.get("executive_summary") or ""))) if x]
        s = (" ".join(k[0] for k in kept), sorted({y for k in kept for y in k[1]})) if kept else ("", [])
    out["executive_summary"] = {"text": s[0], "sources": s[1]}
    gs = []
    for g in (data.get("grievances") or [])[:3]:
        if not isinstance(g, dict):
            continue
        d = v.text(g.get("detail"), "grievance", need_source=True, extra_sources=g.get("sources") or [])
        t = v.text(g.get("title"), "grievance title")
        if not d or not t:
            continue
        stats = [x for x in (v.stat(st) for st in (g.get("stats") or [])[:3] if isinstance(st, dict)) if x]
        gs.append({"title": t[0][:80], "detail": d[0], "stats": stats, "sources": sorted(set(d[1]) | {x["source"] for x in stats})})
    out["grievances"] = gs
    crit = []
    for c in (data.get("opponent_critique") or [])[:2]:
        if not isinstance(c, dict):
            continue
        if ATTACK.search(str(c.get("point"))):
            v.warnings.append("Removed a critique point with attack language."); continue
        p = v.text(c.get("point"), "critique point", need_source=True, extra_sources=c.get("sources") or [])
        if p:
            crit.append({"point": p[0], "sources": p[1]})
    out["opponent_critique"] = crit
    coms = []
    for c in (data.get("commitments") or [])[:5]:
        if not isinstance(c, dict):
            continue
        t = v.text(c.get("commitment"), "commitment", extra_sources=c.get("sources") or [])
        dl = v.text(c.get("delivery"), "delivery note")
        if t:
            coms.append({"commitment": t[0], "delivery": dl[0] if dl else "", "type": "party" if c.get("type") == "party" else "local",
                         "sources": t[1]})
    out["commitments"] = coms
    ev = data.get("event_section") or {}
    items = []
    for it in (ev.get("items") or [])[:8]:
        it = it if isinstance(it, dict) else {"text": it}
        r = v.text(it.get("text"), "outline item", extra_sources=it.get("sources") or [])
        if r:
            items.append({"text": r[0], "sources": r[1]})
    out["event_section"] = {"title": str(ev.get("title") or "Outline")[:60], "items": items}
    out["sources"] = source_list(sources, v.used)
    return out


def _offline_brief(sources, place, event_type, audience, party_name, opponent) -> dict:
    a_label, a_topic, a_query = AUDIENCES[audience]
    want = set(kb.tokens(a_query))
    party = party_name or "our party"
    opp = opponent or "the incumbent"
    pool = [s for s in sources if s["doc_type"] != "party_promise"]
    promises = [s for s in sources if s["doc_type"] == "party_promise"]

    def rel(sent):
        return len(want & set(kb.tokens(sent))) + (0.6 if re.search(r"\d", sent) else 0)

    # candidate (issue, sentence, source) triples, best first; one grievance per issue, prefer distinct documents
    cands = []
    for s in pool:
        for sent in _sentences(s["text"]):
            for iss in issues_of(sent):
                cands.append((rel(sent) + s["score"] + (1.2 if iss[1] == a_topic else 0) + (0.5 if _figure(sent) else 0), iss, sent, s))
    cands.sort(key=lambda x: -x[0])
    grievances, seen_issue, doc_uses, used_sents = [], set(), defaultdict(int), set()
    while len(grievances) < 3:
        live = [c for c in cands if c[1][0] not in seen_issue]
        if not live:
            break
        _, (title, topic), sent, s = max(live, key=lambda c: c[0] - 0.8 * doc_uses[c[3]["doc_id"]])
        seen_issue.add(title); doc_uses[s["doc_id"]] += 1; used_sents.add(sent)
        near = ISSUE_PAT[title]
        extra = [x for x in _sentences(s["text"]) if x != sent and title in [i[0] for i in issues_of(x)] and _figure(x)]
        stats = [{"figure": f, "context": _clause(x, f), "source": s["sid"]}
                 for x in ([sent] + extra) for f in [_figure(x, near)] if f][:2]
        grievances.append({"title": title, "topic": topic, "detail": f"{sent} [{s['sid']}]", "stats": stats, "sources": [s["sid"]]})
    opp_label = opp[0].upper() + opp[1:]
    crit = []
    for _, _, sent, s in sorted(cands, key=lambda x: -x[0]) + [(0, None, x, s) for s in pool for x in _sentences(s["text"])]:
        if LAG_WORDS.search(sent) and sent not in used_sents and all(sent not in c["point"] for c in crit) and len(crit) < 2:
            crit.append({"point": f"On the record: {sent} [{s['sid']}] {opp_label} should explain why.", "sources": [s["sid"]]})
    commitments = []
    for s in promises:
        for sent in sorted(_sentences(s["text"]), key=rel, reverse=True):
            if sent.lower().startswith("we will") and rel(sent) > 0 and len(commitments) < 3:
                commitments.append({"commitment": f"{sent} [{s['sid']}]", "delivery": f"Part of {party}'s published commitments.",
                                    "type": "party", "sources": [s["sid"]]})
    LOCAL = {"Agriculture": "Hold a monthly farmers' review in {c} on {i}, and publish what was resolved",
             "Infrastructure": "Publish a ward-by-ward timeline for {i} in {c} and inspect progress with residents every month",
             "Youth": "Set up a {c} jobs and skills desk that follows up on {i} with the district employment office",
             "Welfare": "Run doorstep help camps in {c} to resolve {i} case by case"}
    for g in grievances[: max(1, 5 - len(commitments))]:
        commitments.append({"commitment": LOCAL.get(g["topic"], LOCAL["Welfare"]).format(c=place["name"], i=g["title"].lower()),
                            "delivery": "Through the MLA's office, with a public progress review each month.",
                            "type": "local", "sources": g["sources"]})
    titles = [g["title"].lower() for g in grievances]
    summary = (f"For {a_label.lower()} in {place['name']}, the local record points to "
               f"{', '.join(titles[:-1]) + ' and ' + titles[-1] if len(titles) > 1 else (titles[0] if titles else 'few documented issues')}. "
               + " ".join(f"{g['detail']}" for g in grievances[:1])
               + f" {party[0].upper() + party[1:]} can answer each with a concrete local commitment.")
    items = _offline_event_section(event_type, place, a_label, grievances, crit, commitments)
    return {"warnings": ["Built directly from the sources (no AI provider configured). Edit the wording before public use."],
            "executive_summary": {"text": summary, "sources": sorted({x for g in grievances for x in g["sources"]})},
            "grievances": [{k: v for k, v in g.items() if k != "topic"} for g in grievances], "opponent_critique": crit, "commitments": commitments, "event_section": items,
            "sources": source_list(sources, {x for g in grievances for x in g["sources"]} | {x for c in crit for x in c["sources"]}
                                   | {x for c in commitments for x in c["sources"]})}


def _offline_event_section(event_type, place, a_label, grievances, crit, commitments) -> dict:
    def it(text, srcs=()):
        return {"text": text, "sources": list(srcs)}
    if event_type == "rally_speech":
        items = [it(f"Greet the gathering in {place['name']} and thank the {a_label.lower()} who came.")]
        items += [it(f"{g['detail']}", g["sources"]) for g in grievances]
        items += [it(c["point"], c["sources"]) for c in crit[:1]]
        items += [it(f"Our promise: {c['commitment']}", c["sources"]) for c in commitments[:3]]
        items.append(it("Close: ask everyone to vote, and to bring one neighbour to the booth."))
        return {"title": "Speech outline", "items": items}
    if event_type == "press_conference":
        items = [it(f"Key message: {g['detail']}", g["sources"]) for g in grievances]
        items += [it(f"Likely question: what will you do about {g['title'].lower()}? Answer: {c['commitment']}", c["sources"])
                  for g, c in zip(grievances, commitments)]
        return {"title": "Key messages and likely questions", "items": items}
    if event_type == "door_to_door":
        items = [it(f"Ask whether this affects the family: {g['title'].lower()}.", g["sources"]) for g in grievances]
        items += [it(f"Share one commitment: {commitments[0]['commitment']}", commitments[0]["sources"])] if commitments else []
        items.append(it("Confirm they know their polling booth and plan to vote."))
        return {"title": "Talking points at the door", "items": items}
    return {"title": "Micro-manifesto", "items": [it(f"{i + 1}. {c['commitment']} ({c['delivery']})", c["sources"])
                                                  for i, c in enumerate(commitments[:6])]}


# ---------------------------------------------------------------------------
# micro-manifesto
# ---------------------------------------------------------------------------
MANIFESTO_SCHEMA = """{"title": "...", "preamble": "2-3 sentences with [S#]",
 "sections": [{"topic": "Agriculture|Infrastructure|Youth|Welfare", "local_context": "1-2 sentences with [S#]",
   "commitments": [{"text": "...", "type": "party|local", "delivery": "how it will be delivered or funded", "sources": ["S#"]}]}],
 "closing": "1-2 sentences"}"""


def manifesto(conn, place: dict, language: str, user_id: str, party_name: str = "", topics: list[str] | None = None,
              embedder=None, offline: bool = False) -> dict:
    language = language if language in LANGUAGES else "en"
    topics = [t for t in (topics or kb.TOPICS) if t in kb.TOPICS] or list(kb.TOPICS)
    queries = []
    for t in topics:
        queries.append((f"{kb.TOPIC_LEXICON[t][:160]} {place['name']}", {"topic": t, "doc_types": ["survey", "news", "legislative", "budget", "other"], "k": 2}))
        queries.append((f"we will {kb.TOPIC_LEXICON[t][:120]}", {"topic": t, "doc_types": ["party_promise"], "k": 1}))
    sources = gather_sources(conn, place, queries, embedder, limit=16)
    if not sources:
        raise ValueError("No knowledge base material for this area yet. Upload documents first.")
    meta = {"constituency": {"id": place.get("id"), "name": _place_label(place), "state": place["state"]},
            "language": language, "party_name": party_name or None, "topics": topics,
            "booth_ids": place.get("booth_ids") or None}
    provider = None if offline else get_provider()
    result, note = None, None
    if provider:
        if not within_limit(user_id):
            note = "Hourly AI limit reached; built from the sources directly."
        else:
            try:
                system = ("You draft one-page hyper-local election manifestos for an Indian campaign.\n"
                          + RULES.format(language=LANGUAGES[language]) + "\n\nSchema:\n" + MANIFESTO_SCHEMA
                          + "\n\nOne section per requested topic that the sources support (max 4), 2-3 commitments each. "
                            "Blend party promises (type 'party', cite the party source) with local commitments that answer the "
                            "local evidence (type 'local'). Keep the whole document to about 350 words.")
                user = (f"Area: {_place_label(place)}, {place['state']}\nParty: {party_name or 'our party'}\n"
                        f"Topics: {', '.join(topics)}\n\nSOURCES\n{sources_block(sources)}")
                result = _verify_manifesto(_parse_json(provider.complete(system, user, MAX_TOKENS)), sources)
                if not result["sections"]:
                    raise LLMError("no verified sections")
                result.update(generator=provider.name, live=True, generated_language=language)
            except LLMError as e:
                note = f"AI draft unavailable ({str(e)[:100]}); built from the sources directly."
                result = None
    if result is None:
        result = _offline_manifesto(sources, place, topics, party_name)
        result.update(generator="offline", live=False, generated_language="en")
        if language != "en":
            result["warnings"].insert(0, f"{LANGUAGES[language]} drafts need an AI provider; this draft is in English.")
    if note:
        result["warnings"].insert(0, note)
    payload = {**meta, **result, "generated_at": now_iso()}
    payload["output_id"] = save(conn, "manifesto", place.get("id"), language, meta, payload, user_id)
    return payload


def _verify_manifesto(data: dict, sources: list[dict]) -> dict:
    v = Verifier(sources)
    pre = v.text(data.get("preamble"), "preamble") or ("", [])
    sections = []
    for sec in (data.get("sections") or [])[:4]:
        if not isinstance(sec, dict) or sec.get("topic") not in kb.TOPICS:
            continue
        ctx = v.text(sec.get("local_context"), "context", need_source=True)
        coms = []
        for c in (sec.get("commitments") or [])[:3]:
            if not isinstance(c, dict):
                continue
            t = v.text(c.get("text"), "commitment", extra_sources=c.get("sources") or [])
            d = v.text(c.get("delivery"), "delivery note")
            if t:
                coms.append({"text": t[0], "type": "party" if c.get("type") == "party" else "local",
                             "delivery": d[0] if d else "", "sources": t[1]})
        if ctx and coms:
            sections.append({"topic": sec["topic"], "local_context": ctx[0], "context_sources": ctx[1], "commitments": coms})
    close = v.text(data.get("closing"), "closing") or ("", [])
    return {"warnings": v.warnings, "title": str(data.get("title") or "Local manifesto")[:120],
            "preamble": {"text": pre[0], "sources": pre[1]}, "sections": sections,
            "closing": close[0], "sources": source_list(sources, v.used)}


def _offline_manifesto(sources, place, topics, party_name) -> dict:
    party = party_name or "our party"
    used, sections = set(), []
    LOCAL = {"Agriculture": "Monthly farmers' review in {c}, with published action taken on every complaint",
             "Infrastructure": "Ward-by-ward works calendar for {c}, inspected with residents each month",
             "Youth": "A {c} jobs and skills desk linked to the district employment office",
             "Welfare": "Doorstep help camps in every ward of {c} to fix stalled scheme benefits"}
    used_promises: set[str] = set()
    for t in topics:
        want = set(kb.tokens(kb.TOPIC_LEXICON[t]))
        local = [(len(want & set(kb.tokens(x))) + (0.5 if _figure(x) else 0) + (1 if (issue_of(x) or ("", ""))[1] == t else 0), x, s)
                 for s in sources if s["doc_type"] != "party_promise" for x in _sentences(s["text"])]
        local = [c for c in local if c[0] >= 1.5]
        if not local:
            continue
        _, sent, ctx_src = max(local, key=lambda c: c[0])
        coms = []
        promise_sents = [(len(want & set(kb.tokens(x))), x, p) for p in sources if p["doc_type"] == "party_promise"
                         for x in _sentences(p["text"]) if x.lower().startswith("we will") and x not in used_promises]
        promise_sents = [c for c in promise_sents if c[0] >= 1]
        if promise_sents:
            _, best, p = max(promise_sents, key=lambda c: c[0])
            used_promises.add(best)
            coms.append({"text": best, "type": "party", "delivery": f"From {party}'s published commitments.", "sources": [p["sid"]]})
            used.add(p["sid"])
        coms.append({"text": LOCAL[t].format(c=place["name"]), "type": "local",
                     "delivery": "Run by the MLA's office; progress published monthly.", "sources": [ctx_src["sid"]]})
        used.add(ctx_src["sid"])
        sections.append({"topic": t, "local_context": f"{sent} [{ctx_src['sid']}]", "context_sources": [ctx_src["sid"]],
                         "commitments": coms})
        if len(sections) == 4:
            break
    p = party[0].upper() + party[1:]
    return {"warnings": ["Built directly from the sources (no AI provider configured). Edit the wording before public use."],
            "title": f"{place['name']}: our local commitments",
            "preamble": {"text": f"This plan answers what residents of {_place_label(place)} told us and what the local record shows. "
                                 f"{p}'s commitments are matched to each local problem below.", "sources": []},
            "sections": sections, "closing": "Every commitment will be reviewed publicly each month.",
            "sources": source_list(sources, used)}


# ---------------------------------------------------------------------------
# grounded answers for the knowledge-base search box
# ---------------------------------------------------------------------------

def answer(conn, query: str, place: dict, results: list[dict], user_id: str, language: str = "en") -> dict:
    for i, r in enumerate(results, 1):
        r["sid"] = f"S{i}"
    if not results:
        return {"text": "Nothing in the knowledge base matches this question.", "sources": [], "generator": "offline", "warnings": []}
    provider = get_provider()
    if provider and within_limit(user_id):
        try:
            system = ("Answer questions about a constituency using only the SOURCES. Cite every claim and figure as [S#]. "
                      "If the sources don't answer it, say so. 2-5 sentences. Reply as JSON: {\"answer\": \"...\"}. "
                      f"Write in {LANGUAGES.get(language, 'English')}.")
            data = _parse_json(provider.complete(system, f"Question: {query}\n\nSOURCES\n{sources_block(results)}", 800))
            v = Verifier(results)
            kept = [x for x in (v.text(s, "sentence") for s in _split(str(data.get("answer") or ""))) if x]
            if kept:
                return {"text": " ".join(k[0] for k in kept), "sources": sorted(v.used), "generator": provider.name,
                        "warnings": v.warnings}
        except LLMError:
            pass
    q = set(kb.tokens(query))
    picks = []
    for r in results[:4]:
        sents = sorted(_sentences(r["text"]), key=lambda s: len(q & set(kb.tokens(s))), reverse=True)
        if sents and q & set(kb.tokens(sents[0])):
            picks.append(f"{sents[0]} [{r['sid']}]")
        if len(picks) == 3:
            break
    return {"text": " ".join(picks) or "The closest passages are listed below.", "sources": [r["sid"] for r in results[:len(picks)]],
            "generator": "offline", "warnings": []}


def translate_query(query: str) -> str | None:
    """Cross-language retrieval: turn a Hindi/Punjabi question into English keywords (needs an LLM)."""
    if query.isascii():
        return None
    provider = get_provider()
    if not provider:
        return None
    try:
        out = provider.complete("Translate the user's question into short English search keywords. Reply with the keywords only.",
                                query, 60)
        return re.sub(r"[^\w\s-]", " ", out)[:200] if out else None
    except LLMError:
        return None


# ---------------------------------------------------------------------------
# saved outputs
# ---------------------------------------------------------------------------

def save(conn, kind, constituency_id, language, params, payload, user_id) -> str:
    oid = f"out-{uuid.uuid4().hex[:10]}"
    payload["output_id"] = oid
    conn.execute("""INSERT INTO copilot_outputs (output_id, kind, constituency_id, language, params, payload, created_by, created_at)
                    VALUES (?,?,?,?,?,?,?,?)""", (oid, kind, constituency_id, language, json.dumps(params, ensure_ascii=False),
                                                  json.dumps(payload, ensure_ascii=False), user_id, now_iso()))
    conn.commit()
    return oid
