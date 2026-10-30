"""Data ingestion: the seam where real voter rolls replace mock data.

Accepts CSV or JSON in loose real-world shapes (EPIC/part_no/caste columns,
"Yes"/"No" flags, percentages) and normalises them into the canonical schema.
Unknown columns are preserved in `attributes` so nothing is lost. Each row is
validated independently; bad rows are reported, never silently dropped.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict

from .config import STATES
from .db import now_iso
from .privacy import log_consent, normalize_consent

ALIASES = {
    "voter_id": ["voter_id", "epic", "epic_no", "epic_number", "voterid", "voter_epic"],
    "name": ["name", "voter_name", "full_name", "elector_name"],
    "age": ["age", "voter_age"],
    "gender": ["gender", "sex"],
    "booth_id": ["booth_id", "booth", "booth_no", "part_no", "ps_no", "polling_station"],
    "state": ["state", "state_code", "state_name"],
    "household_id": ["household_id", "hh_id", "family_id", "house_id", "house_no"],
    "community_group": ["community_group", "caste", "community", "caste_group", "social_group"],
    "scheme_beneficiary": ["scheme_beneficiary", "beneficiary", "is_beneficiary", "scheme_status"],
    "scheme_types": ["scheme_types", "scheme", "schemes", "scheme_type", "scheme_name"],
    "voting_history_index": ["voting_history_index", "voting_history", "vhi", "past_voting", "history_index"],
    "party_affinity": ["party_affinity", "affinity", "lean", "support_level", "survey_lean"],
    "is_migrant": ["is_migrant", "migrant", "absentee", "nri", "migrant_status"],
    "phone": ["phone", "mobile", "phone_no", "mobile_no", "contact"],
    "lat": ["lat", "latitude"],
    "lng": ["lng", "lon", "long", "longitude"],
    "address": ["address", "house_address", "locality"],
    # optional booth-level columns embedded in voter rows
    "booth_turnout": ["booth_turnout", "historical_turnout", "turnout_pct"],
    "constituency": ["constituency", "ac_name", "assembly_constituency"],
    "booth_name": ["booth_name", "ps_name", "polling_station_name"],
    "last_margin_pct": ["last_margin_pct", "margin", "margin_pct"],
    "consent_status": ["consent_status", "consent", "dpdp_consent", "consent_flag"],
}
_LOOKUP = {alias: canon for canon, al in ALIASES.items() for alias in al}
STATE_NAMES = {"uttar pradesh": "UP", "up": "UP", "punjab": "PB", "pb": "PB", "uttarakhand": "UK",
               "uk": "UK", "ut": "UK", "goa": "GA", "ga": "GA"}
AFFINITY_WORDS = {"strong supporter": 0.9, "supporter": 0.7, "lean": 0.35, "leaning": 0.35,
                  "neutral": 0.0, "undecided": 0.0, "lean opposition": -0.35, "opposition": -0.8}
TRUE = {"1", "y", "yes", "true", "t"}


def _key(col: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", col.strip().lower()).strip("_")


def _num(v, lo=None, hi=None):
    if v is None or str(v).strip() == "":
        return None
    x = float(str(v).strip().rstrip("%"))
    if lo is not None and not (lo <= x <= hi):
        raise ValueError(f"{x} outside [{lo}, {hi}]")
    return x


def normalize_voter(raw: dict, default_state: str | None = None) -> dict:
    """Map one raw record to the canonical voter dict. Raises ValueError."""
    rec, extras = {}, {}
    for col, val in raw.items():
        k = _key(col)
        if k in _LOOKUP:
            rec[_LOOKUP[k]] = val.strip() if isinstance(val, str) else val
        elif k == "attributes" and isinstance(val, dict):
            extras.update(val)
        elif val not in (None, ""):
            extras[k] = val

    for req in ("voter_id", "name", "age", "booth_id"):
        if rec.get(req) in (None, ""):
            raise ValueError(f"missing {req}")
    state = STATE_NAMES.get(str(rec.get("state") or default_state or "").strip().lower())
    if state not in STATES:
        raise ValueError(f"unknown state {rec.get('state') or default_state!r}")

    age = int(_num(rec["age"], 18, 120))
    g = str(rec.get("gender") or "O").strip().upper()[:1]
    gender = g if g in ("M", "F") else "O"

    schemes = re.split(r"[;,|/]", str(rec.get("scheme_types") or ""))
    schemes = [s.strip() for s in schemes if s.strip() and s.strip().lower() not in ("no", "none", "na")]
    ben = rec.get("scheme_beneficiary")
    beneficiary = int(str(ben).strip().lower() in TRUE) if ben not in (None, "") else int(bool(schemes))

    vhi = _num(rec.get("voting_history_index"), 0, 100)
    vhi = 0.5 if vhi is None else (vhi / 100 if vhi > 1 else vhi)

    aff_raw = rec.get("party_affinity")
    if aff_raw in (None, ""):
        affinity = 0.0  # unknown → neutral; refine as canvass data comes in
    elif str(aff_raw).strip().lower() in AFFINITY_WORDS:
        affinity = AFFINITY_WORDS[str(aff_raw).strip().lower()]
    else:
        affinity = _num(aff_raw, -1, 1)

    mig = rec.get("is_migrant")
    consent = normalize_consent(rec.get("consent_status"))  # None = column absent: keep existing status
    return {
        "voter_id": str(rec["voter_id"]).strip().upper(),
        "name": str(rec["name"]).strip(),
        "age": age, "gender": gender, "state": state,
        "booth_id": str(rec["booth_id"]).strip(),
        "household_id": str(rec.get("household_id") or "").strip(),
        "community_group": (rec.get("community_group") or "Unknown").strip() if isinstance(rec.get("community_group"), str) else "Unknown",
        "scheme_beneficiary": beneficiary, "scheme_types": ",".join(schemes),
        "voting_history_index": round(vhi, 3), "party_affinity": round(affinity, 3),
        "is_migrant": int(str(mig).strip().lower() in TRUE) if mig not in (None, "") else 0,
        "phone": str(rec.get("phone") or "").strip(),
        "lat": _num(rec.get("lat"), -90, 90), "lng": _num(rec.get("lng"), -180, 180),
        "address": rec.get("address") or "",
        "_booth": {k: rec.get(k) for k in ("booth_turnout", "constituency", "booth_name", "last_margin_pct")},
        "attributes": extras,
        "consent_status": consent,
    }


def parse_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def ingest_voters(conn, raw_records: list[dict], default_state=None, mode="append", source="csv",
                  actor: str | None = None) -> dict:
    """Validate, normalise and load voter records, auto-creating booths,
    households and one cadre per new booth. Returns an ingestion report."""
    good, errors = [], []
    for i, raw in enumerate(raw_records, 1):
        try:
            good.append(normalize_voter(raw, default_state))
        except (ValueError, TypeError) as e:
            errors.append({"row": i, "error": str(e), "voter_id": raw.get("voter_id") or raw.get("epic") or raw.get("EPIC")})

    existing_booths = {r["booth_id"]: dict(r) for r in conn.execute("SELECT * FROM booths")}
    if mode == "replace" and good:
        for st in {v["state"] for v in good}:
            conn.execute("DELETE FROM booths WHERE state = ?", (st,))  # cascades to everything
            conn.execute("DELETE FROM cadres WHERE state = ?", (st,))
            existing_booths = {k: b for k, b in existing_booths.items() if b["state"] != st}

    # booths: derive location from member coordinates when not supplied
    by_booth = defaultdict(list)
    for v in good:
        by_booth[v["booth_id"]].append(v)
    booths, cadres, still_good = [], [], []
    for bid, mem in by_booth.items():
        coords = [(m["lat"], m["lng"]) for m in mem if m["lat"] is not None and m["lng"] is not None]
        if bid in existing_booths:
            blat, blng = existing_booths[bid]["lat"], existing_booths[bid]["lng"]
        elif coords:
            blat, blng = sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords)
            meta = mem[0]["_booth"]
            booths.append({"booth_id": bid, "state": mem[0]["state"],
                           "constituency": meta.get("constituency") or "Unassigned",
                           "name": meta.get("booth_name") or f"Booth {bid}", "total_voters": 0,
                           "historical_turnout": _num(meta.get("booth_turnout"), 0, 100) or 60.0,
                           "last_margin_pct": _num(meta.get("last_margin_pct"), 0, 100),
                           "lat": blat, "lng": blng, "attributes": {}})
            cadres.append({"cadre_id": f"CAD-{bid}", "name": f"Cadre {bid}", "state": mem[0]["state"],
                           "phone": "", "booth_ids": bid})
        else:
            for m in mem:
                errors.append({"row": None, "voter_id": m["voter_id"],
                               "error": f"no coordinates and booth {bid} unknown"})
            continue
        for m in mem:
            if m["lat"] is None or m["lng"] is None:
                m["lat"], m["lng"] = blat, blng  # fall back to booth location
                m["attributes"]["geo_fallback"] = "booth"
            still_good.append(m)

    # households: explicit id, else booth+address, else one per voter
    households = {}
    for m in still_good:
        hid = m["household_id"] or (f"{m['booth_id']}-{_key(m['address'])}" if m["address"] else f"{m['booth_id']}-{m['voter_id']}")
        m["household_id"] = hid
        households.setdefault(hid, {"household_id": hid, "booth_id": m["booth_id"], "state": m["state"],
                                    "address": m["address"], "lat": m["lat"], "lng": m["lng"]})
    # consent audit: record every status the import sets or changes
    if still_good:
        ids = [m["voter_id"] for m in still_good if m["consent_status"]]
        old = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            old.update({r[0]: r[1] for r in conn.execute(
                f"SELECT voter_id, consent_status FROM voters WHERE voter_id IN ({','.join('?' * len(chunk))})", chunk)})
        ts = now_iso()
        for m in still_good:
            if m["consent_status"] and old.get(m["voter_id"]) != m["consent_status"]:
                log_consent(conn, m["voter_id"], old.get(m["voter_id"]), m["consent_status"], "import", actor or "system", ts)
                m["consent_updated_at"], m["consent_source"] = ts, "import"
    insert_records(conn, {"booths": booths, "households": list(households.values()),
                          "voters": [{**{k: v for k, v in m.items() if k not in ("_booth", "address")}, "source": source}
                                     for m in still_good],
                          "cadres": cadres})
    conn.execute("INSERT INTO ingestion_log (source, mode, accepted, rejected, created_at) VALUES (?,?,?,?,?)",
                 (source, mode, len(still_good), len(errors), now_iso()))
    conn.commit()
    return {"accepted": len(still_good), "rejected": len(errors), "booths_created": len(booths),
            "households": len(households), "errors": errors[:200]}


def insert_records(conn, data: dict) -> None:
    """Bulk upsert canonical records (used by both the generator and ingestion)."""
    j = lambda d: json.dumps(d or {})
    # Upserts (not OR REPLACE) so updating a booth/household never cascades away its children.
    conn.executemany("""INSERT INTO booths (booth_id, state, constituency, name, total_voters,
        historical_turnout, last_margin_pct, lat, lng, attributes) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(booth_id) DO UPDATE SET constituency=excluded.constituency, name=excluded.name,
        historical_turnout=excluded.historical_turnout, last_margin_pct=excluded.last_margin_pct,
        lat=excluded.lat, lng=excluded.lng, attributes=excluded.attributes""",
        [(b["booth_id"], b["state"], b["constituency"], b["name"], b["total_voters"], b["historical_turnout"],
          b.get("last_margin_pct"), b["lat"], b["lng"], j(b.get("attributes"))) for b in data.get("booths", [])])
    conn.executemany("""INSERT INTO households (household_id, booth_id, state, address, lat, lng)
        VALUES (?,?,?,?,?,?) ON CONFLICT(household_id) DO UPDATE SET address=excluded.address,
        lat=excluded.lat, lng=excluded.lng""", [(h["household_id"], h["booth_id"], h["state"], h.get("address"), h["lat"], h["lng"])
                                  for h in data.get("households", [])])
    # Upsert: consent is only overwritten when the incoming record carries a status.
    conn.executemany("""INSERT INTO voters (voter_id, household_id, booth_id, state, name, age, gender,
        community_group, scheme_beneficiary, scheme_types, voting_history_index, party_affinity, is_migrant,
        phone, lat, lng, attributes, source, consent_status, consent_updated_at, consent_source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,COALESCE(?, 'PENDING'),?,?)
        ON CONFLICT(voter_id) DO UPDATE SET household_id=excluded.household_id, booth_id=excluded.booth_id,
        state=excluded.state, name=excluded.name, age=excluded.age, gender=excluded.gender,
        community_group=excluded.community_group, scheme_beneficiary=excluded.scheme_beneficiary,
        scheme_types=excluded.scheme_types, voting_history_index=excluded.voting_history_index,
        party_affinity=excluded.party_affinity, is_migrant=excluded.is_migrant, phone=excluded.phone,
        lat=excluded.lat, lng=excluded.lng, attributes=excluded.attributes, source=excluded.source,
        consent_status=CASE WHEN ? IS NULL THEN voters.consent_status ELSE excluded.consent_status END,
        consent_updated_at=CASE WHEN ? IS NULL THEN voters.consent_updated_at ELSE excluded.consent_updated_at END,
        consent_source=CASE WHEN ? IS NULL THEN voters.consent_source ELSE excluded.consent_source END""",
        [(v["voter_id"], v["household_id"], v["booth_id"], v["state"], v["name"], v["age"], v["gender"],
          v.get("community_group"), v["scheme_beneficiary"], v.get("scheme_types", ""), v["voting_history_index"],
          v["party_affinity"], v["is_migrant"], v.get("phone"), v["lat"], v["lng"], j(v.get("attributes")),
          v.get("source", "mock"), v.get("consent_status"), v.get("consent_updated_at"), v.get("consent_source"),
          v.get("consent_status"), v.get("consent_status"), v.get("consent_status"))
         for v in data.get("voters", [])])
    conn.executemany("""INSERT INTO cadres (cadre_id, name, state, phone, booth_ids) VALUES (?,?,?,?,?)
                     ON CONFLICT(cadre_id) DO UPDATE SET booth_ids=excluded.booth_ids""",
                     [(c["cadre_id"], c["name"], c["state"], c.get("phone"), c["booth_ids"]) for c in data.get("cadres", [])])
