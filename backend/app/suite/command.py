"""Command Center: one read model across VoterEngine, JanVani Copilot and CounterPulse.

Every function takes an `Area` (states plus, for a constituency manager, one
constituency) and returns aggregates only: no voter names, phones or EPIC numbers
ever leave this module, so the executive view and the briefing PDF are safe to
project in a meeting room.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..auth import today_local
from ..config import QUADRANTS, STATES, TIMEZONE
from ..copilot import kb
from ..copilot.generate import AUDIENCES, issue_of
from ..generator import constituency_code
from ..routing import generate_route
from ..warroom.engine import SEVERITY, SOURCE_TYPES

TOPIC_AUDIENCE = {"Agriculture": "farmers", "Youth": "youth", "Welfare": "women", "Infrastructure": "scheme_beneficiaries"}
_NON_LATIN = re.compile(r"[ऀ-ൿ]")


@dataclass
class Area:
    states: list[str]
    constituency: str | None = None             # managers: their constituency only
    label: str = ""
    cid: str | None = field(default=None, init=False)

    def __post_init__(self):
        if self.constituency:
            self.cid = f"{self.states[0]}-{constituency_code(self.constituency)}"
        if not self.label:
            self.label = (self.constituency + ", " + STATES[self.states[0]]["name"] if self.constituency
                          else STATES[self.states[0]]["name"] if len(self.states) == 1 else "All states")

    def booths(self, a="b") -> tuple[str, list]:
        sql = f"{a}.state IN ({','.join('?' * len(self.states))})"
        params: list = list(self.states)
        if self.constituency:
            sql += f" AND lower({a}.constituency) = lower(?)"; params.append(self.constituency)
        return sql, params

    def outputs(self, a="o") -> tuple[str, list]:
        if self.cid:
            return f"{a}.constituency_id = ?", [self.cid]
        return "(" + " OR ".join(f"{a}.constituency_id LIKE ?" for _ in self.states) + ")", [f"{s}-%" for s in self.states]

    def clusters(self, a="n") -> tuple[str, list]:
        if self.cid:
            return f"{a}.cluster_id IN (SELECT cluster_id FROM media_items WHERE constituency_id = ?)", [self.cid]
        return f"{a}.state IN ({','.join('?' * len(self.states))})", list(self.states)

    def media(self, a="m") -> tuple[str, list]:
        if self.cid:
            return f"{a}.constituency_id = ?", [self.cid]
        return f"{a}.state IN ({','.join('?' * len(self.states))})", list(self.states)


def _day_start_utc(d: str) -> str:
    local = datetime.combine(datetime.fromisoformat(d).date(), dtime.min, tzinfo=ZoneInfo(TIMEZONE))
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def latin(text: str | None) -> bool:
    return bool(text) and not _NON_LATIN.search(text)


# ---------------------------------------------------------------------------
# VoterEngine
# ---------------------------------------------------------------------------

def kpis(c, area: Area) -> dict:
    bw, bp = area.booths()
    d = today_local()
    booths = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(total_voters),0) v FROM booths b WHERE {bw}", bp).fetchone()
    covered = c.execute(f"""SELECT COUNT(DISTINCT h.booth_id) FROM route_stops rs JOIN routes r USING(route_id)
        JOIN households h USING(household_id) JOIN booths b ON b.booth_id = h.booth_id
        WHERE r.route_date = ? AND {bw}""", [d, *bp]).fetchone()[0]
    q = c.execute(f"""SELECT COUNT(*) n, COALESCE(SUM(s.quadrant='PERSUADE'),0) persuade, COALESCE(SUM(s.quadrant='GOTV'),0) gotv
        FROM voters v JOIN voter_scores s USING(voter_id) JOIN booths b ON b.booth_id = v.booth_id
        WHERE {bw} AND v.consent_status != 'OPT_OUT'""", bp).fetchone()
    ow, op = area.outputs()
    outs = {r["kind"]: r["n"] for r in c.execute(f"SELECT kind, COUNT(*) n FROM copilot_outputs o WHERE {ow} GROUP BY kind", op)}
    cw, cp = area.clusters()
    sev = {r["severity"]: r["n"] for r in c.execute(
        f"SELECT severity, COUNT(*) n FROM narrative_clusters n WHERE n.status = 'active' AND {cw} GROUP BY severity", cp)}
    return {
        "active_booths": booths["n"], "booths_covered_today": covered, "electors": booths["v"],
        "scored_voters": q["n"], "persuade": q["persuade"], "gotv": q["gotv"],
        "manifestos": outs.get("manifesto", 0), "briefs": outs.get("brief", 0),
        "critical_threats": sev.get("Critical", 0), "high_threats": sev.get("High", 0),
        "active_narratives": sum(sev.values()),
    }


def by_state(c) -> list[dict]:
    return [{"state": s, "name": STATES[s]["name"], **kpis(c, Area([s]))} for s in STATES]


def quadrants(c, area: Area) -> list[dict]:
    bw, bp = area.booths()
    counts = {r["quadrant"]: r["n"] for r in c.execute(f"""SELECT s.quadrant, COUNT(*) n FROM voters v
        JOIN voter_scores s USING(voter_id) JOIN booths b ON b.booth_id = v.booth_id
        WHERE {bw} AND v.consent_status != 'OPT_OUT' GROUP BY s.quadrant""", bp)}
    total = sum(counts.values()) or 1
    return [{"quadrant": k, "label": QUADRANTS[k]["label"], "action": QUADRANTS[k]["action"],
             "voters": counts.get(k, 0), "share": round(100 * counts.get(k, 0) / total, 1)}
            for k in ("PERSUADE", "GOTV", "LOW", "OPPOSITION")]


def top_booths(c, area: Area, n: int = 5) -> list[dict]:
    bw, bp = area.booths()
    rows = c.execute(f"""SELECT b.booth_id, b.name, b.state, b.constituency, b.total_voters, b.historical_turnout,
        b.last_margin_pct, b.priority_score,
        (SELECT COUNT(*) FROM voters v JOIN voter_scores s USING(voter_id) WHERE v.booth_id = b.booth_id AND s.quadrant='PERSUADE'
           AND v.consent_status != 'OPT_OUT') persuade,
        (SELECT COUNT(*) FROM voters v JOIN voter_scores s USING(voter_id) WHERE v.booth_id = b.booth_id AND s.quadrant='GOTV'
           AND v.consent_status != 'OPT_OUT') gotv
        FROM booths b WHERE {bw} ORDER BY b.priority_score DESC LIMIT ?""", [*bp, n])
    out = []
    for r in rows:
        d = dict(r)
        d["focus"] = "Turnout drive" if d["gotv"] > d["persuade"] else "Persuasion visits"
        out.append(d)
    return out


def segments(c, area: Area) -> list[dict]:
    """Planning segments without community or caste: age, gender, scheme use, migration."""
    bw, bp = area.booths()
    base = f"""FROM voters v JOIN voter_scores s USING(voter_id) JOIN booths b ON b.booth_id = v.booth_id
               WHERE {bw} AND v.consent_status != 'OPT_OUT'"""
    defs = [("First-time and young voters (18–25)", "v.age BETWEEN 18 AND 25"), ("Voters aged 26–40", "v.age BETWEEN 26 AND 40"),
            ("Voters aged 41–60", "v.age BETWEEN 41 AND 60"), ("Voters over 60", "v.age > 60"),
            ("Women voters", "v.gender = 'F'"), ("Scheme beneficiaries", "v.scheme_beneficiary = 1"),
            ("Migrant and absentee voters", "v.is_migrant = 1")]
    out = []
    for label, cond in defs:
        r = c.execute(f"""SELECT COUNT(*) n, COALESCE(SUM(s.quadrant='PERSUADE'),0) p, COALESCE(SUM(s.quadrant='GOTV'),0) g,
            ROUND(AVG(s.turnout_score),1) t, ROUND(AVG(s.swing_score),1) w {base} AND {cond}""", bp).fetchone()
        if r["n"]:
            out.append({"segment": label, "voters": r["n"], "persuade": r["p"], "gotv": r["g"],
                        "persuade_share": round(100 * r["p"] / r["n"], 1), "avg_turnout": r["t"], "avg_swing": r["w"]})
    return out


def _area_cadres(c, area: Area) -> list[str]:
    bw, bp = area.booths()
    booths = {r[0] for r in c.execute(f"SELECT booth_id FROM booths b WHERE {bw}", bp)}
    return [r["cadre_id"] for r in c.execute("SELECT cadre_id, booth_ids FROM cadres ORDER BY cadre_id")
            if booths & set((r["booth_ids"] or "").split(","))]


def field_today(c, area: Area) -> dict:
    bw, bp = area.booths()
    d = today_local()
    st = c.execute(f"""SELECT COUNT(*) planned, COALESCE(SUM(rs.status != 'PENDING'),0) done,
        COALESCE(SUM(rs.status = 'VISITED'),0) visited FROM route_stops rs JOIN routes r USING(route_id)
        JOIN households h USING(household_id) JOIN booths b ON b.booth_id = h.booth_id
        WHERE r.route_date = ? AND {bw}""", [d, *bp]).fetchone()
    cadres = _area_cadres(c, area)
    routed = {r[0] for r in c.execute("SELECT cadre_id FROM routes WHERE route_date = ?", (d,))}
    consent = c.execute(f"""SELECT COALESCE(SUM(cl.new_status = 'GRANTED'),0) granted, COUNT(*) n FROM consent_log cl
        JOIN voters v ON v.voter_id = cl.voter_id JOIN booths b ON b.booth_id = v.booth_id
        WHERE cl.source = 'field' AND cl.created_at >= ? AND {bw}""", [_day_start_utc(d), *bp]).fetchone()
    return {"date": d, "planned": st["planned"], "done": st["done"], "visited": st["visited"],
            "progress": round(100 * st["done"] / st["planned"]) if st["planned"] else 0,
            "cadres": len(cadres), "cadres_dispatched": sum(1 for x in cadres if x in routed),
            "consents_today": consent["n"], "consents_granted_today": consent["granted"]}


def dispatch(c, area: Area, max_stops: int = 20) -> dict:
    """Build today's route for every cadre in the area who doesn't have one yet."""
    d = today_local()
    routed = {r[0] for r in c.execute("SELECT cadre_id FROM routes WHERE route_date = ?", (d,))}
    todo = [x for x in _area_cadres(c, area) if x not in routed]
    made, stops, empty = [], 0, 0
    for cid in todo:
        r = generate_route(c, cid, d, max_stops)
        if r.get("route_id"):
            made.append(cid); stops += len(r.get("stops") or [])
        else:
            empty += 1
    c.commit()
    return {"date": d, "dispatched": len(made), "already_dispatched": len(routed & set(_area_cadres(c, area))),
            "no_priority_doors": empty, "stops": stops, "cadres": made}


# ---------------------------------------------------------------------------
# JanVani Copilot
# ---------------------------------------------------------------------------

def grievances(c, area: Area, per_place: int = 3, places: int = 4) -> list[dict]:
    ow, op = area.outputs()
    seen, out = set(), []
    for r in c.execute(f"""SELECT constituency_id, payload, created_at FROM copilot_outputs o
                           WHERE kind = 'brief' AND {ow} ORDER BY created_at DESC, rowid DESC""", op):
        if r["constituency_id"] in seen or len(seen) >= places:
            continue
        p = json.loads(r["payload"])
        if p.get("generated_language", "en") != "en":
            continue
        seen.add(r["constituency_id"])
        cites = {s["id"]: s["citation"] for s in p.get("sources", [])}
        for g in p.get("grievances", [])[:per_place]:
            st = (g.get("stats") or [{}])[0]
            out.append({"constituency": p["constituency"]["name"], "constituency_id": r["constituency_id"],
                        "title": g["title"], "figure": st.get("figure") or "", "context": st.get("context") or "",
                        "source": cites.get(st.get("source") or (g.get("sources") or [None])[0], ""),
                        "brief_date": r["created_at"][:10]})
    order = list(STATES)
    return sorted(out, key=lambda g: order.index(g["constituency_id"].split("-")[0]) if g["constituency_id"] else 9)


def promises(c, area: Area, n: int = 5) -> list[dict]:
    rows = c.execute(f"""SELECT d.state, d.source_citation, ch.text FROM copilot_documents d JOIN copilot_chunks ch USING(doc_id)
        WHERE d.doc_type = 'party_promise' AND d.active = 1 AND (d.state IN ({','.join('?' * len(area.states))}) OR d.state IS NULL)
        ORDER BY d.state IS NULL, d.state, ch.seq""", area.states).fetchall()
    pools: dict = {}
    for r in rows:
        for s in re.split(r"(?<=[.!?])\s+", r["text"]):
            if s.lower().startswith("we will"):
                pools.setdefault(r["state"], []).append(
                    {"text": s.strip(), "scope": STATES[r["state"]]["name"] if r["state"] else "National",
                     "citation": r["source_citation"], "topic": (issue_of(s) or (None, None))[1]})
    keys = [s for s in STATES if s in pools] + ([None] if None in pools else [])
    out, i = [], 0
    while len(out) < n and any(i < len(pools[k]) for k in keys):     # round-robin: every state gets a line
        out += [pools[k][i] for k in keys if i < len(pools[k])][: n - len(out)]
        i += 1
    return out


def recent_outputs(c, area: Area, n: int = 6) -> list[dict]:
    ow, op = area.outputs()
    return [dict(r) for r in c.execute(f"""SELECT o.output_id, o.kind, o.constituency_id, o.language, o.created_at,
        json_extract(o.payload, '$.constituency.name') place, json_extract(o.payload, '$.event_label') event,
        json_extract(o.payload, '$.generator') generator, u.display_name author
        FROM copilot_outputs o LEFT JOIN users u ON u.user_id = o.created_by
        WHERE o.kind IN ('brief','manifesto') AND {ow} ORDER BY o.created_at DESC, o.rowid DESC LIMIT ?""", [*op, n])]


# ---------------------------------------------------------------------------
# CounterPulse
# ---------------------------------------------------------------------------

def narratives(c, area: Area, n: int = 5) -> list[dict]:
    cw, cp = area.clusters()
    out = []
    for r in c.execute(f"""SELECT * FROM narrative_clusters n WHERE n.status = 'active' AND {cw}
                           ORDER BY n.threat_score DESC, n.last_seen DESC LIMIT ?""", [*cp, n]):
        d = dict(r)
        b = json.loads(d.get("breakdown") or "{}")
        resp = c.execute("""SELECT response_id, medium, status, payload FROM warroom_responses WHERE cluster_id = ?
                            ORDER BY (status = 'approved') DESC, created_at DESC LIMIT 1""", (d["cluster_id"],)).fetchone()
        response = None
        if resp:
            p = json.loads(resp["payload"])
            response = {"response_id": resp["response_id"], "medium": resp["medium"], "medium_label": p.get("medium_label"),
                        "status": resp["status"], "verdict": p["assessment"]["verdict_label"],
                        "lead_fact": re.sub(r"\s*\[S\d+\]", "", (p.get("counter_facts") or [{}])[0].get("fact", "")),
                        "safety": p["safety_check"]["status"]}
        claim = d["claim"] if latin(d["claim"]) else None
        out.append({"cluster_id": d["cluster_id"], "label": d["label"], "state": d["state"], "issue": d["issue"],
                    "threat_score": d["threat_score"], "severity": d["severity"], "item_count": d["item_count"],
                    "items_24h": b.get("items_24h", 0), "trend": b.get("trend", []), "claim": claim,
                    "last_seen": d["last_seen"], "response": response})
    return out


def sentiment(c, area: Area, days: int = 7) -> dict:
    mw, mp = area.media()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = c.execute(f"""SELECT source_type, sentiment_label, COUNT(*) n, ROUND(AVG(negativity),2) neg FROM media_items m
        WHERE {mw} AND m.published_at >= ? GROUP BY source_type, sentiment_label""", [*mp, since]).fetchall()
    by_source: dict = {}
    for r in rows:
        s = by_source.setdefault(SOURCE_TYPES.get(r["source_type"], r["source_type"]), {"negative": 0, "neutral": 0, "positive": 0})
        s[r["sentiment_label"]] = s.get(r["sentiment_label"], 0) + r["n"]
    tot = {k: sum(v[k] for v in by_source.values()) for k in ("negative", "neutral", "positive")}
    n = sum(tot.values())
    return {"days": days, "items": n, **tot, "negative_share": round(100 * tot["negative"] / n) if n else 0,
            "by_source": [{"source": k, **v} for k, v in sorted(by_source.items(), key=lambda kv: -sum(kv[1].values()))]}


def pending_approvals(c, area: Area) -> int:
    cw, cp = area.clusters()
    return c.execute(f"""SELECT COUNT(*) FROM warroom_responses r JOIN narrative_clusters n USING(cluster_id)
        WHERE r.status = 'draft' AND n.status = 'active' AND {cw}""", cp).fetchone()[0]


# ---------------------------------------------------------------------------
# quick actions + overview
# ---------------------------------------------------------------------------

def actions(c, area: Area, k: dict, threats: list[dict], f: dict) -> dict:
    # speech: the constituency with the most persuadable voters that has knowledge-base material
    bw, bp = area.booths()
    with_docs = {r[0] for r in c.execute("SELECT DISTINCT constituency_id FROM copilot_documents WHERE active = 1 AND constituency_id IS NOT NULL")}
    places = c.execute(f"""SELECT b.state, b.constituency, COUNT(*) n FROM voters v JOIN voter_scores s USING(voter_id)
        JOIN booths b ON b.booth_id = v.booth_id WHERE {bw} AND s.quadrant = 'PERSUADE' GROUP BY b.state, b.constituency
        ORDER BY n DESC""", bp).fetchall()
    speech = None
    pick = next((p for p in places if f"{p['state']}-{constituency_code(p['constituency'])}" in with_docs),
                places[0] if places else None)
    if pick:
        cid = f"{pick['state']}-{constituency_code(pick['constituency'])}"
        topic = next((kb_topic(t["issue"]) for t in threats if t["state"] == pick["state"] and t["issue"]), None)
        aud = TOPIC_AUDIENCE.get(topic or "", "farmers")
        speech = {"constituency_id": cid, "name": pick["constituency"], "state": pick["state"], "persuadable": pick["n"],
                  "audience": aud, "audience_label": AUDIENCES[aud][0], "has_documents": cid in with_docs}
    top = next((t for t in threats if t["severity"] in ("Critical", "High")), threats[0] if threats else None)
    return {
        "dispatch": {"cadres": f["cadres"], "dispatched": f["cadres_dispatched"], "pending": f["cadres"] - f["cadres_dispatched"]},
        "speech": speech,
        "threat": {k2: top[k2] for k2 in ("cluster_id", "label", "state", "threat_score", "severity")} | {
            "has_response": bool(top["response"]), "response_status": top["response"]["status"] if top["response"] else None}
        if top else None,
    }


def kb_topic(issue_title: str | None) -> str | None:
    from ..copilot.generate import ISSUES
    return next((topic for _, title, topic in ISSUES if title == issue_title), None)


def overview(c, area: Area, include_states: bool) -> dict:
    k = kpis(c, area)
    f = field_today(c, area)
    threats = narratives(c, area, 6)
    return {
        "area": {"label": area.label, "states": area.states, "constituency": area.constituency},
        "kpis": k, "by_state": by_state(c) if include_states else None,
        "voterengine": {"quadrants": quadrants(c, area), "top_booths": top_booths(c, area, 5), "field": f},
        "copilot": {"grievances": grievances(c, area, 2, 4), "promises": promises(c, area, 4), "recent": recent_outputs(c, area, 5)},
        "warroom": {"narratives": threats, "sentiment": sentiment(c, area), "pending_approvals": pending_approvals(c, area),
                    "severity_scale": [s for _, s in SEVERITY]},
        "actions": actions(c, area, k, threats, f),
    }
