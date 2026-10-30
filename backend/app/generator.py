"""Synthetic data generator.

Produces booths → households → voters with realistic structure:
households share community, scheme access and part of their political lean;
booths contain 2–5 spatial neighbourhoods (mohalla / hamlet / ward) so the
routing engine has real clusters to find. Deterministic for a given seed.
"""
from __future__ import annotations

import json
import math
import random

from .config import STATES

FIRST = {
    "UP": {"M": ["Ramesh", "Suresh", "Anil", "Rahul", "Vikas", "Ajay", "Sanjay", "Deepak", "Manoj", "Amit", "Rohit", "Pankaj"],
           "F": ["Sunita", "Geeta", "Pooja", "Neha", "Anita", "Kavita", "Rekha", "Priya", "Seema", "Rani", "Komal", "Savita"]},
    "PB": {"M": ["Gurpreet", "Harjit", "Manpreet", "Jaswinder", "Kuldeep", "Baljit", "Sukhdev", "Amrik", "Ravinder", "Parminder"],
           "F": ["Harpreet", "Simran", "Jasleen", "Kulwinder", "Rajwinder", "Navneet", "Amandeep", "Sukhpreet", "Manjit", "Gurleen"]},
    "UK": {"M": ["Mohan", "Govind", "Prakash", "Dinesh", "Harish", "Kailash", "Naveen", "Jagdish", "Bhupendra", "Mahesh"],
           "F": ["Kamla", "Basanti", "Deepa", "Hema", "Pushpa", "Lata", "Meena", "Kusum", "Bina", "Janki"]},
    "GA": {"M": ["Prakash", "Rohan", "Sanjay", "Vinod", "Anant", "Sagar", "Dattaraj", "Ravi", "Uday", "Kunal"],
           "F": ["Sneha", "Pratiksha", "Shweta", "Vaishali", "Sushma", "Anjali", "Deepali", "Rupali", "Kavita", "Nisha"]},
}
FIRST_GROUP = {
    "Muslim": {"M": ["Imran", "Salim", "Arif", "Irfan", "Shahid", "Nadeem", "Rashid", "Javed"],
               "F": ["Shabana", "Nazia", "Rukhsar", "Farzana", "Salma", "Ayesha", "Rubina", "Nasreen"]},
    "Catholic": {"M": ["Joseph", "Agnelo", "Francis", "Savio", "Anthony", "Peter", "Cajetan", "Rosario"],
                 "F": ["Maria", "Fatima", "Joyce", "Sylvia", "Agnes", "Rosy", "Lourdes", "Flavia"]},
}
LAST = {
    "UP": ["Sharma", "Verma", "Kumar", "Singh", "Yadav", "Tyagi", "Saini", "Prajapati", "Chauhan", "Gupta", "Pal", "Kashyap"],
    "PB": ["Sandhu", "Gill", "Dhillon", "Sidhu", "Brar", "Grewal", "Sharma", "Arora", "Bansal", "Kaur"],
    "UK": ["Rawat", "Negi", "Bisht", "Joshi", "Pant", "Bhandari", "Thapliyal", "Nautiyal", "Arya", "Tamta"],
    "GA": ["Naik", "Gaonkar", "Kamat", "Prabhu", "Shetye", "Desai", "Parab", "Sawant", "Kerkar", "Borkar"],
}
LAST_GROUP = {
    "Muslim": ["Khan", "Qureshi", "Ansari", "Shaikh", "Siddiqui", "Malik"],
    "Catholic": ["Fernandes", "D'Souza", "Rodrigues", "Pereira", "Gomes", "Colaco", "Mascarenhas", "Dias"],
}
LOCALITIES = {
    "UP": ["Kaili", "Rasulpur", "Daurala", "Jani Khurd", "Kankarkhera", "Lalkurti", "Sadar", "Kasampur", "Pallavpuram", "Rohta"],
    "PB": ["Bhawanigarh", "Ubhawal", "Longowal", "Kanjhla", "Bhasaur", "Badrukhan", "Kakra", "Benra", "Ghanaur", "Sherpur"],
    "UK": ["Kandoliya", "Chopra", "Adwani", "Banghat", "Kirtinagar", "Dhari", "Chauras", "Khirsu", "Buwakhal", "Gholteer"],
    "GA": ["Aquem", "Borda", "Comba", "Malbhat", "Gogol", "Navelim", "Davorlim", "Arlem", "Pajifond", "Monte Hill"],
}


def constituency_code(name: str) -> str:
    """'Sardhana' -> 'SAR', 'Meerut Cantt' -> 'MC'. Shared by the generator and the roll importer."""
    words = name.replace("(", "").replace(")", "").split()
    if not words:
        return "UNK"
    return (words[0][:3] if len(words) == 1 else "".join(w[0] for w in words)).upper()[:3]


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    keys, w = zip(*weights.items())
    return rng.choices(keys, weights=w, k=1)[0]


def _offset(lat: float, lng: float, km: float, rng: random.Random, gaussian=False):
    """Random point within ~km of (lat,lng)."""
    if gaussian:
        dy, dx = rng.gauss(0, km), rng.gauss(0, km)
    else:
        r, t = km * math.sqrt(rng.random()), rng.random() * 2 * math.pi
        dy, dx = r * math.sin(t), r * math.cos(t)
    return lat + dy / 111.0, lng + dx / (111.0 * math.cos(math.radians(lat)))


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def generate_state(code: str, rng: random.Random, booths_per_constituency=3,
                   households_per_booth=(95, 130)) -> dict[str, list[dict]]:
    cfg = STATES[code]
    extras = cfg.get("extras", {})
    out = {"booths": [], "households": [], "voters": [], "cadres": []}
    # Seeded community lean offsets — deliberately arbitrary (see config.py docstring).
    community_offset = {g: rng.uniform(-0.3, 0.3) for g in cfg["community_groups"]}
    spread = cfg.get("booth_spread_km", 3.5 if code != "UK" else 5.0)
    localities = LOCALITIES[code][:]
    rng.shuffle(localities)
    vid = 0

    for c_idx, (const, (clat, clng)) in enumerate(cfg["constituencies"].items()):
        const_code = constituency_code(const)
        for b in range(1, booths_per_constituency + 1):
            booth_id = f"{code}-{const_code}-{b:03d}"
            loc = localities[(c_idx * booths_per_constituency + b - 1) % len(localities)]
            blat, blng = _offset(clat, clng, spread, rng)
            lo, hi = cfg["turnout_base"]
            turnout = round(_clip(rng.uniform(lo, hi) + rng.gauss(0, 3), 35, 95), 1)
            margin = round(rng.uniform(0.4, 6.0) if code == "GA" else rng.uniform(1.0, 24.0), 1)
            b_attrs = {}
            if code == "UK":
                b_attrs["terrain"] = _pick(rng, extras["terrain_clusters"])
                if b_attrs["terrain"] == "High-Hill":
                    turnout = round(turnout - rng.uniform(3, 7), 1)
            booth_lean = rng.gauss(0, 0.22)
            booth = {"booth_id": booth_id, "state": code, "constituency": const,
                     "name": f"Govt. School, {loc} (Booth {b})", "total_voters": 0,
                     "historical_turnout": turnout, "last_margin_pct": margin,
                     "lat": round(blat, 6), "lng": round(blng, 6), "attributes": b_attrs}
            out["booths"].append(booth)

            # 2–5 neighbourhoods per booth
            hood_km = 0.9 if code == "UK" else (0.35 if code == "GA" else 0.6)
            hoods = [_offset(blat, blng, hood_km, rng) for _ in range(rng.randint(2, 5))]
            n_hh = rng.randint(*households_per_booth)
            if code == "GA":
                n_hh = int(n_hh * 0.8)
            for h in range(1, n_hh + 1):
                hh_id = f"{booth_id}-H{h:04d}"
                hlat, hlng = _offset(*rng.choice(hoods), 0.11 if code != "UK" else 0.18, rng, gaussian=True)
                group = _pick(rng, cfg["community_groups"])
                hh_lean = booth_lean + community_offset[group] + rng.gauss(0, 0.28)
                hh_benefit = rng.random() < cfg["beneficiary_rate"]
                hh_schemes = [s for s, p in cfg["schemes"].items() if rng.random() < p] if hh_benefit else []
                if hh_benefit and not hh_schemes:
                    hh_schemes = [_pick(rng, cfg["schemes"])]
                migrant_hh = rng.random() < cfg["migrant_rate"] * 2.2
                size = rng.choices([1, 2, 3, 4, 5, 6, 7], weights=[5, 18, 22, 24, 15, 10, 6])[0]
                if code in ("PB", "GA"):
                    size = min(8, size + rng.choice([0, 0, 1]))
                surname = rng.choice(LAST_GROUP.get(group, LAST[code]))
                hh_attrs = {}
                if code == "PB":
                    hh_attrs["dera_follower"] = rng.random() < extras["dera_follower_rate"]
                    hh_attrs["occupation"] = "Farmer" if rng.random() < extras["farmer_rate"] else rng.choice(["Labour", "Business", "Service", "Transport"])
                    if hh_attrs["occupation"] == "Farmer":
                        hh_attrs["crop"] = _pick(rng, {"Wheat-Paddy": 0.75, "Sugarcane": 0.12, "Cotton": 0.08, "Vegetables": 0.05})
                elif code == "UP":
                    hh_attrs["occupation"] = _pick(rng, {"Farmer": 0.38, "Labour": 0.25, "Business": 0.17, "Service": 0.12, "Artisan": 0.08})
                    if hh_attrs["occupation"] == "Farmer":
                        hh_attrs["crop"] = _pick(rng, {"Sugarcane": 0.6, "Wheat": 0.3, "Vegetables": 0.1})
                elif code == "UK":
                    hh_attrs["terrain"] = b_attrs["terrain"]
                    hh_attrs["occupation"] = _pick(rng, {"Farmer": 0.30, "Service": 0.22, "Ex-Servicemen": 0.15, "Tourism": 0.13, "Labour": 0.20})
                elif code == "GA":
                    hh_attrs["occupation"] = _pick(rng, {"Service": 0.30, "Tourism": 0.22, "Business": 0.20, "Fishing": 0.10, "Gulf / Shipping": 0.10, "Mining-affected": 0.08})
                hh_attrs["family_size"] = size

                out["households"].append({"household_id": hh_id, "booth_id": booth_id, "state": code,
                                          "address": f"H.No. {rng.randint(1, 400)}, {loc}",
                                          "lat": round(hlat, 6), "lng": round(hlng, 6)})
                ages = sorted([rng.randint(18, 85) for _ in range(size)], reverse=True)
                for i, age in enumerate(ages):
                    vid += 1
                    gender = "M" if (i % 2 == 0) ^ (rng.random() < 0.15) else "F"
                    first_pool = FIRST_GROUP.get(group, FIRST[code])[gender]
                    affinity = _clip(hh_lean + rng.gauss(0, 0.2), -1, 1)
                    is_migrant = int(migrant_hh and 20 <= age <= 45 and rng.random() < 0.55)
                    hist_mean = booth["historical_turnout"] / 100 + (0.08 if 35 <= age <= 65 else -0.06 if age < 26 else 0)
                    history = _clip(rng.gauss(hist_mean - (0.25 if is_migrant else 0), 0.18), 0, 1)
                    v_attrs = dict(hh_attrs)
                    if code == "GA":
                        v_attrs["family_head"] = i == 0
                    if code == "PB" and is_migrant:
                        v_attrs["absentee_type"] = rng.choice(["NRI (Canada)", "NRI (Australia)", "NRI (UK)", "Migrant (Other state)"])
                    elif is_migrant:
                        v_attrs["absentee_type"] = rng.choice(["Migrant (Delhi NCR)", "Migrant (Other state)", "Seasonal"])
                    name = f"{rng.choice(first_pool)} {'Kaur' if code == 'PB' and gender == 'F' and group.endswith('Sikh') else surname}"
                    out["voters"].append({
                        "voter_id": f"{code}{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{vid:07d}",
                        "household_id": hh_id, "booth_id": booth_id, "state": code,
                        "name": name, "age": age, "gender": gender, "community_group": group,
                        "scheme_beneficiary": int(bool(hh_schemes)),
                        "scheme_types": ",".join(hh_schemes),
                        "voting_history_index": round(history, 3),
                        "party_affinity": round(affinity, 3),
                        "is_migrant": is_migrant,
                        "phone": f"+91 {rng.choice('6789')}{rng.randint(100000000, 999999999)}",
                        "lat": round(hlat, 6), "lng": round(hlng, 6),
                        "attributes": v_attrs, "source": "mock",
                    })
                    booth["total_voters"] += 1

            out["cadres"].append({
                "cadre_id": f"CAD-{booth_id}",
                "name": f"{rng.choice(FIRST[code]['M'] + FIRST[code]['F'])} {rng.choice(LAST[code])}",
                "state": code, "phone": f"+91 9{rng.randint(100000000, 999999999)}",
                "booth_ids": booth_id,
            })
    return out


# Mock consent mix. A separate RNG keeps the voter data identical to v1 for the same seed.
CONSENT_MIX = {"GRANTED": 0.55, "PENDING": 0.38, "OPT_OUT": 0.07}


def assign_mock_consent(voters: list[dict], rng: random.Random) -> None:
    from .db import now_iso
    ts = now_iso()
    for v in voters:
        v["consent_status"] = _pick(rng, CONSENT_MIX)
        v["consent_updated_at"] = ts if v["consent_status"] != "PENDING" else None
        v["consent_source"] = "mock"


def seed_database(conn, seed: int = 42, states=None) -> dict[str, int]:
    """Wipe and regenerate all mock data and demo logins, then run the scoring pipeline."""
    from .auth import seed_demo_users
    from .db import reset_all
    from .ingestion import insert_records
    from .scoring import run_scoring

    reset_all(conn)
    rng, consent_rng = random.Random(seed), random.Random(seed + 1)
    counts = {"booths": 0, "households": 0, "voters": 0, "cadres": 0}
    for code in states or STATES:
        data = generate_state(code, rng)
        assign_mock_consent(data["voters"], consent_rng)
        insert_records(conn, data)
        for k in counts:
            counts[k] += len(data[k])
    conn.commit()
    counts.update(seed_demo_users(conn))
    from .knowledge import seed_knowledge
    from .copilot.kb import seed_corpus
    seed_knowledge(conn)              # no-op when the campaign already has a knowledge base
    seed_corpus(conn)                 # JanVani Copilot sample corpus (synthetic, labelled)
    from .warroom.seed import seed_feed
    seed_feed(conn)                   # CounterPulse sample media feed (synthetic, labelled)
    run_scoring(conn)
    return counts


def export_json(conn, path) -> None:
    from .db import rows
    payload = {t: rows(conn.execute(f"SELECT * FROM {t}")) for t in ["booths", "households", "voters", "cadres"]}
    with open(path, "w") as f:
        json.dump(payload, f, indent=1)
