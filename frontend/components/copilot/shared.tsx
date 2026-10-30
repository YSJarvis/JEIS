"use client";

import { Fragment, useState } from "react";
import { api } from "@/lib/api";
import type { CpBrief, CpManifesto, CpSource } from "@/lib/types";

export const DOC_TYPE_LABEL: Record<string, string> = {
  budget: "Budget", legislative: "Assembly question", news: "News", survey: "Survey", party_promise: "Party promise", other: "Other",
};

/** Text with [S3]-style markers turned into citation buttons. */
export function CitedText({ text, onCite, className = "" }: { text: string; onCite: (id: string) => void; className?: string }) {
  const parts = text.split(/(\[S\d+\])/g);
  return (
    <span className={className}>
      {parts.map((p, i) => {
        const m = p.match(/^\[(S\d+)\]$/);
        return m ? <Cite key={i} id={m[1]} onCite={onCite} /> : <Fragment key={i}>{p}</Fragment>;
      })}
    </span>
  );
}

export function Cite({ id, onCite }: { id: string; onCite: (id: string) => void }) {
  return (
    <button type="button" onClick={() => onCite(id)} title={`Show source ${id}`}
            className="mx-0.5 px-1 rounded bg-violet-soft text-violet text-[11.5px] font-semibold align-[1px] hover:bg-violet hover:text-white">
      {id}
    </button>
  );
}

export function Refs({ ids, onCite, inline }: { ids: string[]; onCite: (id: string) => void; inline?: string }) {
  const extra = ids.filter((id) => !inline?.includes(`[${id}]`));
  return <>{extra.map((id) => <Cite key={id} id={id} onCite={onCite} />)}</>;
}

export function SourcesList({ sources, active }: { sources: CpSource[]; active: string | null }) {
  const [showAll, setShowAll] = useState(false);
  const list = showAll ? sources : sources.filter((s) => s.used);
  return (
    <section aria-labelledby="src-h">
      <div className="flex items-baseline justify-between gap-2">
        <h3 id="src-h" className="text-[16px] font-semibold">Sources</h3>
        {sources.some((s) => !s.used) && (
          <button onClick={() => setShowAll(!showAll)} className="no-print text-[13px] text-ink-3 underline underline-offset-2">
            {showAll ? "Only cited sources" : `Also show ${sources.filter((s) => !s.used).length} retrieved but unused`}
          </button>
        )}
      </div>
      <ol className="mt-2 space-y-2">
        {list.map((s) => (
          <li key={s.id} id={`src-${s.id}`}
              className={`rounded-md border px-3 py-2 transition-colors ${active === s.id ? "border-violet bg-violet-soft" : "border-line"}`}>
            <div className="flex items-baseline gap-2">
              <span className="text-[12px] font-semibold text-violet">{s.id}</span>
              <span className="text-[14px] font-medium flex-1">{s.title}</span>
              <span className="text-[12px] text-ink-3 whitespace-nowrap">{DOC_TYPE_LABEL[s.doc_type] ?? s.doc_type}{s.page ? ` · p. ${s.page}` : ""}</span>
            </div>
            <p className="text-[13px] text-ink-2 mt-1">“{s.snippet}”</p>
            <p className="text-[12px] text-ink-3 mt-1">{s.citation}{s.url && <> · <a href={s.url} target="_blank" rel="noreferrer" className="underline">link</a></>}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function useCiteFocus() {
  const [active, setActive] = useState<string | null>(null);
  const cite = (id: string) => {
    setActive(id);
    document.getElementById(`src-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => setActive((a) => (a === id ? null : a)), 2500);
  };
  return { active, cite };
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="no-print text-[13px] text-ink-3 hover:text-ink underline underline-offset-2"
            onClick={async () => {
              try { await navigator.clipboard.writeText(text); setDone(true); window.setTimeout(() => setDone(false), 1500); }
              catch { /* clipboard blocked */ }
            }}>
      {done ? "Copied" : label}
    </button>
  );
}

export function ExportBar({ id, english, text }: { id: string; english: boolean; text: string }) {
  return (
    <div className="no-print flex flex-wrap items-center gap-2">
      <a href={api.cpExportUrl(id, "pdf")} download aria-disabled={!english}
         onClick={(e) => { if (!english) e.preventDefault(); }}
         title={english ? "Download PDF" : "PDF export supports English. Use Print for Hindi or Punjabi."}
         className={`h-9 px-3 grid place-items-center rounded-md text-[14px] ${english ? "bg-violet text-white" : "bg-line text-ink-3 cursor-not-allowed"}`}>
        PDF
      </a>
      <a href={api.cpExportUrl(id, "md")} download className="h-9 px-3 grid place-items-center rounded-md border border-line text-[14px]">Markdown</a>
      <button onClick={() => window.print()} className="h-9 px-3 rounded-md border border-line text-[14px]">Print</button>
      <CopyAllButton text={text} />
    </div>
  );
}

function CopyAllButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="h-9 px-3 rounded-md border border-line text-[14px]"
            onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); window.setTimeout(() => setDone(false), 1500); } catch { /* blocked */ } }}>
      {done ? "Copied" : "Copy all"}
    </button>
  );
}

export function Notes({ warnings, generator, live }: { warnings: string[]; generator: string; live: boolean }) {
  return (
    <div className="no-print rounded-md bg-paper px-3 py-2 text-[13px] text-ink-2">
      <p>{live ? `Drafted by ${generator.split(":")[1] ?? generator}; every figure was checked against its source.` : "Built directly from the sources (no AI provider configured)."}</p>
      {warnings.filter((w) => !w.startsWith("Built directly")).map((w, i) => <p key={i} className="text-amber">{w}</p>)}
    </div>
  );
}

// ---------- plain-text versions for "Copy all" ----------

function sourcesText(sources: CpSource[]) {
  return sources.filter((s) => s.used).map((s) => `[${s.id}] ${s.citation}`).join("\n");
}

export function briefText(b: CpBrief): string {
  const lines = [`${b.event_label}: ${b.constituency.name} (${b.audience_label})`, "", "EXECUTIVE SUMMARY", b.executive_summary.text, "",
    "KEY LOCAL GRIEVANCES"];
  b.grievances.forEach((g, i) => {
    lines.push(`${i + 1}. ${g.title}: ${g.detail}`);
    g.stats.forEach((s) => lines.push(`   - ${s.figure}: ${s.context} [${s.source}]`));
  });
  if (b.opponent_critique.length) lines.push("", "RECORD-BASED CRITIQUE", ...b.opponent_critique.map((c) => `- ${c.point}`));
  lines.push("", "COMMITMENTS", ...b.commitments.map((c) => `- (${c.type}) ${c.commitment}${c.delivery ? ` | Delivery: ${c.delivery}` : ""}`));
  lines.push("", b.event_section.title.toUpperCase(), ...b.event_section.items.map((it, i) => `${i + 1}. ${it.text}`));
  lines.push("", "SOURCES", sourcesText(b.sources));
  return lines.join("\n");
}

export function manifestoText(m: CpManifesto): string {
  const lines = [m.title, "", m.preamble.text, ""];
  m.sections.forEach((s) => {
    lines.push(s.topic.toUpperCase(), `What we found: ${s.local_context}`);
    s.commitments.forEach((c) => lines.push(`- ${c.text}${c.delivery ? ` | How: ${c.delivery}` : ""}`));
    lines.push("");
  });
  if (m.closing) lines.push(m.closing, "");
  lines.push("SOURCES", sourcesText(m.sources));
  return lines.join("\n");
}
