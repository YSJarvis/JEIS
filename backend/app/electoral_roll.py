"""Electoral roll import.

Takes roll exports — CSV, or JSON produced by a PDF roll parser — maps their
columns onto the voter schema, validates and de-duplicates them, and upserts
only the roll fields, so enrichment already held for a voter (schemes, survey
lean, phone, consent) is never overwritten by a roll refresh.

Flow:
  parse_upload()  -> raw records (+ part-level fields flattened in)
  suggest_mapping() -> {target: source column} with a confidence per field
  process(..., dry_run=True)  -> validation + de-duplication report (preview)
  process(..., dry_run=False) -> same report, records written

De-duplication:
  1. Inside the file: same EPIC -> later row dropped (conflicting names flagged).
     Rows without EPIC: same name + house number + booth -> later row dropped.
  2. Against the database: EPIC match -> update (or skip, per option).
     No EPIC match but same name + house + booth as an existing voter ->
     reported as a possible duplicate and not inserted; if the existing voter
     had no EPIC and the row has one, the record is linked to that EPIC.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata

from .config import STATES
from .db import now_iso
from .generator import constituency_code
from .ingestion import STATE_NAMES

MAX_ROWS = 200_000
EPIC_RE = re.compile(r"^[A-Z]{3}\d{7}$")
# "45", "45A", "Part 45", "Part No. 45", "PS-45", "भाग 45"
PART_RE = re.compile(r"(?:(?:part|ps|booth|polling\s*station|bhag|भाग|मतदान\s*केंद्र|no|number|संख्या)[\s.:#-]*)*"
                     r"(\d+)\s*([A-Za-z]?)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Target fields and header aliases (English, common parser output, Hindi)
# ---------------------------------------------------------------------------
TARGETS: dict[str, dict] = {
    "epic": {"label": "EPIC number", "required": False,
             "aliases": ["epic", "epicno", "epicnumber", "epicid", "voterid", "voteridno", "voteridnumber",
                         "idcardno", "electorid", "एपिक", "एपिकसंख्या", "पहचानपत्रसंख्या", "मतदातापहचानपत्रसंख्या"]},
    "name": {"label": "Voter name", "required": True,
             "aliases": ["name", "votername", "electorname", "fullname", "electorsname", "नाम", "निर्वाचककानाम",
                         "मतदाताकानाम", "मतदातानाम"]},
    "relative_name": {"label": "Relative name", "required": False,
                      "aliases": ["relativename", "relationname", "relativesname", "fathername", "fathersname",
                                  "husbandname", "husbandsname", "fatherhusbandname", "fatherorhusbandname",
                                  "fatherhusbandsname", "guardianname", "पिताकानाम", "पतिकानाम", "पितापतिकानाम",
                                  "संबंधीकानाम", "सम्बन्धीकानाम"]},
    "relation_type": {"label": "Relation type", "required": False,
                      "aliases": ["relationtype", "relation", "rlntype", "relntype", "संबंध", "सम्बन्ध"]},
    "age": {"label": "Age", "required": True, "aliases": ["age", "ageyears", "उम्र", "आयु"]},
    "gender": {"label": "Gender", "required": False, "aliases": ["gender", "sex", "लिंग"]},
    "house_no": {"label": "House number", "required": False,
                 "aliases": ["houseno", "housenumber", "hno", "hnumber", "houseaddress", "doorno", "मकानसंख्या",
                             "मकाननंबर", "मकाननं", "गृहसंख्या", "मकाननम्बर"]},
    "booth": {"label": "Polling station / booth no.", "required": True,
              "aliases": ["booth", "boothno", "boothnumber", "boothid", "partno", "partnumber", "part", "psno",
                          "pollingstation", "pollingstationno", "pollingstationnumber", "pollingstationid",
                          "भागसंख्या", "भागसं", "मतदानकेंद्रसंख्या", "मतदानकेन्द्रसंख्या"]},
    "serial_no": {"label": "Serial no. in part", "required": False,
                  "aliases": ["serialno", "slno", "srno", "serial", "serialnumber", "क्रमसंख्या", "क्रमांक", "क्रसं"]},
    "section": {"label": "Section", "required": False,
                "aliases": ["section", "sectionno", "sectionname", "अनुभाग", "अनुभागसंख्या"]},
    "constituency": {"label": "Assembly constituency", "required": False,
                     "aliases": ["acname", "constituency", "assemblyconstituency", "assemblyconstituencyname",
                                 "विधानसभाक्षेत्र", "विधानसभा"]},
    "state": {"label": "State", "required": False, "aliases": ["state", "statename", "statecode", "राज्य"]},
}

GENDERS = {"m": "M", "male": "M", "पुरुष": "M", "पु": "M", "f": "F", "female": "F", "महिला": "F", "स्त्री": "F",
           "t": "O", "tg": "O", "o": "O", "other": "O", "thirdgender": "O", "तृतीयलिंग": "O", "अन्य": "O"}
RELATIONS = {"f": "Father", "father": "Father", "पिता": "Father", "h": "Husband", "husband": "Husband",
             "पति": "Husband", "m": "Mother", "mother": "Mother", "माता": "Mother", "w": "Wife", "wife": "Wife",
             "पत्नी": "Wife", "o": "Other", "other": "Other", "अन्य": "Other"}
HONORIFICS = {"shri", "sri", "smt", "shrimati", "kumari", "kum", "mr", "mrs", "ms", "dr", "late",
              "श्री", "श्रीमती", "कुमारी", "कु", "स्व"}


def norm_header(s: str) -> str:
    return re.sub(r"[\s.\-_/()#:,'\"]+", "", str(s).strip().lower())


def ascii_digits(s: str) -> str:
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() else ch for ch in s)


def name_key(name: str) -> str:
    words = re.sub(r"[.,'\"\-]+", " ", str(name).lower()).split()
    return " ".join(w for w in words if w not in HONORIFICS)


def house_key(h) -> str:
    return re.sub(r"[\s.,#]+", "", ascii_digits(str(h or "")).lower()).removeprefix("hno")


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_digits(str(s))).strip("-").upper()[:24] or "X"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _flatten_json(obj) -> list[dict]:
    """Accepts a list of electors, {"electors"|"voters"|"records": [...]}, or
    PDF-parser output with part-level fields: {"part_no", "ac_name", "state",
    "electors": [...]} or {"parts": [ ...those... ]}. Part fields are copied
    onto each elector that lacks them."""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) and any(k in x for k in ("electors", "voters", "records")) for x in obj):
            return [r for part in obj for r in _flatten_json(part)]
        return [x for x in obj if isinstance(x, dict)]
    if not isinstance(obj, dict):
        raise ValueError("JSON must be a list of electors or an object containing them")
    if isinstance(obj.get("parts"), list):
        inherited = {k: v for k, v in obj.items() if k != "parts" and not isinstance(v, (list, dict))}
        return [{**inherited, **r} for part in obj["parts"] for r in _flatten_json({**inherited, **part})]
    for key in ("electors", "voters", "records", "rows", "data"):
        if isinstance(obj.get(key), list):
            part = {k: v for k, v in obj.items() if not isinstance(v, (list, dict))}
            return [{**part, **r} for r in obj[key] if isinstance(r, dict)]
    raise ValueError("no list of electors found (expected 'electors', 'voters', 'records' or 'parts')")


def parse_upload(data: bytes, filename: str = "") -> tuple[str, list[dict]]:
    text = _decode(data)
    stripped = text.lstrip()
    if filename.lower().endswith(".json") or stripped[:1] in ("[", "{"):
        try:
            rows = _flatten_json(json.loads(stripped))
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON: {e.msg} at line {e.lineno}")
        fmt = "json"
    elif filename.lower().endswith(".pdf") or data[:4] == b"%PDF":
        raise ValueError("PDF rolls must be converted first: upload the CSV or JSON your roll parser produces")
    else:
        sample = text[:20000]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [r for r in csv.DictReader(io.StringIO(text), dialect=dialect)
                if any(str(v or "").strip() for v in r.values())]
        fmt = "csv"
    if not rows:
        raise ValueError("the file has no rows")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"the file has {len(rows):,} rows; split it into files of at most {MAX_ROWS:,}")
    # stringify keys/values; drop None keys from ragged CSV rows
    clean = [{str(k).strip(): ("" if v is None else v) for k, v in r.items() if k is not None} for r in rows]
    return fmt, clean


def columns_of(rows: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for r in rows[:500]:
        for k in r:
            seen.setdefault(k, None)
    return list(seen)


# ---------------------------------------------------------------------------
# Mapping suggestion
# ---------------------------------------------------------------------------

def _value_sniff(values: list[str]) -> dict[str, float]:
    vals = [str(v).strip() for v in values if str(v).strip()]
    if not vals:
        return {}
    n = len(vals)
    out = {}
    epic = sum(bool(EPIC_RE.match(v.replace(" ", "").upper())) for v in vals) / n
    if epic >= 0.6:
        out["epic"] = epic
    gender = sum(norm_header(v) in GENDERS for v in vals) / n
    if gender >= 0.8:
        out["gender"] = gender
    ages = 0
    for v in vals:
        d = re.findall(r"\d+", v)
        ages += bool(d) and len(d) == 1 and 18 <= int(d[0]) <= 120 and len(v) <= 8
    if ages / n >= 0.9:
        out["age"] = ages / n * 0.6   # numbers are ambiguous: weaker than a header match
    return out


def suggest_mapping(rows: list[dict]) -> tuple[dict[str, str | None], dict[str, str]]:
    cols = columns_of(rows)
    normed = {c: norm_header(c) for c in cols}
    candidates: list[tuple[float, str, str]] = []   # (score, target, column)
    for t, meta in TARGETS.items():
        aliases = set(meta["aliases"])
        for c, n in normed.items():
            if n in aliases:
                candidates.append((1.0, t, c))
            elif len(n) >= 4 and any(len(a) >= 4 and (a in n or n in a) for a in aliases):
                candidates.append((0.7, t, c))
    for c in cols:
        for t, score in _value_sniff([r.get(c, "") for r in rows[:200]]).items():
            candidates.append((min(score, 0.65), t, c))
    mapping: dict[str, str | None] = {t: None for t in TARGETS}
    confidence: dict[str, str] = {}
    used: set[str] = set()
    for score, t, c in sorted(candidates, key=lambda x: (-x[0], list(TARGETS).index(x[1]))):
        if mapping[t] is None and c not in used:
            mapping[t], confidence[t] = c, "high" if score >= 1 else "medium" if score >= 0.65 else "low"
            used.add(c)
    return mapping, confidence


# ---------------------------------------------------------------------------
# Validation + de-duplication + write
# ---------------------------------------------------------------------------

class _Booths:
    """Resolves roll booth/part values to booth ids, creating booths on commit."""

    def __init__(self, conn, options: dict):
        self.conn = conn
        self.existing = {r["booth_id"]: dict(r) for r in conn.execute("SELECT booth_id, state, constituency, lat, lng FROM booths")}
        self.new: dict[str, dict] = {}
        self.default_constituency = (options.get("constituency") or "").strip() or None

    def resolve(self, raw: str, state: str, constituency: str | None) -> tuple[str, bool]:
        v = ascii_digits(str(raw).strip())
        if v.upper() in self.existing or v.upper() in self.new:
            return v.upper(), False
        const = constituency or self.default_constituency
        m = PART_RE.fullmatch(v)
        if m:
            if not const:
                raise ValueError(f"booth '{raw}' looks like a part number: choose the constituency it belongs to")
            part = int(m.group(1))
            booth_id = f"{state}-{constituency_code(const)}-{part:03d}{m.group(2).upper()}"
            name = f"Part {part}{m.group(2).upper()}"
        else:
            booth_id, name = slug(v), f"Booth {v}"
        if booth_id in self.existing:
            if self.existing[booth_id]["state"] != state:
                raise ValueError(f"booth {booth_id} belongs to another state")
            return booth_id, False
        if booth_id not in self.new:
            lat, lng, how = self._locate(state, const)
            self.new[booth_id] = {"booth_id": booth_id, "state": state, "constituency": const or "Unassigned",
                                  "name": name, "lat": lat, "lng": lng,
                                  "geo_fallback": how}
        return booth_id, True

    def _locate(self, state, const):
        """New booths have no coordinates in a roll: place them at the
        constituency (or state) centre and flag them for geocoding."""
        if const:
            r = self.conn.execute("SELECT AVG(lat) a, AVG(lng) b FROM booths WHERE state = ? AND lower(constituency) = lower(?)",
                                  (state, const)).fetchone()
            if r["a"] is not None:
                return r["a"], r["b"], "constituency_centre"
            for name, (lat, lng) in STATES[state]["constituencies"].items():
                if name.lower() == const.lower():
                    return lat, lng, "constituency_centre"
        lat, lng = STATES[state]["center"]
        return lat, lng, "state_centre"

    def info(self, booth_id):
        return self.existing.get(booth_id) or self.new[booth_id]


def _val(row: dict, mapping: dict, target: str) -> str:
    col = mapping.get(target)
    if not col:
        return ""
    v = row.get(col, "")
    return str(v).strip() if v is not None else ""


def _surrogate_id(state, booth_id, house, name, relative) -> str:
    h = hashlib.sha1(f"{state}|{booth_id}|{house}|{name}|{relative}".encode()).hexdigest()[:10].upper()
    return f"NOEPIC-{h}"


def process(conn, rows: list[dict], mapping: dict, options: dict, dry_run: bool, actor: str | None = None,
            filename: str = "") -> dict:
    missing = [TARGETS[t]["label"] for t, m in TARGETS.items() if m["required"] and not mapping.get(t)]
    if missing:
        raise ValueError(f"map these fields first: {', '.join(missing)}")
    if not mapping.get("epic") and not mapping.get("house_no"):
        raise ValueError("map EPIC number or House number: one of them is needed to tell voters apart")
    unknown = {c for c in mapping.values() if c} - set(columns_of(rows))
    if unknown:
        raise ValueError(f"mapped columns not in the file: {sorted(unknown)}")
    opt_state = STATE_NAMES.get(str(options.get("state") or "").strip().lower())
    on_existing = options.get("on_existing", "update")
    if on_existing not in ("update", "skip"):
        raise ValueError("on_existing must be 'update' or 'skip'")

    booths = _Booths(conn, options)
    issues: list[dict] = []
    stats = dict(rows=len(rows), valid=0, invalid=0, duplicates_in_file=0, created=0, updated=0, unchanged=0,
                 skipped_existing=0, possible_duplicates=0, linked_epic=0, nonstandard_epic=0,
                 booths_created=0, households_created=0, needs_geocoding=0)

    def issue(row_no, kind, msg, rec=None):
        if len(issues) < 500:
            issues.append({"row": row_no, "kind": kind, "message": msg,
                           "epic": (rec or {}).get("epic") or None, "name": (rec or {}).get("name") or None})

    # ---- 1. validate + normalise each row ----
    recs = []
    for i, row in enumerate(rows, start=2 if filename.lower().endswith(".csv") else 1):
        rec = {"epic": re.sub(r"[\s]+", "", ascii_digits(_val(row, mapping, "epic"))).upper(),
               "name": re.sub(r"\s+", " ", _val(row, mapping, "name")),
               "relative_name": re.sub(r"\s+", " ", _val(row, mapping, "relative_name")),
               "house_no": _val(row, mapping, "house_no"), "serial_no": _val(row, mapping, "serial_no"),
               "section": _val(row, mapping, "section")}
        try:
            if not rec["name"]:
                raise ValueError("name is empty")
            ages = re.findall(r"\d+", _val(row, mapping, "age"))
            if not ages:
                raise ValueError("age is missing")
            rec["age"] = int(ages[0])
            if not 18 <= rec["age"] <= 120:
                raise ValueError(f"age {rec['age']} is outside 18–120")
            g = _val(row, mapping, "gender")
            rec["gender"] = GENDERS.get(norm_header(g), "O")
            if g and norm_header(g) not in GENDERS:
                issue(i, "warning", f"gender '{g}' not recognised; recorded as other", rec)
            rel = norm_header(_val(row, mapping, "relation_type"))
            rec["relation_type"] = RELATIONS.get(rel) if rel else None
            state = STATE_NAMES.get(_val(row, mapping, "state").lower()) or opt_state
            if not state:
                raise ValueError("state unknown: choose a state for this file")
            rec["state"] = state
            raw_booth = _val(row, mapping, "booth")
            if not raw_booth:
                raise ValueError("booth / part number is empty")
            rec["booth_id"], _ = booths.resolve(raw_booth, state, _val(row, mapping, "constituency") or None)
            if rec["epic"] and not EPIC_RE.match(rec["epic"]):
                stats["nonstandard_epic"] += 1
                issue(i, "warning", f"EPIC '{rec['epic']}' is not in the standard ABC1234567 format; kept as given", rec)
            rec["voter_id"] = rec["epic"] or _surrogate_id(state, rec["booth_id"], house_key(rec["house_no"]),
                                                           name_key(rec["name"]), name_key(rec["relative_name"]))
            rec["row"] = i
            recs.append(rec)
        except ValueError as e:
            stats["invalid"] += 1
            issue(i, "error", str(e), rec)

    # ---- 2. de-duplicate inside the file ----
    by_epic: dict[str, dict] = {}
    by_key: dict[tuple, dict] = {}
    unique = []
    for r in recs:
        if r["epic"]:
            first = by_epic.get(r["epic"])
            if first:
                stats["duplicates_in_file"] += 1
                same = name_key(first["name"]) == name_key(r["name"])
                issue(r["row"], "duplicate", f"EPIC repeated from row {first['row']}" +
                      ("" if same else f" with a different name ('{first['name']}'): check the roll"), r)
                continue
            by_epic[r["epic"]] = r
        key = (name_key(r["name"]), house_key(r["house_no"]), r["booth_id"]) if r["house_no"] else None
        if key:
            first = by_key.get(key)
            if first and (not first["relative_name"] or not r["relative_name"]
                          or name_key(first["relative_name"]) == name_key(r["relative_name"])):
                if not (first["epic"] and r["epic"] and first["epic"] != r["epic"]):
                    stats["duplicates_in_file"] += 1
                    issue(r["row"], "duplicate", f"same name, house number and booth as row {first['row']}", r)
                    continue
            by_key.setdefault(key, r)
        unique.append(r)
    stats["valid"] = len(unique)

    # ---- 3. match against the database ----
    existing_ids: dict[str, dict] = {}
    ids = [r["voter_id"] for r in unique]
    for k in range(0, len(ids), 900):
        chunk = ids[k:k + 900]
        for row in conn.execute(f"""SELECT voter_id, name, age, gender, booth_id, household_id, attributes FROM voters
                                    WHERE voter_id IN ({','.join('?' * len(chunk))})""", chunk):
            existing_ids[row["voter_id"]] = dict(row)
    booth_set = sorted({r["booth_id"] for r in unique})
    existing_keys: dict[tuple, dict] = {}
    for k in range(0, len(booth_set), 900):
        chunk = booth_set[k:k + 900]
        for row in conn.execute(f"""SELECT v.voter_id, v.name, v.booth_id, v.attributes, h.address
                                    FROM voters v JOIN households h ON h.household_id = v.household_id
                                    WHERE v.booth_id IN ({','.join('?' * len(chunk))})""", chunk):
            attrs = json.loads(row["attributes"] or "{}")
            hk = house_key(attrs.get("house_no") or "")
            if hk:
                existing_keys[(name_key(row["name"]), hk, row["booth_id"])] = {**dict(row), "attrs": attrs}

    plan_new, plan_update, plan_link = [], [], []
    for r in unique:
        if r["voter_id"] in existing_ids:
            if on_existing == "skip":
                stats["skipped_existing"] += 1
                continue
            ex = existing_ids[r["voter_id"]]
            exattrs = json.loads(ex["attributes"] or "{}")
            changed = (ex["name"], ex["age"], ex["gender"], ex["booth_id"], house_key(exattrs.get("house_no") or "")) != \
                      (r["name"], r["age"], r["gender"], r["booth_id"], house_key(r["house_no"]))
            if changed:
                plan_update.append(r)
            else:
                stats["unchanged"] += 1
            continue
        key = (name_key(r["name"]), house_key(r["house_no"]), r["booth_id"]) if r["house_no"] else None
        twin = existing_keys.get(key) if key else None
        if twin:
            rel_ok = not twin["attrs"].get("relative_name") or not r["relative_name"] or \
                name_key(twin["attrs"]["relative_name"]) == name_key(r["relative_name"])
            if rel_ok:
                if r["epic"] and twin["voter_id"].startswith("NOEPIC-"):
                    plan_link.append((twin["voter_id"], r))
                else:
                    stats["possible_duplicates"] += 1
                    issue(r["row"], "duplicate", f"matches existing voter {twin['voter_id']} (same name, house and booth); not added", r)
                continue
        plan_new.append(r)
    stats["created"], stats["updated"], stats["linked_epic"] = len(plan_new), len(plan_update), len(plan_link)
    stats["booths_created"] = len({r["booth_id"] for r in plan_new + plan_update + [x[1] for x in plan_link]} & set(booths.new))
    stats["needs_geocoding"] = stats["booths_created"]

    affected_states = sorted({r["state"] for r in plan_new + plan_update + [x[1] for x in plan_link]})
    report = {"dry_run": dry_run, "stats": stats, "issues": issues, "states": affected_states,
              "new_booths": [b for b in booths.new.values() if b["booth_id"] in
                             {r["booth_id"] for r in plan_new + plan_update + [x[1] for x in plan_link]}][:100]}
    if dry_run:
        return report

    # ---- 4. write ----
    ts = now_iso()
    used_booths = {r["booth_id"] for r in plan_new + plan_update + [x[1] for x in plan_link]}
    for b in booths.new.values():
        if b["booth_id"] in used_booths:
            conn.execute("""INSERT INTO booths (booth_id, state, constituency, name, total_voters, historical_turnout,
                            last_margin_pct, lat, lng, attributes) VALUES (?,?,?,?,0,?,NULL,?,?,?)""",
                         (b["booth_id"], b["state"], b["constituency"], b["name"],
                          float(options.get("default_turnout") or 60), b["lat"], b["lng"],
                          json.dumps({"geo_fallback": b["geo_fallback"], "source": "roll"})))
            conn.execute("INSERT OR IGNORE INTO cadres (cadre_id, name, state, phone, booth_ids) VALUES (?,?,?,?,?)",
                         (f"CAD-{b['booth_id']}", f"Cadre {b['booth_id']}", b["state"], "", b["booth_id"]))

    def household(r) -> str:
        hid = f"{r['booth_id']}-H-{slug(r['house_no'])}" if r["house_no"] else f"{r['booth_id']}-V-{slug(r['voter_id'])}"
        if not conn.execute("SELECT 1 FROM households WHERE household_id = ?", (hid,)).fetchone():
            b = booths.info(r["booth_id"])
            addr = f"H.No. {r['house_no']}, Part {r['booth_id'].rsplit('-', 1)[-1]}" if r["house_no"] else ""
            conn.execute("INSERT INTO households (household_id, booth_id, state, address, lat, lng) VALUES (?,?,?,?,?,?)",
                         (hid, r["booth_id"], r["state"], addr, b["lat"], b["lng"]))
            stats["households_created"] += 1
        return hid

    def roll_attrs(r, base: dict) -> str:
        a = {**base, "relative_name": r["relative_name"] or None, "relation_type": r["relation_type"],
             "house_no": r["house_no"] or None, "serial_no": r["serial_no"] or None, "section": r["section"] or None,
             "roll_imported_at": ts, "roll_file": filename[:80] or None}
        if r["booth_id"] in booths.new:
            a["geo_fallback"] = booths.new[r["booth_id"]]["geo_fallback"]
        return json.dumps({k: v for k, v in a.items() if v is not None})

    def coords(hid):
        h = conn.execute("SELECT lat, lng FROM households WHERE household_id = ?", (hid,)).fetchone()
        return h["lat"], h["lng"]

    for r in plan_new:
        hid = household(r)
        lat, lng = coords(hid)
        conn.execute("""INSERT INTO voters (voter_id, household_id, booth_id, state, name, age, gender, community_group,
                        scheme_beneficiary, scheme_types, voting_history_index, party_affinity, is_migrant, phone,
                        lat, lng, attributes, source, consent_status)
                        VALUES (?,?,?,?,?,?,?,'Unknown',0,'',0.5,0,0,NULL,?,?,?,'roll','PENDING')""",
                     (r["voter_id"], hid, r["booth_id"], r["state"], r["name"], r["age"], r["gender"], lat, lng,
                      roll_attrs(r, {})))
    for r in plan_update:
        hid = household(r)
        lat, lng = coords(hid)
        ex = existing_ids[r["voter_id"]]
        base = json.loads(ex["attributes"] or "{}")
        # roll fields only: schemes, survey lean, phone and consent are left as they are
        conn.execute("""UPDATE voters SET name=?, age=?, gender=?, booth_id=?, state=?, household_id=?, lat=?, lng=?,
                        attributes=? WHERE voter_id=?""",
                     (r["name"], r["age"], r["gender"], r["booth_id"], r["state"], hid, lat, lng,
                      roll_attrs(r, base), r["voter_id"]))
    for old_id, r in plan_link:
        if conn.execute("SELECT 1 FROM voters WHERE voter_id = ?", (r["epic"],)).fetchone():
            continue
        conn.execute("DELETE FROM voter_scores WHERE voter_id = ?", (old_id,))
        base = json.loads(conn.execute("SELECT attributes FROM voters WHERE voter_id = ?", (old_id,)).fetchone()[0] or "{}")
        conn.execute("UPDATE voters SET voter_id = ?, name = ?, age = ?, gender = ?, attributes = ? WHERE voter_id = ?",
                     (r["epic"], r["name"], r["age"], r["gender"], roll_attrs(r, base), old_id))
        conn.execute("UPDATE consent_log SET voter_id = ? WHERE voter_id = ?", (r["epic"], old_id))

    conn.execute("INSERT INTO ingestion_log (source, mode, accepted, rejected, created_at) VALUES (?,?,?,?,?)",
                 ("electoral_roll", on_existing, stats["created"] + stats["updated"] + stats["linked_epic"],
                  stats["invalid"] + stats["duplicates_in_file"] + stats["possible_duplicates"], ts))
    conn.commit()
    return report
