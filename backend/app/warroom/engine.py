"""CounterPulse AI engine: media ingestion, sentiment, narrative clustering, threat scoring.

Clustering is incremental, so every new item is placed as it arrives:
  1. same state, and the same recognised issue (e.g. "Delayed cane payments") -> join that cluster
  2. otherwise cosine similarity on TF-IDF term weights (unigrams + bigrams) against
     each recent cluster centroid; >= JOIN_THRESHOLD joins, else a new cluster starts
`recluster()` rebuilds everything from scratch (after tuning thresholds).

Threat score (1-100) is a weighted blend, explained in `breakdown`:
  volume      recency-weighted item count (24 h half-life)
  velocity    last 24 h against the previous 48 h
  negativity  recency-weighted lexicon negativity
  reach       source type (TV > YouTube > print > social) and reported views/shares
  spread      distinct channels and constituencies
then scaled by state significance (assembly size, damped so small states still register).
Sentiment is a transparent English/Hindi/Punjabi lexicon; a classifier can replace
`score_sentiment` without touching the rest.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..config import STATES
from ..copilot import kb
from ..copilot.generate import ISSUES
from ..db import now_iso
from ..generator import constituency_code

SOURCE_TYPES = {"social": "Social", "print": "Local print", "tv": "TV", "youtube": "YouTube",
                "survey": "Survey report", "other": "Other"}
SOURCE_WEIGHT = {"tv": 1.0, "youtube": 0.8, "print": 0.7, "social": 0.55, "survey": 0.5, "other": 0.4}
ASSEMBLY_SEATS = {"UP": 403, "PB": 117, "UK": 70, "GA": 40}
STATE_SIGNIFICANCE = {s: round(0.5 + 0.5 * math.sqrt(n / 403), 2) for s, n in ASSEMBLY_SEATS.items()}
JOIN_THRESHOLD = 0.25
ACTIVE_DAYS = 14
SEVERITY = [(70, "Critical"), (50, "High"), (30, "Medium"), (0, "Low")]

NEG_WORDS = set("""delay delayed delays pending unpaid dues protest protests protested protesting anger angry outrage fail failed
failure failures broken neglect neglected crisis shortage shortages scam betrayal betrayed suffer suffering stalled lapsed
blocked blockade dharna strike unemployment jobless collapse collapsed corruption corrupt loot shame worst poor complaint
complaints demand demands waterlogging waterlogged flooded overflow overflowing cut cuts outage outages leak leaked cancelled
harassment unsafe dead death deaths died stranded slow ignored empty denied rot rotting distress
बकाया देरी विरोध प्रदर्शन नाराज़ नाराज गुस्सा नाकाम विफल टूटी बदहाल संकट घोटाला धरना बेरोजगारी जलभराव लापरवाही अधूरा
अधूरी परेशान शिकायत किल्लत
ਵਿਰੋਧ ਧਰਨਾ ਦੇਰੀ ਨਾਰਾਜ਼ ਬਕਾਇਆ ਪਰੇਸ਼ਾਨ""".split())
POS_WORDS = set("""inaugurated completed cleared paid relief welcomed launched improved resolved restored praised benefit
benefits opened success successful thanked approved sanctioned delivered
उद्घाटन राहत भुगतान पूरा पूरी स्वागत ਭੁਗਤਾਨ ਰਾਹਤ""".split())
PROTEST = re.compile(r"\b(protest\w*|dharna|blockade|blocked the|march(?:ed)? to|gherao|strike)\b|धरना|प्रदर्शन|विरोध|ਧਰਨਾ", re.I)
CLAIM_VERBS = re.compile(r"\b(says?|said|alleg\w*|claim\w*|accus\w*|blam\w*|fail\w*|not|no|never|only)\b|आरोप|कहा", re.I)
EXTRA_ISSUES = [  # Hindi / media phrasings on top of the Copilot issue list
    (r"गन्ना|गन्ने|चीनी मिल", "Delayed cane payments", "Agriculture"),
    (r"जलभराव|नाली", "Drainage and waterlogging", "Infrastructure"),
    (r"सड़क|road repair|potholes?", "Unfinished road works", "Infrastructure"),
    (r"power cuts?|outages?|बिजली कटौती", "Power cuts", "Infrastructure"),
    (r"paper leak|exam (?:cancel|postpon)", "Exam disruption", "Youth"),
    (r"पानी की किल्लत|water cuts?|tanker", "Irregular water supply", "Infrastructure"),
]
STOP = set("said says residents today news video post people village district reported report local area also over "
           "would could their there sample week day days".split())
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


# ---------------------------------------------------------------------------
# text features
# ---------------------------------------------------------------------------

def scrub(text: str) -> str:
    """Remove phone numbers and email addresses (DPDP data minimisation)."""
    return EMAIL.sub("[email removed]", PHONE.sub("[phone removed]", text))


def score_sentiment(text: str) -> tuple[float, str]:
    toks = kb.tokens(text)
    neg = sum(1 for t in toks if t in NEG_WORDS or (len(t) > 4 and t.endswith("s") and t[:-1] in NEG_WORDS))
    pos = sum(1 for t in toks if t in POS_WORDS)
    negativity = round(min(1.0, neg / (neg + pos + 1.5)), 3)
    label = "negative" if negativity >= 0.4 else ("positive" if pos > neg else "neutral")
    return negativity, label


def find_issue(text: str) -> str | None:
    low = text.lower()
    for pat, title, _ in ISSUES + EXTRA_ISSUES:      # specific Copilot patterns first
        if re.search(pat, low):
            return title
    return None


def extract_claim(text: str) -> str:
    sents = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text) if len(s.strip()) > 15] or [text.strip()]
    best = max(sents, key=lambda s: sum(t in NEG_WORDS for t in kb.tokens(s)) * 2 + len(CLAIM_VERBS.findall(s))
               + (1 if re.search(r"\d", s) else 0))
    return best[:260]


def term_vector(text: str) -> dict[str, float]:
    toks = [t for t in kb.tokens(text) if t not in STOP and not t.isdigit()]
    grams = toks + [f"{a} {b}" for a, b in zip(toks, toks[1:])]
    tf = Counter(grams)
    return {t: 1 + math.log(c) for t, c in tf.items()}


def _cosine(a: dict, b: dict, idf: dict) -> float:
    if not a or not b:
        return 0.0
    dot = sum(w * b.get(t, 0) * idf.get(t, 1) ** 2 for t, w in a.items())
    na = math.sqrt(sum((w * idf.get(t, 1)) ** 2 for t, w in a.items()))
    nb = math.sqrt(sum((w * idf.get(t, 1)) ** 2 for t, w in b.items()))
    return dot / (na * nb) if na and nb else 0.0


def _idf(conn, state) -> dict[str, float]:
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT tf FROM media_items WHERE (state IS ? OR state = ?) AND published_at >= ?",
                        (state, state, since)).fetchall()
    n = len(rows) + 1
    df = Counter(t for r in rows for t in json.loads(r["tf"]))
    return {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}


# ---------------------------------------------------------------------------
# ingestion + clustering
# ---------------------------------------------------------------------------

def _ts(value) -> str:
    if value in (None, ""):
        return now_iso()
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), tz=timezone.utc)
    else:
        v = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            raise ValueError(f"timestamp '{value}' is not ISO 8601") from None
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))   # bare times are IST
    return min(dt.astimezone(timezone.utc), datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _place(conn, item: dict) -> tuple[str | None, str | None, str | None]:
    state = (item.get("state") or "").strip().upper() or None
    if state and state not in STATES:
        names = {v["name"].lower(): k for k, v in STATES.items()}
        state = names.get(state.lower())
        if not state:
            raise ValueError(f"unknown state {item.get('state')}")
    cid, const = item.get("constituency_id"), item.get("constituency")
    consts = kb.known_constituencies(conn)
    if cid:
        c = next((c for c in consts if c["id"].lower() == str(cid).lower()), None)
        if not c:
            raise ValueError(f"unknown constituency id {cid}")
        return c["state"], c["name"], c["id"]
    if const:
        c = next((c for c in consts if c["name"].lower() == str(const).lower() and (not state or c["state"] == state)), None)
        if c:
            return c["state"], c["name"], c["id"]
        if state:
            return state, const, f"{state}-{constituency_code(const)}"
        raise ValueError(f"unknown constituency {const}: give its state too")
    if not state:  # try the text itself
        det = kb.detect_metadata(conn, "", item.get("text", ""))
        return det["state"], det["constituency"], det["constituency_id"]
    return state, None, None


def ingest_item(conn, item: dict, actor: str, enforce: dict | None = None) -> dict:
    text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
    if len(text) < 20:
        raise ValueError("text is too short (20+ characters)")
    if len(text) > 5000:
        text = text[:5000]
    text = scrub(text)
    source = str(item.get("source_type") or "other").lower().replace(" ", "_")
    source = {"local_print": "print", "newspaper": "print", "television": "tv", "yt": "youtube"}.get(source, source)
    if source not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of {list(SOURCE_TYPES)}")
    channel = str(item.get("channel") or item.get("channel_name") or "").strip()[:120]
    if not channel:
        raise ValueError("channel is required (outlet, page or programme name)")
    state, const, cid = _place(conn, item)
    if enforce:
        if (state and state != enforce["state"]) or (cid and cid != enforce["constituency_id"]):
            raise ValueError("you can add items for your own constituency only")
        if not cid:
            state, const, cid = enforce["state"], enforce["constituency"], enforce["constituency_id"]
    published = _ts(item.get("timestamp") or item.get("published_at"))
    sha = hashlib.sha256(f"{channel.lower()}|{text.lower()}".encode()).hexdigest()
    dup = conn.execute("SELECT item_id, cluster_id FROM media_items WHERE sha256 = ?", (sha,)).fetchone()
    if dup:
        return {"status": "duplicate", "item_id": dup["item_id"], "cluster_id": dup["cluster_id"]}
    negativity, label = score_sentiment(text)
    issue = find_issue(text)
    vec = term_vector(text)
    reach = item.get("reach")
    try:
        reach = float(reach) if reach not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("reach must be a number (views, shares or circulation)") from None
    item_id = f"m-{uuid.uuid4().hex[:10]}"
    lang = "hi" if re.search(r"[ऀ-ॿ]", text) else "pa" if re.search(r"[਀-੿]", text) else "en"
    conn.execute("""INSERT INTO media_items (item_id, state, constituency, constituency_id, source_type, channel, url,
                    published_at, ingested_at, text, language, negativity, sentiment_label, reach, claim, issue, tf, sha256,
                    ingested_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (item_id, state, const, cid, source, channel, (item.get("url") or None), published, now_iso(), text, lang,
                  negativity, label, reach, extract_claim(text), issue, json.dumps(vec), sha, actor))
    cluster_id, created = assign(conn, item_id, state, issue, vec, published)
    row = conn.execute("SELECT label FROM narrative_clusters WHERE cluster_id = ?", (cluster_id,)).fetchone()
    return {"status": "ingested", "item_id": item_id, "state": state, "constituency": const, "constituency_id": cid,
            "source_type": source, "negativity": negativity, "sentiment": label, "issue": issue,
            "cluster_id": cluster_id, "cluster_label": row["label"], "new_cluster": created}


def assign(conn, item_id, state, issue, vec, published) -> tuple[str, bool]:
    since = (datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS)).isoformat(timespec="seconds")
    cands = [dict(r) for r in conn.execute("""SELECT cluster_id, issue, centroid FROM narrative_clusters
        WHERE (state IS ? OR state = ?) AND last_seen >= ?""", (state, state, since))]
    best, best_sim = None, 0.0
    if cands:
        idf = _idf(conn, state)
        for c in cands:
            sim = _cosine(vec, json.loads(c["centroid"]), idf)
            if issue and c["issue"] == issue:
                sim += 1.0      # a shared, recognised issue is the strongest signal
            if sim > best_sim:
                best, best_sim = c, sim
    created = not (best and best_sim >= JOIN_THRESHOLD)
    ts = now_iso()
    if created:
        cid = f"n-{uuid.uuid4().hex[:8]}"
        conn.execute("""INSERT INTO narrative_clusters (cluster_id, state, label, issue, centroid, first_seen, last_seen,
                        item_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,0,?,?)""",
                     (cid, state, issue or "New narrative", issue, json.dumps(vec), published, published, ts, ts))
    else:
        cid = best["cluster_id"]
    conn.execute("UPDATE media_items SET cluster_id = ? WHERE item_id = ?", (cid, item_id))
    refresh_cluster(conn, cid)
    return cid, created


def refresh_cluster(conn, cluster_id: str) -> None:
    items = [dict(r) for r in conn.execute("SELECT * FROM media_items WHERE cluster_id = ? ORDER BY published_at", (cluster_id,))]
    c = dict(conn.execute("SELECT * FROM narrative_clusters WHERE cluster_id = ?", (cluster_id,)).fetchone())
    if not items:
        conn.execute("DELETE FROM narrative_clusters WHERE cluster_id = ?", (cluster_id,)); return
    centroid: Counter = Counter()
    for it in items[-60:]:
        v = json.loads(it["tf"])
        n = math.sqrt(sum(x * x for x in v.values())) or 1
        for t, w in v.items():
            centroid[t] += w / n
    centroid = dict(centroid.most_common(120))
    issues = Counter(it["issue"] for it in items if it["issue"])
    issue = issues.most_common(1)[0][0] if issues else c["issue"]
    label = c["label"]
    if c["label_source"] == "auto":
        label = _label(items, issue, centroid)
    rep = max(items[-20:], key=lambda it: it["negativity"] * SOURCE_WEIGHT.get(it["source_type"], 0.4)
              + (0.2 if re.search(r"\d", it["claim"] or "") else 0) + (0.15 if it["language"] == "en" else 0))
    conn.execute("""UPDATE narrative_clusters SET label = ?, issue = ?, centroid = ?, claim = ?, first_seen = ?, last_seen = ?,
                    item_count = ?, updated_at = ? WHERE cluster_id = ?""",
                 (label, issue, json.dumps(centroid), rep["claim"], items[0]["published_at"], items[-1]["published_at"],
                  len(items), now_iso(), cluster_id))


def _label(items, issue, centroid) -> str:
    protest_share = sum(bool(PROTEST.search(it["text"])) for it in items) / len(items)
    if issue:
        return f"{issue}: protests" if protest_share >= 0.3 else issue
    grams = [t for t, _ in sorted(centroid.items(), key=lambda x: -x[1]) if " " in t and not re.search(r"[ऀ-੿]", t)]
    words = grams[:1] or [t for t, _ in sorted(centroid.items(), key=lambda x: -x[1])[:3]]
    base = " ".join(words).title() if words else "Unlabelled narrative"
    return f"{base}: protests" if protest_share >= 0.3 else base


def recluster(conn) -> dict:
    """Rebuild every cluster from the items, oldest first. Manual labels and statuses are lost."""
    conn.execute("DELETE FROM warroom_responses")
    conn.execute("UPDATE media_items SET cluster_id = NULL")
    conn.execute("DELETE FROM narrative_clusters")
    items = [dict(r) for r in conn.execute("SELECT item_id, state, issue, tf, published_at FROM media_items ORDER BY published_at")]
    for it in items:
        assign(conn, it["item_id"], it["state"], it["issue"], json.loads(it["tf"]), it["published_at"])
    conn.commit()
    return {"items": len(items), "clusters": conn.execute("SELECT COUNT(*) FROM narrative_clusters").fetchone()[0]}


# ---------------------------------------------------------------------------
# threat scoring
# ---------------------------------------------------------------------------

def _age_h(iso: str, now: datetime) -> float:
    return max(0.0, (now - datetime.fromisoformat(iso)).total_seconds() / 3600)


def threat(items: list[dict], state: str | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    ages = [_age_h(it["published_at"], now) for it in items]
    weights = [0.5 ** (a / 24) for a in ages]
    recent = [(it, w) for it, a, w in zip(items, ages, weights) if a <= 72]
    w_sum = sum(w for _, w in recent)
    volume = 1 - math.exp(-w_sum / 5)
    last24 = sum(1 for a in ages if a <= 24)
    prev = sum(1 for a in ages if 24 < a <= 72) / 2
    velocity = max(0.0, min(1.0, (last24 - prev) / (prev + 2)))
    pool = recent or [(it, 1.0) for it in items]
    tot = sum(w for _, w in pool) or 1
    negativity = sum(it["negativity"] * w for it, w in pool) / tot
    src = sum(SOURCE_WEIGHT.get(it["source_type"], 0.4) * w for it, w in pool) / tot
    total_reach = sum(it["reach"] or 0 for it, _ in pool)
    reach = 0.6 * src + 0.4 * min(1.0, math.log10(1 + total_reach) / 6)
    channels = len({it["channel"].lower() for it, _ in pool})
    places = len({it["constituency_id"] for it, _ in pool if it["constituency_id"]})
    spread = min(1.0, 0.6 * min(1, (channels - 1) / 5) + 0.4 * min(1, max(0, places - 1) / 3))
    sig = STATE_SIGNIFICANCE.get(state, 0.6)
    raw = 0.32 * volume + 0.15 * velocity + 0.23 * negativity + 0.18 * reach + 0.12 * spread
    score = max(1, min(100, round(100 * raw * (0.55 + 0.45 * sig))))
    severity = next(name for floor, name in SEVERITY if score >= floor)
    return {"score": score, "severity": severity, "items_24h": last24, "items_72h": len(recent),
            "breakdown": {"volume": round(volume, 3), "velocity": round(velocity, 3), "negativity": round(negativity, 3),
                          "reach": round(reach, 3), "spread": round(spread, 3), "state_significance": sig},
            "trend": [sum(1 for a in ages if 6 * i <= a < 6 * (i + 1)) for i in range(11, -1, -1)]}


def score_clusters(conn, cluster_ids: list[str] | None = None) -> None:
    where = f"WHERE cluster_id IN ({','.join('?' * len(cluster_ids))})" if cluster_ids else ""
    for c in conn.execute(f"SELECT cluster_id, state FROM narrative_clusters {where}", cluster_ids or []).fetchall():
        items = [dict(r) for r in conn.execute("SELECT published_at, negativity, source_type, reach, channel, constituency_id "
                                               "FROM media_items WHERE cluster_id = ?", (c["cluster_id"],))]
        t = threat(items, c["state"])
        conn.execute("UPDATE narrative_clusters SET threat_score = ?, severity = ?, breakdown = ? WHERE cluster_id = ?",
                     (t["score"], t["severity"], json.dumps({**t["breakdown"], "items_24h": t["items_24h"],
                                                             "items_72h": t["items_72h"], "trend": t["trend"]}), c["cluster_id"]))
    conn.commit()


_last_rescore = [0.0]


def rescore_if_stale(conn, max_age_s: float = 60) -> None:
    """Scores decay with time, so re-score recently active clusters when read (at most once a minute)."""
    import time
    if time.time() - _last_rescore[0] < max_age_s:
        return
    _last_rescore[0] = time.time()
    since = (datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS)).isoformat(timespec="seconds")
    ids = [r[0] for r in conn.execute("SELECT cluster_id FROM narrative_clusters WHERE last_seen >= ?", (since,))]
    if ids:
        score_clusters(conn, ids)


def parse_upload(data: bytes, name: str) -> list[dict]:
    """Media items from an uploaded CSV, JSON or plain-text file (one item per paragraph)."""
    import csv
    import io
    text = data.decode("utf-8-sig", errors="replace")
    low = name.lower()
    if low.endswith(".json"):
        try:
            body = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name}: not valid JSON ({e.msg})") from None
        items = body.get("items") if isinstance(body, dict) else body
        if not isinstance(items, list):
            raise ValueError(f"{name}: expected a list of items or {{\"items\": [...]}}")
        return [i for i in items if isinstance(i, dict)]
    if low.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(text)))
        norm = lambda k: re.sub(r"[^a-z]+", "_", (k or "").lower()).strip("_")
        alias = {"channel_name": "channel", "source": "source_type", "outlet": "channel", "published_at": "timestamp",
                 "date": "timestamp", "time": "timestamp", "content": "text", "snippet": "text", "views": "reach"}
        return [{alias.get(norm(k), norm(k)): v for k, v in r.items() if k} for r in rows]
    return [{"text": p} for p in re.split(r"\n\s*\n", text) if p.strip()]
