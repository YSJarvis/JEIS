"use client";

import { useEffect, useState, useRef } from "react";
import { api } from "@/lib/api";
import type { CpBrief, CpConstituency, CpMeta, CpOutputRow } from "@/lib/types";
import { briefText, Cite, CitedText, CopyButton, ExportBar, Notes, Refs, SourcesList, useCiteFocus } from "./shared";

const FOCUS_IDEAS: Record<string, string[]> = {
  farmers: ["Cane payment delays", "Irrigation and canal water", "Mandi procurement"],
  youth: ["Local jobs and skilling", "Young people migrating for work"],
  women: ["Safety on unlit roads", "Ration delivery"],
  scheme_beneficiaries: ["Stopped PM-KISAN instalments", "Ration and pensions"],
};

export interface BriefPreset { event?: string; audience?: string; go?: boolean }

export default function BriefGenerator({ meta, place, preset }: { meta: CpMeta; place: CpConstituency; preset?: BriefPreset }) {
  const [event, setEvent] = useState(preset?.event && preset.event in meta.event_types ? preset.event : "rally_speech");
  const [audience, setAudience] = useState(preset?.audience && preset.audience in meta.audiences ? preset.audience : "farmers");
  const [language, setLanguage] = useState("en");
  const [party, setParty] = useState("");
  const [opponent, setOpponent] = useState("");
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [brief, setBrief] = useState<CpBrief | null>(null);
  const [history, setHistory] = useState<CpOutputRow[]>([]);
  const { active, cite } = useCiteFocus();

  useEffect(() => {
    api.cpOutputs("brief").then((h) => setHistory(h.filter((x) => x.constituency_id === place.id))).catch(() => setHistory([]));
  }, [place.id, brief]);

  // deep link from the Command Center ("Draft Rally Speech"): generate straight away, once
  const fired = useRef(false);
  useEffect(() => {
    if (!preset?.go || fired.current) return;
    fired.current = true;
    if (preset.event && preset.event in meta.event_types) setEvent(preset.event);
    if (preset.audience && preset.audience in meta.audiences) setAudience(preset.audience);
    generate(preset);
  }, [preset]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function generate(p?: BriefPreset) {
    setBusy(true); setError("");
    const ev = p?.event && p.event in meta.event_types ? p.event : event;
    const aud = p?.audience && p.audience in meta.audiences ? p.audience : audience;
    try {
      setBrief(await api.cpBrief({ constituency_id: place.id, event_type: ev, target_audience: aud, language,
                                   party_name: party, opponent, focus }));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="grid lg:grid-cols-[340px_minmax(0,1fr)] gap-6 items-start">
      <form className="no-print bg-panel border border-line rounded-lg p-4 lg:sticky lg:top-4 space-y-4"
            onSubmit={(e) => { e.preventDefault(); generate(); }}>
        <Choice label="What is it for?" value={event} onChange={setEvent} options={meta.event_types} />
        <Choice label="Who is listening?" value={audience} onChange={(v) => { setAudience(v); setFocus(""); }} options={meta.audiences} />
        <label className="block">
          <span className="text-[14px] font-medium">Language</span>
          <select value={language} onChange={(e) => setLanguage(e.target.value)}
                  className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]">
            {Object.entries(meta.languages).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          {language !== "en" && !meta.llm.live && <span className="block text-[12.5px] text-ink-3 mt-1">Hindi and Punjabi drafts need an AI provider; you’ll get English.</span>}
        </label>
        <label className="block">
          <span className="text-[14px] font-medium">Campaign focus <span className="font-normal text-ink-3">(optional)</span></span>
          <input value={focus} onChange={(e) => setFocus(e.target.value)} maxLength={200} placeholder="e.g. Cane payment delays"
                 className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]" />
          <span className="flex flex-wrap gap-1 mt-1.5">
            {(FOCUS_IDEAS[audience] ?? []).map((f) => (
              <button key={f} type="button" onClick={() => setFocus(f)}
                      className="px-2 py-0.5 rounded-full border border-line text-[12.5px] text-ink-2 hover:border-ink-3">{f}</button>
            ))}
          </span>
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="text-[14px] font-medium">Your party</span>
            <input value={party} onChange={(e) => setParty(e.target.value)} maxLength={60} placeholder="our party"
                   className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]" />
          </label>
          <label className="block">
            <span className="text-[14px] font-medium">Opponent</span>
            <input value={opponent} onChange={(e) => setOpponent(e.target.value)} maxLength={60} placeholder="the incumbent"
                   className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]" />
          </label>
        </div>
        <button disabled={busy} className="w-full h-11 rounded-md bg-violet text-white font-medium disabled:opacity-50">
          {busy ? "Drafting from sources…" : `Draft ${meta.event_types[event].toLowerCase()}`}
        </button>
        <p className="text-[12.5px] text-ink-3">Critique sticks to the documented record. Nothing appeals to caste or religion, and figures not found in a source are removed.</p>
        {error && <p role="alert" className="text-[14px] text-amber">{error}</p>}
        {history.length > 0 && (
          <div className="pt-3 border-t border-line">
            <p className="text-[13px] text-ink-3">Recent drafts</p>
            <ul className="mt-1 space-y-1">
              {history.slice(0, 5).map((h) => (
                <li key={h.output_id}>
                  <button type="button" onClick={() => api.cpOutput<CpBrief>(h.output_id).then(setBrief)}
                          className="text-left text-[13.5px] text-violet hover:underline">
                    {meta.event_types[String(h.params.event_type)] ?? "Brief"} · {meta.audiences[String(h.params.audience)] ?? ""}
                    <span className="text-ink-3"> · {new Date(h.created_at).toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </form>

      <div aria-live="polite">
        {!brief && !busy && (
          <div className="bg-panel border border-dashed border-line rounded-lg p-10 text-center text-ink-3">
            Choose the event and audience, then draft. Every claim links to the document it came from.
          </div>
        )}
        {busy && !brief && <div className="bg-panel border border-line rounded-lg p-10 text-center text-ink-3">Reading {place.name}’s documents…</div>}
        {brief && <BriefView brief={brief} onCite={cite} active={active} dim={busy} />}
      </div>
    </div>
  );
}

function Choice({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: Record<string, string> }) {
  return (
    <fieldset>
      <legend className="text-[14px] font-medium">{label}</legend>
      <div className="flex flex-wrap gap-1.5 mt-1.5">
        {Object.entries(options).map(([k, v]) => (
          <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)}
                  className={`px-2.5 py-1.5 rounded-md text-[13.5px] border ${value === k ? "border-ink bg-ink text-white" : "border-line text-ink-2 hover:border-ink-3"}`}>
            {v}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function Card({ title, copy, children }: { title: string; copy?: string; children: React.ReactNode }) {
  return (
    <section className="py-5 border-t border-line first:border-t-0">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold text-violet">{title}</h3>
        {copy && <CopyButton text={copy} />}
      </div>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function BriefView({ brief: b, onCite, active, dim }: { brief: CpBrief; onCite: (id: string) => void; active: string | null; dim: boolean }) {
  const english = b.generated_language === "en";
  return (
    <article className={`print-area bg-panel border border-line rounded-lg px-6 py-5 ${dim ? "opacity-60" : ""}`} lang={b.generated_language}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[13px] text-ink-3">{b.event_label} · {b.audience_label}{b.party_name ? ` · ${b.party_name}` : ""}</p>
          <h2 className="wide text-[26px] font-semibold leading-tight">{b.constituency.name}</h2>
        </div>
        <ExportBar id={b.output_id} english={english} text={briefText(b)} />
      </div>
      <div className="mt-3"><Notes warnings={b.warnings} generator={b.generator} live={b.live} /></div>

      <Card title="Executive summary" copy={b.executive_summary.text}>
        <p className="text-[16.5px] leading-relaxed"><CitedText text={b.executive_summary.text} onCite={onCite} /></p>
      </Card>

      <Card title="Key local grievances" copy={b.grievances.map((g, i) => `${i + 1}. ${g.title}: ${g.detail}`).join("\n")}>
        <ol className="grid md:grid-cols-3 gap-3">
          {b.grievances.map((g, i) => (
            <li key={i} className="rounded-md border border-line p-3 flex flex-col">
              <span className="text-[12px] text-ink-3">{i + 1}</span>
              <h4 className="text-[16px] font-semibold leading-snug">{g.title}</h4>
              {g.stats[0] && (
                <p className="mt-2">
                  <span className="narrow block text-[30px] font-semibold leading-none text-ink">{g.stats[0].figure}</span>
                  <span className="block text-[13px] text-ink-2 mt-1">{g.stats[0].context}<Cite id={g.stats[0].source} onCite={onCite} /></span>
                </p>
              )}
              <p className="text-[14px] text-ink-2 mt-2"><CitedText text={g.detail} onCite={onCite} /><Refs ids={g.sources} inline={g.detail} onCite={onCite} /></p>
              {g.stats.slice(1).map((s, j) => (
                <p key={j} className="text-[13px] text-ink-2 mt-1"><b>{s.figure}</b>: {s.context}<Cite id={s.source} onCite={onCite} /></p>
              ))}
            </li>
          ))}
        </ol>
      </Card>

      {b.opponent_critique.length > 0 && (
        <Card title="Record-based critique" copy={b.opponent_critique.map((c) => c.point).join("\n")}>
          <ul className="space-y-2">
            {b.opponent_critique.map((c, i) => (
              <li key={i} className="text-[15px] pl-3 border-l-2 border-ink-3"><CitedText text={c.point} onCite={onCite} /><Refs ids={c.sources} inline={c.point} onCite={onCite} /></li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Commitments" copy={b.commitments.map((c) => `${c.commitment}${c.delivery ? ` (${c.delivery})` : ""}`).join("\n")}>
        <ul className="divide-y divide-line">
          {b.commitments.map((c, i) => (
            <li key={i} className="py-2 flex gap-3">
              <span className={`shrink-0 mt-0.5 text-[11.5px] font-semibold px-1.5 py-0.5 rounded h-fit ${c.type === "party" ? "bg-ink text-white" : "bg-gotv-soft text-gotv"}`}>
                {c.type === "party" ? "Party" : "Local"}
              </span>
              <span className="text-[15px]">
                <CitedText text={c.commitment} onCite={onCite} /><Refs ids={c.sources} inline={c.commitment} onCite={onCite} />
                {c.delivery && <span className="block text-[13px] text-ink-3">{c.delivery}</span>}
              </span>
            </li>
          ))}
        </ul>
      </Card>

      <Card title={b.event_section.title} copy={b.event_section.items.map((x, i) => `${i + 1}. ${x.text}`).join("\n")}>
        <ol className="space-y-2 list-decimal pl-5 marker:text-ink-3">
          {b.event_section.items.map((it, i) => (
            <li key={i} className="text-[15.5px] leading-relaxed pl-1"><CitedText text={it.text} onCite={onCite} /><Refs ids={it.sources} inline={it.text} onCite={onCite} /></li>
          ))}
        </ol>
      </Card>

      <div className="pt-5 border-t border-line"><SourcesList sources={b.sources} active={active} /></div>
    </article>
  );
}
