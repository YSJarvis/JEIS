"""Cadre routing & priority engine.

1. Roll voter scores up to households (a door is the unit of work).
2. Cluster priority households by proximity (k-means on lat/lng).
3. Pick the highest-value cluster for today, top it up from the nearest
   remaining doors, and order stops with nearest-neighbour + 2-opt.
"""
from __future__ import annotations

import math
import random
from datetime import date

from .db import now_iso, rows
from .scoring import haversine_km

PRIORITY_FLOOR = 40.0          # households below this are not routed
FINAL_STATUSES = ("VISITED", "REFUSED")


def household_priorities(conn, booth_ids: list[str] | None = None, state: str | None = None,
                         include_done=False) -> list[dict]:
    clauses, params = [], []
    if booth_ids:
        clauses.append(f"h.booth_id IN ({','.join('?' * len(booth_ids))})"); params += booth_ids
    if state:
        clauses.append("h.state = ?"); params.append(state)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = conn.execute(f"""
        SELECT h.household_id, h.booth_id, h.address, h.lat, h.lng,
               COUNT(v.voter_id) AS members,
               MAX(s.priority) AS priority,
               SUM(s.quadrant = 'PERSUADE') AS n_persuade,
               SUM(s.quadrant = 'GOTV') AS n_gotv,
               (SELECT rs.status FROM route_stops rs WHERE rs.household_id = h.household_id
                ORDER BY COALESCE(rs.updated_at, '') DESC, rs.stop_id DESC LIMIT 1) AS last_status
        FROM households h
        JOIN voters v ON v.household_id = h.household_id
        JOIN voter_scores s ON s.voter_id = v.voter_id
        {where}
        GROUP BY h.household_id""", params)
    out = []
    for r in rows(cur):
        if r["priority"] is None or r["priority"] < PRIORITY_FLOOR:
            continue
        if not include_done and r["last_status"] in FINAL_STATUSES:
            continue
        r["primary_quadrant"] = "PERSUADE" if r["n_persuade"] >= r["n_gotv"] and r["n_persuade"] else "GOTV"
        out.append(r)
    return out


def kmeans(points: list[tuple[float, float]], k: int, iters=25, seed=7) -> list[int]:
    """Plain k-means with k-means++ init. Points are (lat, lng); at booth scale
    planar distance on degrees is an adequate proxy for clustering."""
    n = len(points)
    if n == 0:
        return []
    k = max(1, min(k, n))
    rng = random.Random(seed)
    cents = [points[rng.randrange(n)]]
    while len(cents) < k:
        d2 = [min((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 for c in cents) for p in points]
        tot = sum(d2) or 1
        r, acc = rng.random() * tot, 0.0
        for i, d in enumerate(d2):
            acc += d
            if acc >= r:
                cents.append(points[i]); break
        else:
            cents.append(points[-1])
    labels = [0] * n
    for _ in range(iters):
        changed = False
        for i, p in enumerate(points):
            best = min(range(k), key=lambda j: (p[0] - cents[j][0]) ** 2 + (p[1] - cents[j][1]) ** 2)
            if best != labels[i]:
                labels[i], changed = best, True
        for j in range(k):
            mem = [points[i] for i in range(n) if labels[i] == j]
            if mem:
                cents[j] = (sum(m[0] for m in mem) / len(mem), sum(m[1] for m in mem) / len(mem))
        if not changed:
            break
    return labels


def build_clusters(households: list[dict], target_size=25) -> list[dict]:
    if not households:
        return []
    k = math.ceil(len(households) / target_size)
    labels = kmeans([(h["lat"], h["lng"]) for h in households], k)
    groups: dict[int, list[dict]] = {}
    for h, lab in zip(households, labels):
        groups.setdefault(lab, []).append(h)
    clusters = []
    for lab, mem in groups.items():
        clusters.append({
            "cluster_id": lab,
            "lat": sum(m["lat"] for m in mem) / len(mem),
            "lng": sum(m["lng"] for m in mem) / len(mem),
            "households": len(mem),
            "voters": sum(m["members"] for m in mem),
            "persuade": sum(m["n_persuade"] for m in mem),
            "gotv": sum(m["n_gotv"] for m in mem),
            "total_priority": round(sum(m["priority"] for m in mem), 1),
            "members": mem,
        })
    clusters.sort(key=lambda c: c["total_priority"], reverse=True)
    for rank, c in enumerate(clusters, 1):
        c["rank"] = rank
    return clusters


def _path_len(start, pts, order) -> float:
    d, prev = 0.0, start
    for i in order:
        d += haversine_km(prev[0], prev[1], pts[i][0], pts[i][1]); prev = pts[i]
    return d


def order_stops(start: tuple[float, float], pts: list[tuple[float, float]]) -> list[int]:
    """Open-path TSP heuristic: nearest neighbour, then 2-opt until no gain."""
    remaining, order, cur = set(range(len(pts))), [], start
    while remaining:
        nxt = min(remaining, key=lambda i: haversine_km(cur[0], cur[1], pts[i][0], pts[i][1]))
        order.append(nxt); remaining.remove(nxt); cur = pts[nxt]
    improved, best = True, _path_len(start, pts, order)
    while improved:
        improved = False
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                cand = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                L = _path_len(start, pts, cand)
                if L + 1e-9 < best:
                    order, best, improved = cand, L, True
    return order


def generate_route(conn, cadre_id: str, route_date: str | None = None, max_stops=20,
                   regenerate=False) -> dict:
    route_date = route_date or date.today().isoformat()
    cadre = conn.execute("SELECT * FROM cadres WHERE cadre_id = ?", (cadre_id,)).fetchone()
    if not cadre:
        raise KeyError(f"Unknown cadre {cadre_id}")
    existing = conn.execute("SELECT route_id FROM routes WHERE cadre_id = ? AND route_date = ?",
                            (cadre_id, route_date)).fetchone()
    if existing and not regenerate:
        return get_route(conn, existing["route_id"])

    booth_ids = cadre["booth_ids"].split(",")
    booth = conn.execute("SELECT lat, lng FROM booths WHERE booth_id = ?", (booth_ids[0],)).fetchone()
    start = (booth["lat"], booth["lng"])
    kept: list = []
    if existing:  # rebuild: keep stops already worked, replace only pending ones
        conn.execute("DELETE FROM route_stops WHERE route_id = ? AND status = 'PENDING'", (existing["route_id"],))
        kept = conn.execute("""SELECT rs.household_id, h.lat, h.lng FROM route_stops rs
            JOIN households h USING(household_id) WHERE route_id = ? ORDER BY seq""", (existing["route_id"],)).fetchall()
        if kept:
            start = (kept[-1]["lat"], kept[-1]["lng"])  # continue from the last door worked

    taken_today = {k["household_id"] for k in kept}
    cands = [c for c in household_priorities(conn, booth_ids=booth_ids) if c["household_id"] not in taken_today]
    if not cands and not kept:
        if existing:
            conn.execute("DELETE FROM routes WHERE route_id = ?", (existing["route_id"],))
            conn.commit()
        return {"route_id": None, "cadre_id": cadre_id, "route_date": route_date, "stops": [],
                "total_km": 0, "message": "No eligible priority households left in this area."}

    chosen: list[dict] = []
    if cands:
        clusters = build_clusters(cands, target_size=max_stops)
        best = clusters[0]
        chosen = sorted(best["members"], key=lambda m: m["priority"], reverse=True)[:max_stops]
        if len(chosen) < max_stops:  # top up from the nearest remaining doors
            ids = {m["household_id"] for m in chosen}
            rest = sorted((c for c in cands if c["household_id"] not in ids),
                          key=lambda c: haversine_km(best["lat"], best["lng"], c["lat"], c["lng"]))
            chosen += rest[: max_stops - len(chosen)]

    pts = [(c["lat"], c["lng"]) for c in chosen]
    order = order_stops(start, pts)
    if existing:
        route_id = existing["route_id"]
    else:
        route_id = conn.execute("INSERT INTO routes (cadre_id, route_date, total_km, created_at) VALUES (?,?,0,?)",
                                (cadre_id, route_date, now_iso())).lastrowid
    prev, seq0 = start, len(kept)
    for n, i in enumerate(order, 1):
        c = chosen[i]
        leg = haversine_km(prev[0], prev[1], c["lat"], c["lng"]); prev = (c["lat"], c["lng"])
        conn.execute("""INSERT INTO route_stops (route_id, seq, household_id, leg_km, household_priority)
                        VALUES (?,?,?,?,?)""", (route_id, seq0 + n, c["household_id"], round(leg, 3), c["priority"]))
    total = conn.execute("SELECT COALESCE(SUM(leg_km),0) FROM route_stops WHERE route_id = ?", (route_id,)).fetchone()[0]
    conn.execute("UPDATE routes SET total_km = ? WHERE route_id = ?", (round(total, 2), route_id))
    conn.commit()
    return get_route(conn, route_id)


def get_route(conn, route_id: int) -> dict:
    r = conn.execute("""SELECT r.*, c.name AS cadre_name, c.booth_ids FROM routes r
                        JOIN cadres c ON c.cadre_id = r.cadre_id WHERE route_id = ?""", (route_id,)).fetchone()
    stops = rows(conn.execute("""SELECT rs.*, h.address, h.lat, h.lng, h.booth_id
        FROM route_stops rs JOIN households h ON h.household_id = rs.household_id
        WHERE rs.route_id = ? ORDER BY rs.seq""", (route_id,)))
    out = dict(r)
    out["stops"] = stops
    out["progress"] = {"done": sum(s["status"] != "PENDING" for s in stops), "total": len(stops)}
    return out
