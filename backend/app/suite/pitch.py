"""One-click pitch demo.

The pitch demo is a separate SQLite workspace (``<db>-pitch-demo.db``), never the
campaign's real database. A strategist switches *their own browser session* into it
(an HTTP-only cookie); every other user, including cadres syncing from the field,
keeps working on real data. Sessions are stamped with their workspace, so a login
made inside the demo cannot be replayed against the real workspace.

``build()`` creates a complete, presentation-ready state for UP, Punjab, Uttarakhand
and Goa in a few seconds:

* VoterEngine: ~10,000 scored synthetic voters, 24 booths, 24 cadres, today's routes
  dispatched with field progress, doorstep consent and device syncs
* JanVani Copilot: the sample corpus plus party commitments and local records for all
  four states, and saved local manifestos and rally briefs per lead constituency
* CounterPulse AI: a multi-state media feed (TV, print, YouTube, social) forming
  narratives at every severity, with approved and draft counter-briefs
* Today's field directives for each state

Everything is generated offline (no LLM calls, no cost) and labelled as sample data.
"""
from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db
from ..auth import seed_demo_users, today_local
from ..config import ENV
from ..copilot import generate as cp_gen
from ..copilot import kb
from ..generator import seed_database
from ..knowledge import add_directive
from ..ops import touch_device
from ..routing import generate_route
from ..sync import sync
from ..warroom import counter as wr_counter
from ..warroom.engine import ingest_item, score_clusters

PITCH_ACTOR = "pitch-demo"
VERSION = "pitch-2026.1"
SEED = 2027
STRATEGIST = "U-admin"
CORPUS = Path(__file__).with_name("pitch_corpus.json")
LEADS = {"UP": "Sardhana", "PB": "Sangrur", "UK": "Pauri", "GA": "Margao"}
BRIEFS = {"UP": ("rally_speech", "farmers"), "PB": ("rally_speech", "farmers"),
          "UK": ("rally_speech", "youth"), "GA": ("door_to_door", "women")}
COUNTERS = {"UP": ("tv_brief", True), "PB": ("whatsapp", True), "UK": ("press_release", False), "GA": ("press_release", False)}

# (state, constituency, source, channel, text, reach, hours ago) — every channel is marked "(sample)"
FEED = [
    ("UP", "Sardhana", "tv", "Pradesh 24x7 (sample)", "Farmers' dharna at the Daurala mill enters day four; opposition MLA says cane dues of Rs 100 crore are unpaid since July.", 280000, 2),
    ("UP", "Sardhana", "social", "Kisan Ekta Sardhana (sample)", "Tractor march to the tehsil tomorrow over pending cane payments. Mill dues delayed again this season.", 22000, 4),
    ("UP", "Meerut Cantt", "print", "Meerut Samachar (sample)", "Six-hour power cuts on rural feeders anger farmers in Meerut villages during sowing.", 36000, 10),
    ("UP", "Sardhana", "social", "Sardhana Updates (sample)", "Power cuts again tonight in Sardhana villages; pumps idle and farmers complain at the substation.", 9000, 6),
    ("UP", "Meerut Cantt", "youtube", "Ground Report UP (sample)", "Video: farmers protest outside the power substation over daily outages and power cuts in the fields.", 48000, 15),
    ("PB", "Sangrur", "social", "Sangrur Live (sample)", "Paddy lifting still slow at Sangrur mandi; farmers say payments are pending for ten days.", 17000, 3),
    ("PB", "Dhuri", "print", "Malwa Times (sample)", "Dhuri arhtiyas warn of a strike as procurement slows and paddy heaps pile up in the grain market.", 41000, 9),
    ("PB", "Sangrur", "youtube", "Punjab Pulse (sample)", "Mothers in Sangrur villages protest against drug abuse among youth and say the de-addiction centre has too few beds.", 62000, 20),
    ("PB", "Sangrur", "social", "Sangrur Youth Front (sample)", "Another overdose in our village. Drug abuse is destroying young people and nobody is acting.", 14000, 30),
    ("PB", "Sangrur", "print", "Malwa Times (sample)", "Drug abuse cases rise in Sangrur; families demand more de-addiction beds and counselling.", 39000, 44),
    ("UK", "Pauri", "tv", "Uttarakhand Abhi (sample)", "Villagers near Pauri block the highway after the road blocked by landslides stays shut for a week; they say repair work is incomplete.", 120000, 5),
    ("UK", "Pauri", "social", "Pauri Youth Collective (sample)", "Monsoon damage: road blocked again near Kot, the ambulance could not reach the village.", 9500, 12),
    ("UK", "Pauri", "print", "Garhwal Darpan (sample)", "Kot health centre has had no doctor for days; patients from hill villages travel to Srinagar for basic care.", 18000, 26),
    ("UK", "Srinagar (Garhwal)", "social", "Garhwal Voices (sample)", "No lady doctor at the hospital again and pregnant women are sent to Dehradun. Health centre posts lie empty.", 7000, 40),
    ("GA", "Margao", "tv", "Goa 365 (sample)", "Margao residents protest at the PWD office over irregular water supply; tankers fail to reach Borda.", 90000, 4),
    ("GA", "Margao", "social", "Margao Citizens Network (sample)", "Day 21 of irregular water supply in Aquem. Pipeline work stalled and the tanker schedule ignored.", 12500, 7),
    ("GA", "Fatorda", "youtube", "Goa Ground Zero (sample)", "Young people say there are no local jobs in Salcete and many are now working abroad on ships.", 28000, 30),
    ("GA", "Margao", "print", "Salcete Herald (sample)", "Job seekers queue at the Margao employment fair; youth say local jobs are scarce.", 15000, 50),
]
DIRECTIVES = {
    "UP": "Ask farmers whether cane dues for the last crushing season have been paid, and note the mill and the amount pending.",
    "PB": "Ask families about paddy lifting and payment delays at their mandi, and whether anyone at home needs de-addiction support.",
    "UK": "Note any road stretch still blocked after the monsoon and how far the household is from a working health centre.",
    "GA": "Ask how many days this week the household had piped water, and whether young members are looking for local work.",
}
OUTCOMES = [("VISITED", 0.68), ("NOT_HOME", 0.18), ("REVISIT", 0.08), ("REFUSED", 0.06)]


def demo_allowed() -> bool:
    """Off in production unless explicitly enabled (VOTERENGINE_PITCH_DEMO=1)."""
    flag = os.environ.get("VOTERENGINE_PITCH_DEMO")
    return flag == "1" if ENV == "production" else flag != "0"


def demo_meta(path: Path | None = None) -> dict | None:
    p = path or db.workspace_path("demo")
    if not p.exists():
        return None
    try:
        c = db.connect(p)
        try:
            return {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM app_meta")} or None
        finally:
            c.close()
    except Exception:
        return None


def is_stale(meta: dict | None) -> bool:
    """Rebuild when missing, from an older build, or not built today (routes and feeds are dated)."""
    return not meta or meta.get("version") != VERSION or meta.get("built_date") != today_local()


def _remove(p: Path) -> None:
    for x in (p, Path(f"{p}-wal"), Path(f"{p}-shm")):
        try:
            x.unlink()
        except FileNotFoundError:
            pass


def build(target: Path | None = None) -> dict:
    """Build the pitch workspace into a temporary file, then swap it in atomically."""
    target = target or db.workspace_path("demo")
    tmp = target.with_name(f"{target.stem}.building{target.suffix}")
    _remove(tmp)
    t0 = time.time()
    c = db.connect(tmp)
    try:
        db.init_schema(c)
        counts = seed_database(c, seed=SEED)
        seed_demo_users(c, force=True)
        rng = random.Random(SEED)
        stats = {"voters": counts.get("voters", 0), "booths": counts.get("booths", 0)}
        stats["documents"] = _corpus(c)
        stats["media_items"] = _feed(c)
        stats["directives"] = _directives(c)
        stats.update(_field(c, rng))
        stats.update(_copilot(c))
        stats.update(_counters(c))
        meta = {"version": VERSION, "built_at": db.now_iso(), "built_date": today_local(), "seed": str(SEED),
                "stats": json.dumps(stats)}
        c.executemany("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", list(meta.items()))
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()
    for side in (Path(f"{target}-wal"), Path(f"{target}-shm")):   # old sidecars must not meet the new file
        try:
            side.unlink()
        except FileNotFoundError:
            pass
    os.replace(tmp, target)
    _remove(tmp)
    stats["seconds"] = round(time.time() - t0, 1)
    return {**{k: v for k, v in meta.items() if k != "stats"}, "stats": stats}


def ensure_user(user: dict) -> str:
    """Make the presenter's own login exist in the demo workspace; returns the demo user_id to use."""
    c = db.connect(db.workspace_path("demo"))
    try:
        if user["role"] == "ADMIN":
            c.execute("""INSERT OR IGNORE INTO users (user_id, username, password_hash, display_name, role, state,
                         constituency, cadre_id, active, created_at) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                      (user["user_id"], user["username"], user["password_hash"], user["display_name"], "ADMIN",
                       None, None, None, db.now_iso()))
            c.commit()
        row = c.execute("SELECT user_id FROM users WHERE user_id = ? AND role = 'ADMIN'", (user["user_id"],)).fetchone()
        return row["user_id"] if row else STRATEGIST
    finally:
        c.close()


# ---------------------------------------------------------------------------
# build steps
# ---------------------------------------------------------------------------

def _corpus(c) -> int:
    n = 0
    for d in json.loads(CORPUS.read_text())["documents"]:
        raw = {"title": d["title"], "pages": [(p["page"], p["text"]) for p in d["pages"]], "meta": {}}
        res = kb.ingest(c, raw, {k: d.get(k) for k in ("state", "constituency", "doc_type", "topics", "source_citation",
                                                       "published_on", "booth_ids", "scope")}, "seed", "pitch_corpus.json")
        n += res["status"] == "indexed"
    c.commit()
    return c.execute("SELECT COUNT(*) FROM copilot_documents").fetchone()[0]


def _feed(c) -> int:
    now = datetime.now(timezone.utc)
    for state, const, src, channel, text, reach, h in FEED:
        ingest_item(c, {"state": state, "constituency": const, "source_type": src, "channel": channel, "text": text,
                        "reach": reach, "timestamp": (now - timedelta(hours=h)).isoformat(timespec="seconds")}, "sample-feed")
    score_clusters(c)
    c.commit()
    return c.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]


def _directives(c) -> int:
    d = today_local()
    for state, text in DIRECTIVES.items():
        add_directive(c, text, d, state, None, STRATEGIST)
    c.commit()
    return len(DIRECTIVES)


def _pick(rng, weighted):
    x, acc = rng.random(), 0.0
    for v, w in weighted:
        acc += w
        if x <= acc:
            return v
    return weighted[-1][0]


def _field(c, rng) -> dict:
    """Dispatch today's routes and replay a morning of door-to-door work through the real sync path."""
    d = today_local()
    now = datetime.now(timezone.utc)
    cadres = [r["cadre_id"] for r in c.execute("SELECT cadre_id FROM cadres ORDER BY cadre_id")]
    dispatched = applied = 0
    for i, cid in enumerate(cadres):
        if i % 6 == 5:          # a few cadres haven't been dispatched yet: gives the "Dispatch" action something to do
            continue
        route = generate_route(c, cid, d, 16)
        if not route.get("route_id"):
            continue
        dispatched += 1
        stops = [dict(r) for r in c.execute("SELECT stop_id, household_id FROM route_stops WHERE route_id = ? ORDER BY seq",
                                            (route["route_id"],))]
        done = int(len(stops) * rng.uniform(0.3, 0.85))
        ops, t = [], now - timedelta(minutes=done * 11 + rng.randint(5, 40))
        for s in stops[:done]:
            t += timedelta(minutes=rng.randint(7, 14))
            status = _pick(rng, OUTCOMES)
            ops.append({"op_id": f"pitch-{cid}-{s['stop_id']}", "type": "stop_status", "stop_id": s["stop_id"],
                        "status": status, "client_ts": min(t, now).isoformat(timespec="seconds")})
            if status == "VISITED":
                for v in c.execute("SELECT voter_id FROM voters WHERE household_id = ? ORDER BY voter_id LIMIT 2", (s["household_id"],)):
                    choice = _pick(rng, [("GRANTED", 0.62), (None, 0.33), ("OPT_OUT", 0.05)])
                    if choice:
                        ops.append({"op_id": f"pitch-{cid}-c-{v['voter_id']}", "type": "consent", "voter_id": v["voter_id"],
                                    "status": choice, "client_ts": min(t, now).isoformat(timespec="seconds")})
        res = sync(c, {"cadre_id": cid, "user_id": f"U-{cid.lower()}"}, ops)
        applied += res["summary"]["applied"]
        touch_device(c, cid, synced=True, queue_remaining=rng.choice([0, 0, 0, 2, 5]), app_version="2.1.0")
    c.commit()
    return {"routes_dispatched": dispatched, "field_updates": applied, "cadres": len(cadres)}


def _place(c, state, name) -> dict:
    p = next(x for x in kb.known_constituencies(c) if x["state"] == state and x["name"] == name)
    return {"id": p["id"], "name": p["name"], "state": p["state"]}


def _copilot(c) -> dict:
    briefs = manifestos = 0
    for state, name in LEADS.items():
        place = _place(c, state, name)
        out = cp_gen.manifesto(c, place, "en", STRATEGIST, offline=True)
        manifestos += bool(out.get("output_id"))
        ev, aud = BRIEFS[state]
        out = cp_gen.brief(c, place, ev, aud, "en", STRATEGIST, offline=True)
        briefs += bool(out.get("output_id"))
    c.commit()
    return {"manifestos": manifestos, "briefs": briefs}


def _counters(c) -> dict:
    n = approved = 0
    for state, (medium, approve) in COUNTERS.items():
        top = c.execute("""SELECT cluster_id FROM narrative_clusters WHERE state = ? AND status = 'active'
                           ORDER BY threat_score DESC LIMIT 1""", (state,)).fetchone()
        if not top:
            continue
        out = wr_counter.generate(c, top["cluster_id"], medium, "en", STRATEGIST, offline=True)
        n += 1
        if approve and out["safety_check"]["status"] != "blocked":
            c.execute("UPDATE warroom_responses SET status = 'approved', reviewed_by = ?, reviewed_at = ? WHERE response_id = ?",
                      (STRATEGIST, db.now_iso(), out["response_id"]))
            approved += 1
    c.commit()
    return {"counter_briefs": n, "approved_counters": approved}
