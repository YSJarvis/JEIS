"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Severity, WrMediaItem, WrMeta } from "@/lib/types";
import { NegativityBar, SOURCE_SHORT, STATE_SHORT, ThreatScore, ago } from "./shared";

const PAGE = 50;
const WINDOWS: [string, string][] = [["", "Any time"], ["6", "Last 6 h"], ["24", "Last 24 h"], ["72", "Last 72 h"], ["168", "Last 7 days"]];

export default function MediaLog({ meta, state, severity, version, onOpen }: {
  meta: WrMeta; state: string; severity: Severity[]; version: number; onOpen: (clusterId: string) => void;
}) {
  const [source, setSource] = useState("");
  const [sentiment, setSentiment] = useState("");
  const [since, setSince] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; items: WrMediaItem[] } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");

  const params = { state, severity: severity.join(","), source_type: source, sentiment, since_hours: since, q };
  const key = JSON.stringify(params);
  useEffect(() => { setOffset(0); }, [key]);
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.wrMedia({ ...params, limit: PAGE, offset }).then((d) => { setData(d); setError(""); }).catch((e) => setError(e.message));
    }, 200);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, offset, version]);

  const sel = "h-8 px-2 rounded-md border border-line bg-panel text-[14px]";
  return (
    <section aria-labelledby="log-h" className="pt-4">
      <div className="flex flex-wrap items-end gap-2">
        <h2 id="log-h" className="text-[16px] font-semibold mr-auto">Media log
          <span className="ml-2 text-[13px] font-normal text-ink-3">{data ? `${data.total.toLocaleString("en-IN")} items` : ""}</span></h2>
        <label><span className="sr-only">Source type</span>
          <select value={source} onChange={(e) => setSource(e.target.value)} className={sel}>
            <option value="">All sources</option>
            {Object.entries(meta.source_types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label>
        <label><span className="sr-only">Sentiment</span>
          <select value={sentiment} onChange={(e) => setSentiment(e.target.value)} className={sel}>
            <option value="">Any sentiment</option><option value="negative">Negative</option>
            <option value="neutral">Neutral</option><option value="positive">Positive</option>
          </select></label>
        <label><span className="sr-only">Time window</span>
          <select value={since} onChange={(e) => setSince(e.target.value)} className={sel}>
            {WINDOWS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label>
        <label><span className="sr-only">Search text or channel</span>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search text or channel"
                 className="h-8 w-52 px-2.5 rounded-md border border-line bg-panel text-[14px]" /></label>
        <a href={api.wrMediaCsvUrl(params)} download className="h-8 px-3 grid place-items-center rounded-md bg-ink text-white text-[14px]">Export CSV</a>
      </div>
      {error && <p role="alert" className="mt-3 px-3 py-2 rounded-md bg-violet-soft text-[14px]">{error}</p>}

      <div className="mt-3 bg-panel border border-line rounded-lg overflow-x-auto">
        <table className="w-full text-[14px] min-w-[900px]">
          <thead className="text-left text-[12.5px] text-ink-3 border-b border-line">
            <tr>
              <th scope="col" className="font-medium px-3 py-2 w-[92px]">When</th>
              <th scope="col" className="font-medium px-3 py-2 w-[190px]">Source</th>
              <th scope="col" className="font-medium px-3 py-2 w-[120px]">Place</th>
              <th scope="col" className="font-medium px-3 py-2">Coverage</th>
              <th scope="col" className="font-medium px-3 py-2 w-[90px]">Negativity</th>
              <th scope="col" className="font-medium px-3 py-2 w-[230px]">Narrative and threat</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {data?.items.map((it) => {
              const expanded = open === it.item_id;
              return (
                <tr key={it.item_id} className="align-top">
                  <td className="px-3 py-2 text-ink-3 whitespace-nowrap" title={new Date(it.published_at).toLocaleString("en-IN")}>{ago(it.published_at)}</td>
                  <td className="px-3 py-2">
                    <span className="block font-medium">{SOURCE_SHORT[it.source_type] ?? it.source_type}</span>
                    <span className="block text-[13px] text-ink-3 truncate max-w-[180px]" title={it.channel}>{it.channel}</span>
                    {it.reach ? <span className="block text-[12px] text-ink-3">reach {Math.round(it.reach).toLocaleString("en-IN")}</span> : null}
                  </td>
                  <td className="px-3 py-2 text-[13.5px]">{it.constituency ?? "–"}<span className="block text-[12px] text-ink-3">{it.state ? STATE_SHORT[it.state] : ""}</span></td>
                  <td className="px-3 py-2">
                    <button onClick={() => setOpen(expanded ? null : it.item_id)} aria-expanded={expanded}
                            className={`text-left leading-snug ${expanded ? "" : "line-clamp-2"}`} lang={it.language}>{it.text}</button>
                    {expanded && it.url && <a href={it.url} target="_blank" rel="noreferrer" className="text-[13px] underline">Open original</a>}
                  </td>
                  <td className="px-3 py-2"><NegativityBar value={it.negativity} /></td>
                  <td className="px-3 py-2">
                    {it.cluster_id && it.severity ? (
                      <button onClick={() => onOpen(it.cluster_id!)} className="flex items-center gap-2 text-left group">
                        <ThreatScore score={it.threat_score ?? 0} severity={it.severity} size="sm" />
                        <span className="text-[13.5px] leading-snug group-hover:underline">{it.cluster_label}</span>
                      </button>
                    ) : <span className="text-ink-3">–</span>}
                  </td>
                </tr>
              );
            })}
            {data && !data.items.length && <tr><td colSpan={6} className="px-3 py-8 text-center text-ink-3">No items match these filters.</td></tr>}
            {!data && <tr><td colSpan={6} className="px-3 py-8 text-center text-ink-3">Loading…</td></tr>}
          </tbody>
        </table>
      </div>
      {data && data.total > PAGE && (
        <div className="mt-3 flex items-center justify-end gap-2 text-[14px]">
          <span className="text-ink-3">{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total.toLocaleString("en-IN")}</span>
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="h-8 px-3 rounded-md border border-line disabled:opacity-40">Previous</button>
          <button disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)} className="h-8 px-3 rounded-md border border-line disabled:opacity-40">Next</button>
        </div>
      )}
    </section>
  );
}
