"use client";

import type { WrCluster } from "@/lib/types";
import { STATE_SHORT, SeverityTag, Sparkline, ThreatScore, ago } from "./shared";

export default function NarrativeMonitor({ clusters, selected, onSelect, rising }: {
  clusters: WrCluster[] | null; selected: string | null; onSelect: (id: string) => void; rising: Record<string, number>;
}) {
  return (
    <section aria-labelledby="mon-h" className="no-print lg:sticky lg:top-4">
      <div className="flex items-baseline justify-between">
        <h2 id="mon-h" className="text-[16px] font-semibold">Active narratives</h2>
        <span className="text-[13px] text-ink-3">{clusters ? `${clusters.length} ranked by threat` : ""}</span>
      </div>
      <ol className="mt-2 space-y-2 lg:max-h-[calc(100dvh-210px)] lg:overflow-y-auto lg:pr-1">
        {clusters === null && [0, 1, 2].map((i) => <li key={i} className="h-[104px] rounded-lg bg-panel border border-line animate-pulse" />)}
        {clusters?.map((c) => {
          const on = c.cluster_id === selected;
          const mix = Object.entries(c.source_mix);
          return (
            <li key={c.cluster_id}>
              <button onClick={() => onSelect(c.cluster_id)} aria-current={on ? "true" : undefined}
                      className={`w-full text-left rounded-lg border bg-panel px-3 py-3 flex gap-3 transition-colors ${on ? "border-ink shadow-[inset_3px_0_0_var(--color-violet)]" : "border-line hover:border-ink-3"}`}>
                <ThreatScore score={c.threat_score} severity={c.severity} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-start justify-between gap-2">
                    <span className="text-[15.5px] font-semibold leading-snug">{c.label}</span>
                    {rising[c.cluster_id] ? (
                      <span className="shrink-0 text-[12px] font-medium text-amber whitespace-nowrap" title="Score rose since the last refresh">▲ {rising[c.cluster_id]}</span>
                    ) : null}
                  </span>
                  <span className="block text-[13px] text-ink-3 mt-0.5 truncate">
                    {c.state ? STATE_SHORT[c.state] : "National"}{c.constituencies.length ? ` · ${c.constituencies.slice(0, 3).join(", ")}` : ""}
                  </span>
                  <span className="mt-1.5 flex items-center justify-between gap-2 text-[12.5px] text-ink-2">
                    <SeverityTag severity={c.severity} />
                    <span><span className="narrow font-semibold">{c.items_24h}</span> in 24 h · <span className="narrow">{c.item_count}</span> total</span>
                  </span>
                  <span className="mt-1.5 block"><Sparkline values={c.trend} severity={c.severity} /></span>
                  <span className="mt-1 flex justify-between gap-2 text-[11.5px] text-ink-3">
                    <span className="truncate">{mix.map(([k, v]) => `${k} ${v}`).join(" · ")}</span>
                    <span className="whitespace-nowrap">{ago(c.last_seen)}</span>
                  </span>
                  {c.approved_responses > 0 && (
                    <span className="mt-1.5 inline-block text-[12px] px-1.5 py-0.5 rounded bg-gotv-soft text-gotv">Response approved</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
