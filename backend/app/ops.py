"""Cadre operations: today's door progress, sync health and consent capture,
plus the Daily Booth Summary export (CSV / PDF). Aggregates only — no voter
personal data leaves through these endpoints.

"Not yet synced" is necessarily an estimate: the server can't see a phone
that is offline. It combines (a) queue sizes phones reported at their last
contact and (b) doors still open on routes whose cadre hasn't been in touch
for STALE_MINUTES — those visits may be done but not sent.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import TIMEZONE
from .db import now_iso
from .knowledge import directives_for

STALE_MINUTES = 30
TZ = ZoneInfo(TIMEZONE)


def touch_device(conn, cadre_id: str, synced: bool, queue_remaining: int = 0, app_version: str | None = None) -> None:
    ts = now_iso()
    conn.execute("""INSERT INTO cadre_devices (cadre_id, last_seen_at, last_sync_at, reported_queue, app_version)
                    VALUES (?,?,?,?,?) ON CONFLICT(cadre_id) DO UPDATE SET last_seen_at = excluded.last_seen_at,
                    last_sync_at = COALESCE(excluded.last_sync_at, cadre_devices.last_sync_at),
                    reported_queue = excluded.reported_queue,
                    app_version = COALESCE(excluded.app_version, cadre_devices.app_version)""",
                 (cadre_id, ts, ts if synced else None, max(0, int(queue_remaining or 0)), app_version))


def _local_day_bounds(date: str) -> tuple[str, str]:
    start = datetime.fromisoformat(date).replace(tzinfo=TZ)
    return (start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            (start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds"))


def _minutes_since(iso: str | None) -> float | None:
    if not iso:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60


def summary(conn, booth_ids: list[str], date: str, constituency_label: str, state: str | None) -> dict:
    """booth_ids: booths in the caller's scope (already filtered)."""
    if not booth_ids:
        return {"date": date, "scope": constituency_label, "totals": {}, "cadres": [], "booths": [], "hourly": [],
                "consent": {}, "directives": [], "generated_at": now_iso()}
    ph = ",".join("?" * len(booth_ids))
    day_start, day_end = _local_day_bounds(date)

    # stops on today's routes for cadres whose booths are in scope
    stops = [dict(r) for r in conn.execute(f"""
        SELECT rs.stop_id, rs.status, rs.updated_at, rs.household_id, h.booth_id, r.cadre_id
        FROM route_stops rs JOIN routes r USING(route_id) JOIN households h ON h.household_id = rs.household_id
        WHERE r.route_date = ? AND h.booth_id IN ({ph})""", [date, *booth_ids])]
    hh_ids = list({s["household_id"] for s in stops})
    members: dict[str, list[dict]] = defaultdict(list)
    for i in range(0, len(hh_ids), 900):
        chunk = hh_ids[i:i + 900]
        for m in conn.execute(f"""SELECT v.household_id, v.consent_status, s.quadrant FROM voters v
                                  LEFT JOIN voter_scores s USING(voter_id) WHERE v.household_id IN ({','.join('?' * len(chunk))})""", chunk):
            members[m["household_id"]].append(dict(m))

    cadres = {r["cadre_id"]: dict(r) for r in conn.execute("SELECT * FROM cadres ORDER BY cadre_id")}
    in_scope_cadres = {cid for cid, c in cadres.items() if set(c["booth_ids"].split(",")) & set(booth_ids)}
    devices = {r["cadre_id"]: dict(r) for r in conn.execute("SELECT * FROM cadre_devices")}
    consent_today = defaultdict(int)
    for r in conn.execute(f"""SELECT v.booth_id, COUNT(*) n FROM consent_log l JOIN voters v ON v.voter_id = l.voter_id
                              WHERE l.source = 'field' AND l.new_status = 'GRANTED' AND l.created_at >= ? AND l.created_at < ?
                              AND v.booth_id IN ({ph}) GROUP BY v.booth_id""", [day_start, day_end, *booth_ids]):
        consent_today[r["booth_id"]] = r["n"]

    def blank():
        return dict(targeted=0, completed=0, visited=0, not_home=0, revisit=0, refused=0, remaining=0,
                    stale_pending=0, households_visited=0, households_consented=0, persuadables_reached=0,
                    supporters_reached=0)

    by_cadre: dict[str, dict] = {cid: blank() for cid in in_scope_cadres}
    by_booth: dict[str, dict] = {b: blank() for b in booth_ids}
    hourly = defaultdict(int)
    stale_cadres = set()
    for cid in in_scope_cadres:
        mins = _minutes_since((devices.get(cid) or {}).get("last_sync_at"))
        if mins is None or mins > STALE_MINUTES:
            stale_cadres.add(cid)

    for s in stops:
        rows = [by_cadre.setdefault(s["cadre_id"], blank()), by_booth[s["booth_id"]]]
        st = s["status"]
        for agg in rows:
            agg["targeted"] += 1
            if st == "PENDING":
                agg["remaining"] += 1
                if s["cadre_id"] in stale_cadres:
                    agg["stale_pending"] += 1
            else:
                agg["completed"] += 1
                agg[{"VISITED": "visited", "NOT_HOME": "not_home", "REVISIT": "revisit", "REFUSED": "refused"}[st]] += 1
            if st == "VISITED":
                mem = members.get(s["household_id"], [])
                agg["households_visited"] += 1
                agg["households_consented"] += any(m["consent_status"] == "GRANTED" for m in mem)
                agg["persuadables_reached"] += sum(m["quadrant"] == "PERSUADE" for m in mem)
                agg["supporters_reached"] += sum(m["quadrant"] == "GOTV" for m in mem)
        if st != "PENDING" and s["updated_at"]:
            hourly[datetime.fromisoformat(s["updated_at"]).astimezone(TZ).hour] += 1

    def rate(a):
        return round(100 * a["households_consented"] / a["households_visited"], 1) if a["households_visited"] else None

    cadre_rows = []
    for cid, a in sorted(by_cadre.items()):
        c = cadres.get(cid, {"name": cid, "booth_ids": ""})
        dev = devices.get(cid) or {}
        mins = _minutes_since(dev.get("last_sync_at"))
        if not a["targeted"]:
            status = "No route"
        elif a["remaining"] == 0:
            status = "Done"
        elif mins is None:
            status = "Not started"
        elif mins > STALE_MINUTES:
            status = "Out of contact"
        else:
            status = "On route"
        cadre_rows.append({"cadre_id": cid, "name": c["name"], "booth_ids": c["booth_ids"].split(","), **a,
                           "consent_rate": rate(a), "last_sync_at": dev.get("last_sync_at"),
                           "minutes_since_sync": round(mins) if mins is not None else None,
                           "reported_queue": dev.get("reported_queue", 0) if mins is not None and mins < 24 * 60 else 0,
                           "status": status})

    booth_meta = {r["booth_id"]: dict(r) for r in conn.execute(
        f"SELECT booth_id, name, constituency FROM booths WHERE booth_id IN ({ph})", booth_ids)}
    booth_rows = []
    for b in booth_ids:
        a = by_booth[b]
        cad = [c["name"] for c in cadres.values() if b in c["booth_ids"].split(",")]
        booth_rows.append({"booth_id": b, "name": booth_meta[b]["name"], "constituency": booth_meta[b]["constituency"],
                           "cadres": cad, **a, "consent_rate": rate(a), "consents_captured_today": consent_today[b]})

    tot = blank()
    for a in by_booth.values():
        for k in tot:
            tot[k] += a[k]
    tot["reported_unsynced"] = sum(r["reported_queue"] for r in cadre_rows)
    tot["cadres_active"] = sum(r["status"] in ("On route", "Done") for r in cadre_rows)
    tot["cadres_out_of_contact"] = sum(r["status"] == "Out of contact" for r in cadre_rows)
    tot["cadres_total"] = len(cadre_rows)
    consent = {"households_visited": tot["households_visited"], "households_consented": tot["households_consented"],
               "rate": rate(tot), "captured_today": sum(consent_today.values())}
    constituency = booth_meta[booth_ids[0]]["constituency"] if len({m["constituency"] for m in booth_meta.values()}) == 1 else None
    return {"date": date, "scope": constituency_label, "state": state, "totals": tot, "consent": consent,
            "cadres": cadre_rows, "booths": booth_rows,
            "hourly": [{"hour": h, "completed": hourly.get(h, 0)} for h in range(7, 21)],
            "directives": directives_for(conn, date, state, constituency) if state else [],
            "stale_minutes": STALE_MINUTES, "generated_at": now_iso()}


# ---------------- exports ----------------

BOOTH_COLUMNS = [("booth_id", "Booth"), ("name", "Polling station"), ("constituency", "Constituency"),
                 ("cadres", "Cadres"), ("targeted", "Doors targeted"), ("completed", "Doors completed"),
                 ("visited", "Visited"), ("not_home", "Not home"), ("revisit", "Come back"), ("refused", "Refused"),
                 ("remaining", "Remaining"), ("stale_pending", "Open on out-of-contact routes"),
                 ("households_consented", "Visited households with consent"), ("consent_rate", "Consent rate %"),
                 ("consents_captured_today", "Consents captured today"), ("persuadables_reached", "Persuadables reached"),
                 ("supporters_reached", "Supporters reached")]


def to_csv(s: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date"] + [label for _, label in BOOTH_COLUMNS])
    for b in s["booths"]:
        w.writerow([s["date"]] + [("; ".join(b[k]) if isinstance(b[k], list) else ("" if b[k] is None else b[k]))
                                  for k, _ in BOOTH_COLUMNS])
    t = s["totals"]
    w.writerow([s["date"], "TOTAL", s["scope"], ""] + [t.get(k, "") if k != "consent_rate" else (s["consent"]["rate"] or "")
                                                        for k, _ in BOOTH_COLUMNS[4:14]] +
               [s["consent"]["captured_today"], t.get("persuadables_reached", ""), t.get("supporters_reached", "")])
    for d in s.get("directives", []):
        w.writerow([s["date"], "DIRECTIVE", d["text"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM so Excel reads Hindi correctly


def _latin(text: str, fallback: str) -> str:
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        return fallback


def to_pdf(s: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"Daily Booth Summary {s['date']}", author="VoterEngine AI")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], alignment=0, fontSize=18, spaceAfter=2)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=8.5, textColor=colors.HexColor("#4A4659"))
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=9.5, leading=12)
    violet = colors.HexColor("#5B2E91")
    t, c = s["totals"], s["consent"]
    day = datetime.fromisoformat(s["date"]).strftime("%A %d %B %Y")
    gen = datetime.fromisoformat(s["generated_at"]).astimezone(TZ).strftime("%H:%M")
    story = [Paragraph(f"Daily Booth Summary: {escape(_latin(s['scope'], 'constituency'))}", h1),
             Paragraph(f"{day} · generated {gen} IST · aggregates only, no voter personal data", small), Spacer(0, 5 * mm)]

    kpis = [("Doors targeted", t["targeted"]), ("Completed", t["completed"]), ("Remaining", t["remaining"]),
            ("Open on out-of-contact routes", t["stale_pending"]),
            ("Consent rate (visited households)", f"{c['rate']}%" if c["rate"] is not None else "–"),
            ("Consents captured today", c["captured_today"])]
    lab = ParagraphStyle("lab", parent=body, fontSize=7.5, leading=9, textColor=colors.HexColor("#7C7889"))
    num = ParagraphStyle("num", parent=body, fontSize=16, leading=19, fontName="Helvetica-Bold")
    kt = Table([[[Paragraph(k, lab), Spacer(0, 1.5 * mm), Paragraph(str(v), num)] for k, v in kpis]],
               colWidths=[44.5 * mm] * 6, hAlign="LEFT")
    kt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DCDDE3")),
                            ("LINEBEFORE", (1, 0), (-1, -1), 0.5, colors.HexColor("#DCDDE3")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [kt, Spacer(0, 4 * mm)]

    if s.get("directives"):
        story.append(Paragraph("<b>Today's focus</b>", body))
        for d in s["directives"]:
            story.append(Paragraph("• " + escape(_latin(d["text"], "[Text in a non-Latin script: see the CSV export]")), body))
        story.append(Spacer(0, 3 * mm))

    head = ParagraphStyle("head", parent=body, fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white)

    def table(rows, header, widths):
        hdr = [Paragraph(h, ParagraphStyle("h2", parent=head, alignment=0 if i < 2 else 2)) for i, h in enumerate(header)]
        tb = Table([hdr] + rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        tb.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 0), (-1, 0), violet),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F3F0")]),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#DCDDE3")),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return tb

    pct = lambda v: f"{v}%" if v is not None else "–"
    story.append(Paragraph("<b>By booth</b>", body))
    story.append(table(
        [[b["booth_id"], _latin(b["name"], b["booth_id"])[:40], b["targeted"], b["completed"], b["visited"],
          b["not_home"], b["revisit"], b["refused"], b["remaining"], pct(b["consent_rate"]), b["consents_captured_today"],
          b["persuadables_reached"], b["supporters_reached"]] for b in s["booths"]],
        ["Booth", "Polling station", "Target", "Done", "Visited", "Not home", "Come back", "Refused", "Left",
         "Consent rate", "New consents", "Persuad- ables", "Support- ers"],
        [24 * mm, 56 * mm] + [17 * mm] * 11))
    story.append(Spacer(0, 5 * mm))
    story.append(Paragraph("<b>By cadre</b>", body))
    story.append(table(
        [[_latin(r["name"], r["cadre_id"]), ", ".join(r["booth_ids"]), r["targeted"], r["completed"], r["remaining"],
          pct(r["consent_rate"]), r["status"],
          (f"{r['minutes_since_sync']} min ago" if r["minutes_since_sync"] is not None and r["minutes_since_sync"] < 600
           else ("–" if r["minutes_since_sync"] is None else "over 10 h"))] for r in s["cadres"]],
        ["Cadre", "Booths", "Target", "Done", "Left", "Consent", "Status", "Last sync"],
        [48 * mm, 50 * mm, 20 * mm, 20 * mm, 20 * mm, 22 * mm, 34 * mm, 34 * mm]))
    story += [Spacer(0, 4 * mm), Paragraph(
        f"“Open on out-of-contact routes” counts doors still pending on routes whose cadre has not synced for "
        f"{s['stale_minutes']}+ minutes: some may be visited but not yet sent.", small)]
    doc.build(story)
    return buf.getvalue()
