"""Synthetic media feed for CounterPulse AI demos and tests.

Every channel name ends in "(sample)" and every item is invented. Timestamps are
relative to the moment of seeding so the war room always looks "live".
Delete with POST /api/warroom/reset-sample (strategist) before monitoring real media.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .engine import ingest_item, score_clusters

# (state, constituency, source_type, channel, text, reach)
FEED = {
    "cane": [
        ("UP", "Sardhana", "print", "Meerut Samachar (sample)", "Cane growers supplying the Daurala mill say Rs 100 crore is still unpaid and accuse the government of ignoring farmers for months.", 42000),
        ("UP", "Sardhana", "social", "Sardhana Kisan Manch page (sample)", "Farmers protest outside Daurala mill gate demanding pending cane dues. Payments delayed since July, families in distress.", 18000),
        ("UP", "Sardhana", "tv", "Doaba Live TV (sample)", "Opposition leader claims cane dues delay shows the government has failed sugarcane farmers in Sardhana.", 260000),
        ("UP", "Sardhana", "youtube", "Kisan Awaaz channel (sample)", "Video: angry farmers say mill dues pending, no payment for the last crushing season, dharna to continue.", 95000),
        ("UP", "Meerut Cantt", "social", "Meerut Updates (sample)", "Daurala sugar mill dues still pending, farmers threaten blockade of the highway next week.", 12000),
        ("UP", "Sardhana", "print", "Dainik Paschim (sample)", "गन्ना किसानों का बकाया भुगतान महीनों से लंबित, किसानों ने मिल पर धरना दिया और सरकार पर आरोप लगाया।", 30000),
        ("UP", "Sardhana", "social", "Kaili Gram Samachar (sample)", "Cane payment delay: farmers in Kaili say they borrowed money to sow because mill dues are unpaid.", 6000),
        ("UP", "Sardhana", "tv", "Pradesh 24x7 (sample)", "Debate tonight: who is responsible for pending cane dues at the Daurala mill? Farmers' protest enters day three.", 310000),
    ],
    "drain": [
        ("UP", "Sardhana", "print", "Meerut Samachar (sample)", "Jani Khurd residents block link road over waterlogging; drains overflow after every rain and the drainage project has not started.", 38000),
        ("UP", "Sardhana", "social", "Jani Khurd Residents Forum (sample)", "Third protest this month: waterlogging in Jani Khurd lanes, children cannot reach school, drainage funds lapsed.", 9000),
        ("UP", "Sardhana", "youtube", "Ground Report UP (sample)", "Visuals from Jani Khurd: overflowing drains, residents say officials ignored complaints for two years.", 54000),
    ],
    "paddy": [
        ("PB", "Sangrur", "print", "Malwa Times (sample)", "Paddy lifting slow at Sangrur grain market; heaps waiting six days and farmers allege payment delays.", 45000),
        ("PB", "Sangrur", "tv", "Punjab Khabar TV (sample)", "Farmers protest at Sangrur mandi as paddy lifting stalls; arhtiyas say a third of arrivals not lifted.", 220000),
        ("PB", "Dhuri", "social", "Dhuri Farmers Union page (sample)", "Dharna at Dhuri mandi over slow procurement and moisture deductions. Farmers stranded with crop for a week.", 15000),
        ("PB", "Sangrur", "youtube", "Kisan Punjab Live (sample)", "ਮੰਡੀ ਵਿੱਚ ਝੋਨੇ ਦੀ ਖਰੀਦ ਵਿੱਚ ਦੇਰੀ, ਕਿਸਾਨ ਨਾਰਾਜ਼ ਅਤੇ ਧਰਨੇ ਉੱਤੇ। Procurement delays continue at the grain market.", 70000),
    ],
    "hill_roads": [
        ("UK", "Pauri", "print", "Garhwal Darpan (sample)", "Monsoon road blockages cut off villages near Pauri for days; residents say the road repair work is incomplete.", 21000),
        ("UK", "Pauri", "social", "Pauri Youth Collective (sample)", "Road blocked again near Kot. Patients had to be carried 6 km. When will road repair be completed?", 8000),
    ],
    "water": [
        ("GA", "Margao", "print", "Salcete Herald (sample)", "Irregular water supply in Margao wards; residents of Aquem and Borda depend on tankers as pipeline work stalls.", 26000),
        ("GA", "Margao", "social", "Margao Citizens Network (sample)", "No piped water for three days in Borda. Water supply complaints ignored, pipeline replacement only 30% done.", 11000),
        ("GA", "Fatorda", "youtube", "Goa Ground Zero (sample)", "Residents protest water cuts in Fatorda and Margao; tanker mafia allegations as supply stays irregular.", 33000),
    ],
    "iti": [
        ("UP", "Sardhana", "social", "Sardhana Yuva Morcha page (sample)", "Only 320 ITI seats for 1,100 applicants in Sardhana. Youth demand more seats and instructors.", 7000),
        ("UP", "Sardhana", "print", "Dainik Paschim (sample)", "Skill centre announced in 2024 still at land identification stage, local youth say jobs are moving away.", 19000),
    ],
}
# hours ago for each item: cane is rising (most items in the last day)
HOURS = {"cane": [60, 40, 20, 14, 9, 6, 3, 1], "drain": [50, 30, 18], "paddy": [44, 26, 12, 5], "hill_roads": [66, 30],
         "water": [55, 22, 8], "iti": [70, 36]}
SAMPLE_ACTOR = "sample-feed"


def seed_feed(conn, force=False) -> int:
    if not force and conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]:
        return 0
    now = datetime.now(timezone.utc)
    n = 0
    for key, items in FEED.items():
        for (state, const, src, channel, text, reach), h in zip(items, HOURS[key]):
            res = ingest_item(conn, {"state": state, "constituency": const, "source_type": src, "channel": channel,
                                     "text": text, "reach": reach,
                                     "timestamp": (now - timedelta(hours=h)).isoformat(timespec="seconds")}, SAMPLE_ACTOR)
            n += res["status"] == "ingested"
    score_clusters(conn)
    conn.commit()
    return n


SIMULATED = [
    ("UP", "Sardhana", "social", "Sardhana Updates (sample)", "Farmers at Daurala mill say cane dues still pending; protest to widen to nearby villages tomorrow."),
    ("UP", "Sardhana", "tv", "Pradesh 24x7 (sample)", "Breaking: cane farmers block the Meerut road demanding pending mill dues, opposition joins the dharna."),
    ("PB", "Sangrur", "social", "Sangrur Live (sample)", "Paddy heaps still not lifted at Sangrur mandi, farmers allege payment delays for a second week."),
    ("GA", "Margao", "social", "Margao Citizens Network (sample)", "Water supply cut again in Aquem ward; tankers late, residents angry at the slow pipeline work."),
    ("UP", "Sardhana", "print", "Meerut Samachar (sample)", "Power cuts of six hours daily in Sardhana villages hit tubewell irrigation, farmers complain."),
]


def simulate(conn, count: int, actor: str) -> list[dict]:
    """Inject `count` fresh sample items timestamped now (demo of live updates)."""
    out = []
    for i in range(count):
        state, const, src, channel, text = random.choice(SIMULATED)
        text = f"{text} (update {datetime.now(timezone.utc).strftime('%H:%M:%S')}.{i})"
        out.append(ingest_item(conn, {"state": state, "constituency": const, "source_type": src, "channel": channel,
                                      "text": text, "reach": random.randint(2000, 90000)}, actor))
    score_clusters(conn, list({o["cluster_id"] for o in out if o.get("cluster_id")}))
    conn.commit()
    return out
