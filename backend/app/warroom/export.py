"""Exports for CounterPulse counter-briefs: Markdown (any language) and PDF (Latin script).

Both formats render the same outline, so they never drift apart.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from xml.sax.saxutils import escape

from ..copilot.export import _unicode_font

DISCLAIMER = ("Draft from CounterPulse AI. Opposition items are quoted as claims, not evidence. Check each figure against "
              "its source and get war-room approval before release.")
STATUS = {"pass": "Pass", "fixed": "Fixed", "info": "Note", "action": "Action needed", "fail": "Fail"}


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return ""


def copy_lines(p: dict) -> list[tuple[str, str]]:
    """Medium-specific copy as (style, text): style is h (subheading), p (paragraph), li (bullet) or pre (verbatim)."""
    c, m = p["copy"], p["medium"]
    out: list[tuple[str, str]] = []
    if m == "tv_brief":
        if c.get("headline_message"):
            out.append(("p", f"**Headline message:** {c['headline_message']}"))
        if c.get("talking_points"):
            out += [("h", "Talking points")] + [("li", x) for x in c["talking_points"]]
        if c.get("bridging_lines"):
            out += [("h", "Bridging lines")] + [("li", x) for x in c["bridging_lines"]]
        if c.get("likely_questions"):
            out.append(("h", "Likely questions"))
            for q in c["likely_questions"]:
                out += [("li", f"**Q:** {q['q']}"), ("p", f"**A:** {q['a']}")]
    elif m == "whatsapp":
        out.append(("pre", c.get("text") or ""))
        out.append(("p", f"_{c.get('characters', len(c.get('text') or ''))} characters_"))
    elif m == "press_release":
        out.append(("h", c.get("headline") or "Press release"))
        out += [("p", x) for x in c.get("body") or []]
        q = c.get("quote") or {}
        if q.get("text"):
            out.append(("p", f"“{q['text']}” — {q.get('attribution') or 'Spokesperson'}"))
    else:
        for i, v in enumerate(c.get("variants") or [], 1):
            out += [("h", f"Variant {i}"), ("li", f"**Headline:** {v['headline']}"), ("li", f"**Body:** {v['body']}"),
                    ("li", f"**Call to action:** {v['cta']}")]
    return out


def outline(p: dict) -> tuple[str, str, list[tuple[str, list[tuple[str, str]]]]]:
    title = f"{p['medium_label']}: {p['label']}"
    sub = " · ".join(x for x in [p["place"]["name"], p.get("state") or "", _when(p.get("generated_at")),
                                 f"Threat {p['threat']['score']}/100 ({p['threat']['severity']})",
                                 f"Status: {p.get('status', 'draft')}"] if x)
    a = p["assessment"]
    sections = [
        ("1. Threat summary and core opposition claim", [("p", p["threat_summary"]), ("p", f"**Core claim:** {p['core_claim']}"),
                                                          ("p", f"**Assessment:** {a['verdict_label']}. {a['explanation']}")]),
        ("2. Verified counter-facts", [("li", f["fact"]) for f in p["counter_facts"]] or [("p", "None verified: holding statement only.")]),
        (f"3. {p['medium_label']}", copy_lines(p)),
        ("4. ECI / MCC safety check", [("p", f"**Overall:** {p['safety_check']['status'].replace('_', ' ')}")]
         + [("li", f"**{STATUS[c['status']]} — {c['rule']}:** {c['detail']}") for c in p["safety_check"]["checks"]]),
    ]
    if p.get("avoid"):
        sections.append(("Do not say", [("li", x) for x in p["avoid"]]))
    if p.get("narrative"):
        sections.append(("What is being said (claims, not evidence)",
                         [("li", f"[{n['id']}] {n['source_type']}, {n['channel']}: {n['text']}") for n in p["narrative"]]))
    used = [s for s in p["sources"] if s["used"]]
    if used:
        sections.append(("Sources", [("li", f"**[{s['id']}]** {s['citation']}" + (f" ({s['url']})" if s.get("url") else ""))
                                     for s in used]))
    if p.get("warnings"):
        sections.append(("Review notes", [("li", w) for w in p["warnings"]]))
    return title, sub, sections


def to_markdown(p: dict) -> str:
    title, sub, sections = outline(p)
    out = [f"# {title}", "", sub, ""]
    for head, lines in sections:
        out += [f"## {head}", ""]
        for style, text in lines:
            if style == "h":
                out += [f"### {text}", ""]
            elif style == "li":
                out.append(f"- {text}")
            elif style == "pre":
                out += ["```", text, "```", ""]
            else:
                out += [text, ""]
        out.append("")
    out += ["---", "", f"_{DISCLAIMER}_", ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def to_pdf(p: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    if p.get("generated_language", "en") != "en":
        raise ValueError("PDF export supports English drafts. Use Markdown or Print for Hindi and Punjabi.")
    font = _unicode_font()
    base, bold = (font, f"{font}-Bold") if font else ("Helvetica", "Helvetica-Bold")

    def clean(t: str) -> str:
        t = str(t or "")
        if not font:
            for a, b in (("₹", "Rs "), ("“", '"'), ("”", '"'), ("’", "'"), ("‘", "'"), ("–", "-"), ("—", "-"), ("…", "..."), ("•", "-")):
                t = t.replace(a, b)
            try:
                t.encode("latin-1")
            except UnicodeEncodeError:
                raise ValueError("This draft has characters the PDF font can't show. Use Markdown or Print.") from None
        t = escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", t)
        return re.sub(r"\[([SM]\d+)\]", r"<font size=7 color='#5B2E91'>[\1]</font>", t)

    ss = getSampleStyleSheet()
    ink, violet, muted = colors.HexColor("#1D1A2E"), colors.HexColor("#5B2E91"), colors.HexColor("#6B6779")
    H1 = ParagraphStyle("h1", parent=ss["Title"], fontName=bold, fontSize=17, leading=21, alignment=0, textColor=ink, spaceAfter=2)
    H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=bold, fontSize=12, leading=15, textColor=violet, spaceBefore=9, spaceAfter=3)
    H3 = ParagraphStyle("h3", parent=ss["Heading3"], fontName=bold, fontSize=10.5, leading=13, textColor=ink, spaceBefore=4, spaceAfter=1)
    B = ParagraphStyle("b", parent=ss["Normal"], fontName=base, fontSize=10, leading=14, textColor=ink, spaceAfter=3)
    S = ParagraphStyle("s", parent=B, fontSize=8.5, leading=11.5, textColor=muted)
    LI = ParagraphStyle("li", parent=B, leftIndent=10, bulletIndent=0, spaceAfter=2)
    PRE = ParagraphStyle("pre", parent=B, fontName=base, backColor=colors.HexColor("#F2F3F0"), borderPadding=6, leading=13.5)

    title, sub, sections = outline(p)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
                            title=title, author="CounterPulse AI")
    st = [Paragraph(clean(title), H1), Paragraph(clean(sub), S)]
    for head, lines in sections:
        st.append(Paragraph(clean(head), H2))
        for style, text in lines:
            if style == "h":
                st.append(Paragraph(clean(text), H3))
            elif style == "li":
                st.append(Paragraph(clean(text), LI, bulletText="•"))
            elif style == "pre":
                st.append(Paragraph(clean(text).replace("\n", "<br/>"), PRE))
            else:
                st.append(Paragraph(clean(text), B))
    st += [Spacer(0, 4 * mm), Paragraph(clean(DISCLAIMER), S)]
    doc.build(st)
    return buf.getvalue()
