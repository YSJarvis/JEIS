"""State profiles: the single place where state-specific behaviour lives.

Every number here is a tunable model parameter, not a fact about any real
community. Mock affinity offsets per community group are drawn from a seeded
RNG in the generator precisely so the prototype does not encode assumptions
about how any group votes. When real data arrives, calibrate these weights
against past results (see README > Calibration).
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("VOTERENGINE_DB", BASE_DIR / "data" / "voterengine.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"
MODEL_VERSION = "heuristic-v1.0"

QUADRANTS = {
    "PERSUADE": {"label": "Persuadable · High Turnout", "action": "Top priority — persuade"},
    "GOTV": {"label": "Loyal · Low Turnout", "action": "Get out the vote"},
    "OPPOSITION": {"label": "Opposition Core", "action": "Skip"},
    "LOW": {"label": "Neutral / Low Turnout", "action": "Low priority"},
}

# Thresholds used by the quadrant classifier (0..100 scores, -1..1 affinity).
THRESHOLDS = {
    "swing_high": 62,       # calibrated swing >= this (~top 20% in state) → persuadable
    "turnout_high": 62,     # turnout >= this → likely to vote
    "loyal_affinity": 0.25, # affinity >= this → our supporter
    "opp_affinity": -0.35,  # affinity <= this → opposition
}

STATES: dict[str, dict] = {
    "UP": {
        "name": "Uttar Pradesh",
        "center": (29.06, 77.66),
        "constituencies": {
            "Sardhana": (29.145, 77.614),
            "Meerut Cantt": (28.995, 77.700),
        },
        "community_groups": {  # name: population share in mock data
            "General": 0.20, "OBC (Yadav)": 0.10, "OBC (Non-Yadav)": 0.28,
            "SC (Jatav)": 0.12, "SC (Non-Jatav)": 0.09, "Muslim": 0.21,
        },
        "schemes": {"PM-KISAN": 0.30, "Ration (PMGKAY)": 0.55, "PMAY": 0.18,
                    "Ujjwala": 0.25, "Ayushman Bharat": 0.22},
        "beneficiary_rate": 0.62,
        "migrant_rate": 0.07,
        "turnout_base": (58, 66),
        "swing_weights": {"neutrality": 0.30, "scheme": 0.35, "age": 0.15, "community": 0.20},
        "turnout_weights": {"history": 0.45, "booth": 0.25, "age": 0.20, "local": 0.10},
        "migrant_penalty": 0.55,
    },
    "PB": {
        "name": "Punjab",
        "center": (30.30, 75.86),
        "constituencies": {
            "Sangrur": (30.245, 75.844),
            "Dhuri": (30.370, 75.868),
        },
        "community_groups": {
            "Jat Sikh": 0.30, "SC Sikh": 0.20, "Hindu (General)": 0.18,
            "Hindu (OBC)": 0.14, "SC Hindu": 0.12, "Other": 0.06,
        },
        "schemes": {"PM-KISAN": 0.35, "Ration (Atta-Dal)": 0.45, "Old-Age Pension": 0.15},
        "beneficiary_rate": 0.40,
        "migrant_rate": 0.16,          # high NRI / migrant absentee share
        "turnout_base": (64, 74),
        # micro-demographics (community, dera, agrarian) outweigh schemes
        "swing_weights": {"neutrality": 0.30, "scheme": 0.10, "age": 0.15,
                          "community": 0.20, "dera": 0.12, "agrarian": 0.13},
        "turnout_weights": {"history": 0.45, "booth": 0.25, "age": 0.20, "local": 0.10},
        "migrant_penalty": 0.70,
        "extras": {"dera_follower_rate": 0.22, "farmer_rate": 0.45},
    },
    "UK": {
        "name": "Uttarakhand",
        "center": (30.18, 78.78),
        "constituencies": {
            "Pauri": (30.152, 78.778),
            "Srinagar (Garhwal)": (30.222, 78.784),
        },
        "community_groups": {
            "Thakur / Rajput": 0.35, "Brahmin": 0.25, "SC": 0.19,
            "OBC": 0.13, "Muslim": 0.08,
        },
        "schemes": {"PM-KISAN": 0.28, "Ration (PMGKAY)": 0.50, "PMAY-G": 0.15,
                    "Ujjwala": 0.30, "Ayushman Bharat": 0.25},
        "beneficiary_rate": 0.58,
        "migrant_rate": 0.18,          # hill out-migration
        "turnout_base": (55, 65),
        "swing_weights": {"neutrality": 0.30, "scheme": 0.35, "age": 0.15,
                          "community": 0.10, "terrain": 0.10},
        "turnout_weights": {"history": 0.40, "booth": 0.25, "age": 0.15, "local": 0.20},
        "migrant_penalty": 0.60,
        "extras": {"terrain_clusters": {"Valley": 0.40, "Mid-Hill": 0.35, "High-Hill": 0.25}},
    },
    "GA": {
        "name": "Goa",
        "center": (15.287, 73.964),
        "constituencies": {
            "Margao": (15.283, 73.958),
            "Fatorda": (15.293, 73.972),
        },
        "community_groups": {
            "Catholic": 0.30, "Hindu (Bhandari)": 0.22, "Hindu (GSB / Saraswat)": 0.14,
            "Hindu (Other)": 0.22, "Muslim": 0.08, "Other": 0.04,
        },
        "schemes": {"Griha Aadhar": 0.25, "DSSS Pension": 0.20, "Ladli Laxmi": 0.08, "Ration": 0.30},
        "beneficiary_rate": 0.38,
        "migrant_rate": 0.09,
        "turnout_base": (74, 82),
        # tight micro-constituencies: family influence and micro-demographics dominate
        "swing_weights": {"neutrality": 0.30, "scheme": 0.10, "age": 0.15,
                          "community": 0.20, "family": 0.25},
        "turnout_weights": {"history": 0.45, "booth": 0.25, "age": 0.20, "local": 0.10},
        "migrant_penalty": 0.60,
        "booth_spread_km": 1.2,       # micro-constituencies are compact
    },
}

STATE_CODES = list(STATES.keys())


# ---------------------------------------------------------------------------
# Security, auth and privacy settings (override with environment variables)
# ---------------------------------------------------------------------------
ENV = os.environ.get("VOTERENGINE_ENV", "development")
_DEV_SECRET = "dev-only-insecure-secret-change-me-voterengine-2026"
JWT_SECRET = os.environ.get("VOTERENGINE_JWT_SECRET", _DEV_SECRET)
JWT_ALGORITHM = "HS256"
JWT_TTL_HOURS = int(os.environ.get("VOTERENGINE_JWT_TTL_HOURS", 12))
AUTH_COOKIE = "ve_token"
COOKIE_SECURE = os.environ.get("VOTERENGINE_COOKIE_SECURE", "0" if ENV != "production" else "1") == "1"
CSRF_HEADER = ("X-Requested-With", "VoterEngine")   # required on cookie-authenticated writes
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "http://localhost:3000")
TIMEZONE = os.environ.get("VOTERENGINE_TZ", "Asia/Kolkata")  # defines "today" for cadre routes
DEMO_PASSWORD = os.environ.get("VOTERENGINE_DEMO_PASSWORD", "voterengine")

if ENV == "production" and JWT_SECRET == _DEV_SECRET:
    raise RuntimeError("Set VOTERENGINE_JWT_SECRET (32+ random bytes) before running in production.")

ROLES = {
    "ADMIN": "Central Strategist",
    "MANAGER": "Constituency Manager",
    "CADRE": "Field Cadre",
}

# DPDP Act 2023 consent states and how each is processed.
CONSENT_STATUSES = ("GRANTED", "PENDING", "OPT_OUT")
CONSENT_POLICY = {
    "GRANTED": "Processed; phone shown to the assigned cadre.",
    "PENDING": "Electoral-roll fields processed; phone withheld from every view until consent is recorded.",
    "OPT_OUT": "Excluded from scoring, routing and lists; phone erased. Consent log retained.",
}
# Privacy-by-default: PII masking starts ON in non-essential (dashboard) views.
DEFAULT_SETTINGS = {"pii_masking": "on"}

# Human-readable labels for the scoring configurator.
FEATURE_LABELS = {
    "swing": {
        "neutrality": "No fixed party lean", "scheme": "Welfare scheme reliance", "age": "Age (younger swings more)",
        "community": "Contested community", "dera": "Dera influence", "agrarian": "Farm household",
        "terrain": "Hill terrain", "family": "Family influence",
    },
    "turnout": {
        "history": "Past voting history", "booth": "Booth historical turnout", "age": "Age profile",
        "local": "Distance to booth",
    },
}
