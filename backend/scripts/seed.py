"""Regenerate mock data and scores.  Usage: python -m scripts.seed [--seed 42] [--export data/mock.json]"""
import argparse
import json

from app import db
from app.generator import export_json, seed_database

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--export", help="also write the raw mock dataset to this JSON path")
    a = ap.parse_args()
    with db.session() as c:
        print(json.dumps(seed_database(c, seed=a.seed)))
        if a.export:
            export_json(c, a.export); print("exported", a.export)
