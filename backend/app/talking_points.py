"""AI-recommended talking points.

`RuleBasedProvider` is a deterministic rules engine keyed on state and
household features. To use an LLM instead, implement `TalkingPointProvider`
(same `for_household` signature) and set `PROVIDER` — the API and UI do not
change. Points are issue- and listening-led by design: they ask about
delivery of schemes and local problems rather than asserting claims.
"""
from __future__ import annotations

from typing import Protocol

SCHEME_POINTS = {
    "PM-KISAN": "Ask whether PM-KISAN instalments are arriving on time; log any account or e-KYC issues for follow-up.",
    "PMAY": "Ask how the PMAY house is working out; note pending instalments or construction issues.",
    "PMAY-G": "Ask about the PMAY-Gramin house status; note any pending instalment or geo-tagging issue.",
    "Ujjwala": "Check whether the Ujjwala LPG connection is in use and refills are affordable.",
    "Ayushman Bharat": "Confirm family members have Ayushman cards; offer help with enrolment for those missing.",
    "Ration (PMGKAY)": "Ask if free ration is received in full each month at the local fair-price shop.",
    "Ration (Atta-Dal)": "Ask about ration card status and whether wheat is received regularly.",
    "Ration": "Ask about ration card status and fair-price shop service.",
    "Old-Age Pension": "Check whether the elder's pension is credited regularly.",
    "Griha Aadhar": "Ask whether the Griha Aadhar monthly support is being credited.",
    "DSSS Pension": "Check that the DSSS pension is reaching the family elder without delay.",
    "Ladli Laxmi": "Ask whether Ladli Laxmi support was received; help with paperwork if pending.",
}

STATE_LOCAL = {
    "UP": {
        "Sugarcane": "Discuss sugarcane payment timeliness from the local mill and this season's cane price.",
        "Farmer": "Discuss irrigation, fertiliser availability and stray cattle damage to crops.",
        "_default": "Ask about local roads, drainage and law-and-order in the mohalla.",
    },
    "PB": {
        "Wheat-Paddy": "Discuss MSP procurement at the mandi, payment delays and stubble-management support.",
        "Sugarcane": "Discuss local sugarcane prices (SAP) and pending mill dues.",
        "Cotton": "Discuss pest damage and cotton procurement prices this season.",
        "Farmer": "Discuss input costs, groundwater depletion and crop diversification.",
        "_default": "Ask about drug-abuse concerns, local jobs and village infrastructure.",
    },
    "UK": {
        "High-Hill": "Discuss road connectivity, health-centre access and out-migration from the village.",
        "Mid-Hill": "Discuss road maintenance, school access and wild-animal crop damage.",
        "Valley": "Discuss jobs for youth, tourism income and market access.",
        "Ex-Servicemen": "Thank them for service; ask about ECHS / pension or canteen issues.",
        "_default": "Ask about water supply and mobile connectivity.",
    },
    "GA": {
        "Fishing": "Discuss fishing-ban period support and jetty facilities.",
        "Mining-affected": "Discuss livelihood support for mining-dependent families.",
        "Tourism": "Discuss tourism-season income and local infrastructure.",
        "Gulf / Shipping": "Ask how family members working abroad can vote or stay engaged.",
        "_default": "Discuss ward-level issues: water supply, garbage collection and road repair.",
    },
}


class TalkingPointProvider(Protocol):
    def for_household(self, state: str, members: list[dict]) -> list[dict]: ...


class RuleBasedProvider:
    def for_household(self, state: str, members: list[dict]) -> list[dict]:
        pts: list[dict] = []
        seen: set[str] = set()

        def add(topic, text, why):
            if text not in seen:
                seen.add(text); pts.append({"topic": topic, "point": text, "reason": why})

        quads = {m.get("quadrant") for m in members}
        attrs = members[0].get("attributes", {}) if members else {}

        if "GOTV" in quads:
            add("Turnout", "Confirm polling booth, voting time and whether anyone needs transport or help on polling day.",
                "Household has supporters with low turnout probability")
        if "PERSUADE" in quads:
            add("Listen first", "Open by asking the family's top local concern before discussing the party's work.",
                "Household has persuadable, likely voters")

        # scheme-driven points, most common scheme first
        counts: dict[str, int] = {}
        for m in members:
            for s in (m.get("scheme_types") or "").split(","):
                if s.strip():
                    counts[s.strip()] = counts.get(s.strip(), 0) + 1
        for s, _ in sorted(counts.items(), key=lambda x: -x[1])[:2]:
            if s in SCHEME_POINTS:
                add("Welfare delivery", SCHEME_POINTS[s], f"Beneficiary of {s}")

        local = STATE_LOCAL.get(state, {})
        for key in (attrs.get("crop"), attrs.get("terrain"), attrs.get("occupation")):
            if key and key in local:
                add("Local issue", local[key], f"{key} household"); break
        else:
            if "_default" in local:
                add("Local issue", local["_default"], "General local concern")

        if state == "PB" and attrs.get("dera_follower"):
            add("Community", "Be respectful of the family's dera affiliation; ask about community events rather than debating.",
                "Dera-affiliated household")
        if state == "GA" and len(members) >= 4:
            add("Family", "Speak with the family elder present; in Goa's close-knit wards the head's view carries weight.",
                "Large household")
        if any(m.get("is_migrant") for m in members):
            add("Absentee", "Ask whether absent members (migrant / NRI) can be home on polling day; update their status.",
                "Household has absentee voters")
        if any(m["age"] <= 25 for m in members):
            add("Youth", "Ask young voters about jobs, skilling and exams; check first-time voters are enrolled.",
                "First-time / young voters")
        if any(m["age"] >= 70 for m in members):
            add("Seniors", "Check whether elderly voters want home-voting (85+) or help reaching the booth.",
                "Elderly voters in household")
        return pts[:5]


PROVIDER: TalkingPointProvider = RuleBasedProvider()


# ===========================================================================
# v3: retrieval-augmented talking points with an LLM provider + offline fallback
# ===========================================================================
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import defaultdict  # noqa: E402

from .config import STATES  # noqa: E402
from .db import now_iso  # noqa: E402
from .knowledge import RETRIEVER, directives_for, kb_version, list_docs  # noqa: E402
from .llm import LLMError, get_provider  # noqa: E402

LANGUAGES = {"en": "English", "hi": "Hindi", "pa": "Punjabi", "kok": "Konkani"}
GOAL = {"PERSUADE": "persuade (open to persuasion, likely to vote)", "GOTV": "help them vote (supporter, may not vote)",
        "LOW": "listen (low priority)", "OPPOSITION": "be courteous, do not press"}
MAX_POINTS, MAX_POINT_LEN, MAX_REASON_LEN = 5, 240, 120
LIVE_LIMIT_PER_HOUR = 120

# Model output is screened for appeals on caste/religion (barred by the Model
# Code of Conduct and s.123(3) of the RP Act), for inducements, and for
# anything that looks like personal data.
BLOCKED = re.compile(
    r"\b(caste|castes|jati|biradari|religion|religious|communal|hindus?|muslims?|sikhs?|christians?|catholics?|dalits?|"
    r"brahmins?|thakurs?|rajputs?|yadavs?|jatavs?|jats?|bhandaris?|saraswats?|obc|deras?|mandir|masjid|gurdwara|"
    r"church|mosque|cash|gifts?|liquor|bribes?|freebies?)\b", re.IGNORECASE)
PII = re.compile(r"\d{7,}|[A-Z]{3}\d{7}")

SYSTEM_PROMPT = """You write door-to-door talking points for a campaign volunteer in India.

Rules:
1. Write 3 to 5 short points the volunteer can say or ask at this household. Lead with listening: questions, checks on whether services reach the family, offers of practical help.
2. Use only facts found in DOCUMENTS and DIRECTIVES. Do not add numbers, dates, prices or promises that are not given there. If a directive mentions an announcement, refer to it only as the directive describes it.
3. Put today's directives first when they fit this household.
4. Never mention or appeal to caste, religion or community. Never offer money, gifts or favours. Never criticise individuals. Never give voting-procedure information that is not in DOCUMENTS.
5. Members are anonymous (A, B, ...). Do not invent names.
6. Write in the requested language. Keep each point under 220 characters.

Reply with JSON only:
{"points": [{"topic": "2-3 words", "point": "what to say or ask", "reason": "why, under 100 characters", "sources": ["document or directive ids used"]}]}"""


def _age_band(a: int) -> str:
    for hi, label in ((25, "18-25"), (35, "26-35"), (50, "36-50"), (65, "51-65"), (80, "66-80")):
        if a <= hi:
            return label
    return "80+"


def household_profile(members: list[dict]) -> tuple[dict, set[str]]:
    """Anonymised household profile for the prompt, plus retrieval tags.
    Deliberately excludes names, phones, EPIC numbers, community and dera affiliation."""
    attrs = (members[0].get("attributes") or {}) if members else {}
    tags: set[str] = set()
    people = []
    for i, m in enumerate(members):
        schemes = [s for s in (m.get("scheme_types") or "").split(",") if s]
        tags.update(schemes)
        if m["age"] <= 25: tags.update({"youth", "first-time"})
        if m["age"] >= 70: tags.add("senior")
        if m["age"] >= 85: tags.add("home voting")
        if m.get("is_migrant"): tags.update({"migrant", "absentee"})
        if m.get("quadrant") == "GOTV": tags.add("turnout")
        people.append({"ref": chr(65 + i), "age_band": _age_band(m["age"]), "gender": m.get("gender"),
                       "goal": GOAL.get(m.get("quadrant"), "listen"), "schemes": schemes,
                       "away_for_work": bool(m.get("is_migrant"))})
    hh = {"size": len(members)}
    for k in ("occupation", "crop", "terrain"):
        if attrs.get(k):
            hh[k] = attrs[k]; tags.add(str(attrs[k]))
    return {"household": hh, "members": people}, tags


def _offline(members, state, directives, retrieved) -> list[dict]:
    """Deterministic generator used when no LLM is configured or a call fails."""
    pts: list[dict] = []
    seen: set[str] = set()

    def add(p):
        if p["point"] not in seen and len(pts) < MAX_POINTS:
            seen.add(p["point"]); pts.append(p)

    for d in directives[:2]:
        add({"topic": "Today’s focus", "point": d["text"],
             "reason": f"Set for today by {d.get('author') or 'the campaign'}", "sources": [f"directive-{d['directive_id']}"]})
    rules = PROVIDER.for_household(state, members)
    for r in rules:              # turnout / listening openers come first in the rule engine
        if r["topic"] in ("Turnout", "Listen first"):
            add({**r, "sources": []})
    for doc in retrieved:
        if doc.get("ask") and doc["kind"] != "context":
            add({"topic": doc["title"][:40], "point": doc["ask"], "reason": f"From the brief: {doc['title']}",
                 "sources": [doc["doc_id"]]})
    for r in rules:
        add({**r, "sources": []})
    return pts


def _parse(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise LLMError("no JSON in model reply")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise LLMError("model reply was not valid JSON") from None
    pts = data.get("points") if isinstance(data, dict) else None
    if not isinstance(pts, list):
        raise LLMError("model reply had no points list")
    return pts


def guard(points: list, allowed_sources: set[str]) -> tuple[list[dict], int]:
    """Validate shape, trim, drop unsafe points and unknown citations. Returns (points, dropped)."""
    out, dropped = [], 0
    for p in points:
        if not isinstance(p, dict) or not str(p.get("point") or "").strip():
            dropped += 1; continue
        text = re.sub(r"\s+", " ", str(p["point"])).strip()[:MAX_POINT_LEN]
        topic = re.sub(r"\s+", " ", str(p.get("topic") or "Talking point")).strip()[:40]
        reason = re.sub(r"\s+", " ", str(p.get("reason") or "")).strip()[:MAX_REASON_LEN]
        if BLOCKED.search(f"{topic} {text} {reason}") or PII.search(text):
            dropped += 1; continue
        srcs = [s for s in (p.get("sources") or []) if isinstance(s, str) and s in allowed_sources][:4]
        out.append({"topic": topic, "point": text, "reason": reason, "sources": srcs})
        if len(out) == MAX_POINTS:
            break
    return out, dropped


_calls: dict[str, list[float]] = defaultdict(list)
_calls_lock = threading.Lock()


def _within_limit(user_id: str) -> bool:
    with _calls_lock:
        now = time.time()
        _calls[user_id] = [t for t in _calls[user_id] if now - t < 3600]
        if len(_calls[user_id]) >= LIVE_LIMIT_PER_HOUR:
            return False
        _calls[user_id].append(now)
        return True


def household_members(conn, household_id: str) -> list[dict]:
    """Scored (i.e. not opted-out) members of a household."""
    rows = []
    for r in conn.execute("""SELECT v.voter_id, v.name, v.age, v.gender, v.community_group, v.scheme_beneficiary,
            v.scheme_types, v.is_migrant, v.attributes, v.state, v.consent_status, s.quadrant, s.swing_score,
            s.turnout_score, s.priority FROM voters v JOIN voter_scores s USING(voter_id)
            WHERE v.household_id = ? ORDER BY s.priority DESC""", (household_id,)):
        d = dict(r)
        d["attributes"] = json.loads(d["attributes"] or "{}")
        rows.append(d)
    return rows


def place_of(conn, household_id: str) -> tuple[str, str]:
    r = conn.execute("""SELECT b.state, b.constituency FROM households h JOIN booths b ON b.booth_id = h.booth_id
                        WHERE h.household_id = ?""", (household_id,)).fetchone()
    if not r:
        raise KeyError(f"household {household_id} not found")
    return r["state"], r["constituency"]


def build_prompt(state, constituency, language, profile, directives, retrieved, context_doc) -> str:
    payload = {
        "state": STATES.get(state, {}).get("name", state), "constituency": constituency,
        "language": LANGUAGES.get(language, "English"),
        "state_context": context_doc["body"] if context_doc else None,
        "directives": [{"id": f"directive-{d['directive_id']}", "text": d["text"]} for d in directives],
        "documents": [{"id": d["doc_id"], "title": d["title"], "facts": d["body"]} for d in retrieved],
        **profile,
    }
    return "Write talking points for this household.\n\n" + json.dumps(payload, ensure_ascii=False, indent=1)


def generate(conn, household_id: str, date: str, language: str = "en", user_id: str = "system",
             refresh: bool = False, allow_live: bool = True, docs: list | None = None,
             directives: list | None = None) -> dict:
    language = language if language in LANGUAGES else "en"
    state, constituency = place_of(conn, household_id)
    members = household_members(conn, household_id)
    directives = directives_for(conn, date, state, constituency) if directives is None else directives
    docs = list_docs(conn, state, constituency) if docs is None else docs
    profile, tags = household_profile(members)
    query = " ".join(sorted(tags)) + " " + " ".join(d["text"] for d in directives)
    retrieved = RETRIEVER.search(query, tags, [d for d in docs if d["kind"] != "context"], k=4)
    context_doc = next((d for d in docs if d["kind"] == "context" and d.get("state") == state), None)
    sources_meta = {f"directive-{d['directive_id']}": {"id": f"directive-{d['directive_id']}", "title": d["text"][:80],
                                                       "kind": "directive"} for d in directives}   # directives listed first
    sources_meta.update({d["doc_id"]: {"id": d["doc_id"], "title": d["title"], "kind": d["kind"]} for d in retrieved})

    provider = get_provider() if allow_live and members else None
    sig = json.dumps([household_id, date, language, provider.name if provider else "offline", kb_version(conn),
                      [(d["directive_id"], d["text"]) for d in directives],
                      [(m["voter_id"], m["quadrant"]) for m in members]], sort_keys=True)
    key = hashlib.sha1(sig.encode()).hexdigest()
    if provider and not refresh:
        hit = conn.execute("SELECT payload FROM talking_point_cache WHERE cache_key = ?", (key,)).fetchone()
        if hit:
            return {**json.loads(hit["payload"]), "cached": True}

    points, generator, fallback = None, "offline", None
    if provider:
        if not _within_limit(user_id):
            fallback = "hourly AI limit reached; showing standard points"
        else:
            try:
                raw = provider.complete(SYSTEM_PROMPT, build_prompt(state, constituency, language, profile, directives,
                                                                    retrieved, context_doc))
                pts, dropped = guard(_parse(raw), set(sources_meta))
                if len(pts) < 2:
                    raise LLMError(f"only {len(pts)} usable points after safety checks")
                points, generator = pts, provider.name
                if dropped:
                    fallback = f"{dropped} suggestion(s) left out by the content rules"
            except LLMError as e:
                fallback = f"AI unavailable ({str(e)[:120]}); showing standard points"
    if points is None:
        points = _offline(members, state, directives, retrieved)
    used = {s for p in points for s in p.get("sources", [])}
    payload = {
        "household_id": household_id, "date": date, "language": language if generator != "offline" else "en",
        "points": points, "generator": generator, "live": generator != "offline", "note": fallback,
        "directives": [{"id": d["directive_id"], "text": d["text"], "author": d.get("author")} for d in directives],
        "sources": [sources_meta[s] for s in sources_meta if s in used],
        "generated_at": now_iso(), "cached": False,
    }
    if generator != "offline":
        conn.execute("INSERT OR REPLACE INTO talking_point_cache (cache_key, household_id, for_date, payload, generator, created_at) "
                     "VALUES (?,?,?,?,?,?)", (key, household_id, date, json.dumps(payload), generator, payload["generated_at"]))
        conn.commit()
    return payload
