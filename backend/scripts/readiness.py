"""Go-live readiness check.

    python -m scripts.readiness                  # report only (exit code 1 if anything blocks go-live)
    python -m scripts.readiness --remove-samples # also delete SAMPLE Copilot documents and the sample media feed

Run it against the database you will use for real work (VOTERENGINE_DB) with the same
environment variables as the server. It checks configuration and looks for synthetic
material that must never reach a real speech, briefing or report.
"""
from __future__ import annotations

import argparse
import sys

from werkzeug.security import check_password_hash

from app import config, db
from app.llm import provider_status
from app.privacy import masking_on
from app.suite import pitch

OK, WARN, FAIL = "PASS", "WARN", "FAIL"


def run(remove_samples: bool = False) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    def add(status, item, detail):
        out.append((status, item, detail))

    prod = config.ENV == "production"
    add(OK if prod else FAIL, "VOTERENGINE_ENV", f"'{config.ENV}'" + ("" if prod else ": set to production (disables demo logins and mock seeding)"))
    add(OK if config.JWT_SECRET != config._DEV_SECRET and len(config.JWT_SECRET) >= 32 else FAIL, "VOTERENGINE_JWT_SECRET",
        "set" if config.JWT_SECRET != config._DEV_SECRET else "still the development secret")
    add(OK if config.COOKIE_SECURE else FAIL, "Secure cookies", "on" if config.COOKIE_SECURE else "off: serve over HTTPS and keep VOTERENGINE_COOKIE_SECURE=1")
    add(OK if config.CORS_ORIGIN.startswith("https://") or not config.CORS_ORIGIN else WARN, "CORS_ORIGIN", config.CORS_ORIGIN)
    add(OK if not pitch.demo_allowed() else WARN, "Pitch demo", "off" if not pitch.demo_allowed()
        else "enabled: it uses a separate database file, but turn it off on the campaign server (VOTERENGINE_PITCH_DEMO=0)")
    llm = provider_status()
    add(OK if llm.get("live") else WARN, "LLM provider", f"{llm.get('provider')}" + ("" if llm.get("live") else ": offline drafting only"))
    add(WARN, "Database", f"SQLite at {db.workspace_path('main')}: fine for one API process; move to Postgres for several")

    with db.session() as c:
        db.init_schema(c)
        mock = c.execute("SELECT COUNT(*) FROM voters WHERE source = 'mock'").fetchone()[0]
        real = c.execute("SELECT COUNT(*) FROM voters WHERE source != 'mock' OR source IS NULL").fetchone()[0]
        add(FAIL if mock else OK, "Synthetic voters", f"{mock:,} mock voters present: use a fresh database for real rolls" if mock
            else f"none ({real:,} real voters)")
        demo = []
        for (h,) in c.execute("SELECT DISTINCT password_hash FROM users WHERE active = 1").fetchall():   # demo logins share one hash
            if check_password_hash(h, config.DEMO_PASSWORD):
                demo += [r[0] for r in c.execute("SELECT username FROM users WHERE password_hash = ? AND active = 1", (h,))]
        add(FAIL if demo else OK, "Demo passwords", f"{len(demo)} login(s) still use the demo password (e.g. {demo[0]})" if demo else "none found")
        admins = c.execute("SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND active = 1").fetchone()[0]
        add(OK if admins else FAIL, "Strategist login", f"{admins} active" if admins else "none: create one with scripts.create_user")
        if remove_samples:
            c.execute("DELETE FROM copilot_documents WHERE source_citation LIKE 'SAMPLE%'")
            c.execute("DELETE FROM media_items WHERE channel LIKE '%(sample)%'")
            c.execute("""DELETE FROM narrative_clusters WHERE cluster_id NOT IN
                         (SELECT DISTINCT cluster_id FROM media_items WHERE cluster_id IS NOT NULL)""")
            c.commit()
        docs = c.execute("SELECT COUNT(*) FROM copilot_documents WHERE source_citation LIKE 'SAMPLE%'").fetchone()[0]
        media = c.execute("SELECT COUNT(*) FROM media_items WHERE channel LIKE '%(sample)%'").fetchone()[0]
        add(FAIL if docs else OK, "Sample Copilot documents", f"{docs} present (run with --remove-samples)" if docs else "none")
        add(FAIL if media else OK, "Sample media feed", f"{media} items present (run with --remove-samples)" if media else "none")
        real_docs = c.execute("SELECT COUNT(*) FROM copilot_documents WHERE active = 1 AND source_citation NOT LIKE 'SAMPLE%'").fetchone()[0]
        add(OK if real_docs else WARN, "Knowledge base", f"{real_docs} documents" if real_docs
            else "empty: upload budgets, assembly answers, surveys and party commitments before drafting")
        add(OK if masking_on(c) else FAIL, "PII masking", "on" if masking_on(c) else "off: turn on in Privacy settings")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remove-samples", action="store_true", help="delete SAMPLE Copilot documents and the sample media feed")
    a = ap.parse_args()
    rows = run(a.remove_samples)
    w = max(len(r[1]) for r in rows)
    for status, item, detail in rows:
        print(f"[{status}] {item.ljust(w)}  {detail}")
    fails = sum(r[0] == FAIL for r in rows)
    print(f"\n{'Ready for go-live.' if not fails else f'{fails} blocking item(s).'}")
    sys.exit(1 if fails else 0)
