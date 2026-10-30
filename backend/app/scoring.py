"""Predictive scoring pipeline (heuristic model v1).

Each score is a transparent weighted sum of normalised features (0..1), so a
field coordinator can see *why* a voter is flagged. The `score_voter`
function is pure — swap it for a trained model (e.g. gradient boosting on
past canvass outcomes) by keeping the same input/output contract.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

from .config import MODEL_VERSION, STATES, THRESHOLDS
from .db import now_iso


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------- feature functions (all return 0..1) ----------

def f_swing_age(age: int) -> float:
    if age <= 25: return 0.90
    if age <= 35: return 0.75
    if age <= 50: return 0.55
    if age <= 65: return 0.40
    return 0.30


def f_turnout_age(age: int) -> float:
    if age <= 22: return 0.50
    if age <= 30: return 0.62
    if age <= 60: return 0.88
    if age <= 75: return 0.75
    return 0.50


def f_scheme(beneficiary: int, scheme_types: str) -> float:
    if not beneficiary:
        return 0.10
    n = len([s for s in (scheme_types or "").split(",") if s.strip()])
    return min(1.0, 0.55 + 0.15 * max(1, min(n, 3)))


TERRAIN_SWING = {"Valley": 0.40, "Mid-Hill": 0.55, "High-Hill": 0.75}
TERRAIN_DIST = {"Valley": 1.0, "Mid-Hill": 1.4, "High-Hill": 2.0}


def score_voter(v: dict, ctx: dict, params: dict | None = None) -> dict:
    """Pure function. v: voter row (+ attributes dict); ctx: booth_turnout,
    dist_km, community_lean; params: weights override (defaults: config.STATES).
    Returns raw swing, turnout (0..100) and drivers."""
    cfg = params or STATES.get(v["state"], STATES["UP"])
    a = v.get("attributes") or {}

    # --- Swing propensity ---
    feats = {
        "neutrality": 1 - abs(v["party_affinity"]),
        "scheme": f_scheme(v["scheme_beneficiary"], v["scheme_types"]),
        "age": f_swing_age(v["age"]),
        # contested communities (group lean near zero inside this booth) swing more
        "community": 1 - min(1.0, abs(ctx.get("community_lean", 0.0)) * 2),
        "dera": 0.80 if a.get("dera_follower") else 0.40,
        "agrarian": 0.75 if a.get("occupation") == "Farmer" else 0.40,
        "terrain": TERRAIN_SWING.get(a.get("terrain"), 0.5),
        "family": min(1.0, a.get("family_size", 3) / 6) * 0.6 + (0.4 if a.get("family_head") else 0.2),
    }
    sw = cfg["swing_weights"]
    total_w = sum(sw.values())
    swing_contrib = {k: 100 * w * feats[k] / total_w for k, w in sw.items()}
    swing = sum(swing_contrib.values())

    # --- Turnout probability ---
    dist = ctx.get("dist_km", 0.5) * TERRAIN_DIST.get(a.get("terrain"), 1.0)
    tfeats = {
        "history": v["voting_history_index"],
        "booth": ctx.get("booth_turnout", 60) / 100,
        "age": f_turnout_age(v["age"]),
        "local": math.exp(-dist / 2.0),  # distance (terrain-adjusted) to polling booth
    }
    tw = cfg["turnout_weights"]
    tw_total = sum(tw.values())
    turnout_contrib = {k: 100 * w * tfeats[k] / tw_total for k, w in tw.items()}
    turnout = sum(turnout_contrib.values())
    if v["is_migrant"]:
        penalty = turnout * cfg["migrant_penalty"]
        turnout_contrib["migrant_absentee"] = -penalty
        turnout -= penalty

    return {
        "raw_swing": swing, "turnout_score": round(min(100, max(0, turnout)), 1),
        "drivers": {"swing": {k: round(x, 1) for k, x in swing_contrib.items()},
                    "turnout": {k: round(x, 1) for k, x in turnout_contrib.items()}},
    }


def calibrate_swing(raw: float, mean: float, sd: float) -> float:
    """Standardise raw swing within the state (50 = state average, +15 per SD)
    so thresholds mean the same thing in UP and Goa despite different weights."""
    return round(min(100.0, max(0.0, 50 + 15 * (raw - mean) / (sd or 1))), 1)


def finalize(v: dict, s: dict, ctx: dict, mean: float, sd: float, thresholds: dict | None = None) -> dict:
    swing = calibrate_swing(s["raw_swing"], mean, sd)
    quadrant = classify(swing, s["turnout_score"], v["party_affinity"], thresholds)
    s["drivers"]["calibration"] = {"raw_swing": round(s["raw_swing"], 1), "state_mean": round(mean, 1), "state_sd": round(sd, 1)}
    return {"swing_score": swing, "turnout_score": s["turnout_score"], "quadrant": quadrant,
            "priority": contact_priority(quadrant, swing, s["turnout_score"], ctx.get("margin_pct")),
            "drivers": s["drivers"]}


def classify(swing: float, turnout: float, affinity: float, thresholds: dict | None = None) -> str:
    t = thresholds or THRESHOLDS
    if affinity <= t["opp_affinity"] and swing < t["swing_high"]:
        return "OPPOSITION"
    if swing >= t["swing_high"] and turnout >= t["turnout_high"]:
        return "PERSUADE"
    if affinity >= t["loyal_affinity"] and turnout < t["turnout_high"]:
        return "GOTV"
    return "LOW"  # neutral low-turnout, and already-secured loyal high-turnout voters


def contact_priority(quadrant: str, swing: float, turnout: float, margin_pct) -> float:
    base = {
        "PERSUADE": 50 + 0.5 * (swing * turnout / 100),
        "GOTV": 40 + 0.4 * (100 - turnout),
        "LOW": 0.25 * swing,
        "OPPOSITION": 0.0,
    }[quadrant]
    tightness = max(0.0, 1 - (margin_pct or 15) / 15)  # tight booths get a boost
    return round(min(100.0, base * (1 + 0.3 * tightness)), 1)


def booth_priority(n_total, n_persuade, n_gotv, margin_pct, turnout) -> float:
    if not n_total:
        return 0.0
    actionable = (n_persuade + 0.7 * n_gotv) / n_total
    tight = max(0.0, 1 - (margin_pct or 15) / 15)
    headroom = (100 - turnout) / 100
    s = 100 * (0.5 * min(1, actionable / 0.5) + 0.35 * tight + 0.15 * min(1, headroom / 0.45))
    return round(s, 1)


def run_scoring(conn, state: str | None = None, config: dict | None = None) -> dict:
    """Score every voter (optionally one state) and refresh booth priorities.
    `config` is {"states": {code: params}, "thresholds": {...}, "version": n}; when
    omitted the admin-saved configuration (scoring_config table) is used.
    Opted-out voters are never scored (DPDP: processing stops on withdrawal)."""
    from .privacy import enforce_opt_outs
    from .scoring_config import effective_config

    config = config or effective_config(conn)
    enforce_opt_outs(conn)
    where, params = ("WHERE v.consent_status != 'OPT_OUT' AND v.state = ?", (state,)) if state \
        else ("WHERE v.consent_status != 'OPT_OUT'", ())
    cur = conn.execute(f"""
        SELECT v.*, b.historical_turnout AS booth_turnout, b.last_margin_pct AS margin_pct,
               b.lat AS b_lat, b.lng AS b_lng, h.lat AS h_lat, h.lng AS h_lng
        FROM voters v JOIN booths b ON b.booth_id = v.booth_id
        JOIN households h ON h.household_id = v.household_id {where}""", params)
    voters = []
    for r in cur.fetchall():
        d = dict(r)
        d["attributes"] = json.loads(d["attributes"] or "{}")
        voters.append(d)

    # community lean per (booth, group): data-driven contestedness
    sums = defaultdict(lambda: [0.0, 0])
    for v in voters:
        s = sums[(v["booth_id"], v["community_group"])]
        s[0] += v["party_affinity"]; s[1] += 1
    lean = {k: s[0] / s[1] for k, s in sums.items()}

    # pass 1: raw scores; pass 2: state-level calibration + classification
    raw = []
    for v in voters:
        ctx = {"booth_turnout": v["booth_turnout"], "margin_pct": v["margin_pct"],
               "dist_km": haversine_km(v["h_lat"], v["h_lng"], v["b_lat"], v["b_lng"]),
               "community_lean": lean[(v["booth_id"], v["community_group"])]}
        raw.append((v, ctx, score_voter(v, ctx, config["states"].get(v["state"]))))
    by_state = defaultdict(list)
    for v, _, s in raw:
        by_state[v["state"]].append(s["raw_swing"])
    stats = {}
    for st, xs in by_state.items():
        m = sum(xs) / len(xs)
        stats[st] = (m, math.sqrt(sum((x - m) ** 2 for x in xs) / max(1, len(xs) - 1)))

    ts = now_iso()
    version = f"{MODEL_VERSION}+cfg{config.get('version', 0)}"
    out_rows, booth_stats = [], defaultdict(lambda: {"n": 0, "PERSUADE": 0, "GOTV": 0})
    for v, ctx, s0 in raw:
        s = finalize(v, s0, ctx, *stats[v["state"]], thresholds=config["thresholds"])
        out_rows.append((v["voter_id"], s["swing_score"], s["turnout_score"], s["quadrant"],
                         s["priority"], json.dumps(s["drivers"]), version, ts))
        bs = booth_stats[v["booth_id"]]
        bs["n"] += 1
        if s["quadrant"] in ("PERSUADE", "GOTV"):
            bs[s["quadrant"]] += 1

    conn.executemany("""INSERT OR REPLACE INTO voter_scores
        (voter_id, swing_score, turnout_score, quadrant, priority, drivers, model_version, scored_at)
        VALUES (?,?,?,?,?,?,?,?)""", out_rows)

    for b in conn.execute("SELECT booth_id, last_margin_pct, historical_turnout FROM booths").fetchall():
        st = booth_stats.get(b["booth_id"])
        if st is None:
            continue
        conn.execute("UPDATE booths SET priority_score = ?, total_voters = ? WHERE booth_id = ?",
                     (booth_priority(st["n"], st["PERSUADE"], st["GOTV"], b["last_margin_pct"],
                                     b["historical_turnout"]), st["n"], b["booth_id"]))
    conn.commit()
    return {"scored": len(out_rows), "model_version": version, "scored_at": ts}
