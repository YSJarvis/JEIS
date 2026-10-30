"use client";

import { useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { api } from "@/lib/api";
import { fmt } from "@/lib/quadrants";
import type { CpMeta } from "@/lib/types";
import BriefGenerator, { type BriefPreset } from "./BriefGenerator";
import KnowledgeManager from "./KnowledgeManager";
import ManifestoGenerator from "./ManifestoGenerator";

type Tab = "brief" | "manifesto" | "kb";
const TABS: [Tab, string][] = [["brief", "Speeches and briefings"], ["manifesto", "Micro-manifesto"], ["kb", "Knowledge base"]];
const STATE_NAME: Record<string, string> = { UP: "Uttar Pradesh", PB: "Punjab", UK: "Uttarakhand", GA: "Goa" };

export default function CopilotApp() {
  const [meta, setMeta] = useState<CpMeta | null>(null);
  const [cid, setCid] = useState("");
  const [tab, setTab] = useState<Tab>("brief");
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const [preset, setPreset] = useState<BriefPreset | undefined>(undefined);

  // deep links: /copilot?cid=UP-SAR&event=rally_speech&audience=farmers&go=1
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("cid")) setCid(q.get("cid")!);
    if (q.get("tab") === "manifesto" || q.get("tab") === "kb") setTab(q.get("tab") as Tab);
    if (q.get("event") || q.get("audience") || q.get("go")) {
      setPreset({ event: q.get("event") ?? undefined, audience: q.get("audience") ?? undefined, go: q.get("go") === "1" });
    }
    if (q.toString()) window.history.replaceState(null, "", window.location.pathname);   // a refresh shouldn't regenerate
  }, []);

  useEffect(() => {
    api.cpMeta().then((m) => {
      setMeta(m);
      setCid((c) => (c && m.constituencies.some((x) => x.id === c) ? c : m.constituencies[0]?.id || ""));
    }).catch((e) => setError(e.message));
  }, [version]);

  const place = meta?.constituencies.find((c) => c.id === cid);
  const single = (meta?.constituencies.length ?? 0) <= 1;

  return (
    <div className="min-h-dvh">
      <div className="no-print"><Nav active="copilot" /></div>
      <div className="max-w-[1320px] mx-auto px-5 pb-16">
        <header className="no-print flex flex-wrap items-end justify-between gap-4 pt-6 pb-4 border-b border-line">
          <div>
            <h1 className="wide text-[32px] font-semibold leading-none">JanVani Copilot</h1>
            <p className="text-[14px] text-ink-3 mt-2">Speeches, briefings and local manifestos drafted from your constituency’s own documents, with a source for every figure.</p>
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <label className="block">
              <span className="block text-[13px] text-ink-3">Constituency</span>
              {single ? (
                <span className="block text-[17px] font-semibold mt-1">{place ? `${place.name}, ${STATE_NAME[place.state]}` : "–"}</span>
              ) : (
                <select value={cid} onChange={(e) => { setPreset(undefined); setCid(e.target.value); }}
                        className="mt-1 h-9 px-2 rounded-md border border-line bg-panel text-[15px] min-w-56">
                  {Object.entries(groupBy(meta?.constituencies ?? [])).map(([st, cs]) => (
                    <optgroup key={st} label={STATE_NAME[st] ?? st}>
                      {cs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </optgroup>
                  ))}
                </select>
              )}
            </label>
            {meta && (
              <p className="text-[13px] text-ink-3 leading-snug text-right">
                <span className={meta.llm.live ? "text-gotv" : "text-ink-2"}>{meta.llm.live ? "AI drafting on" : "Offline drafting"}</span>
                <span className="block">{fmt(meta.stats.documents)} documents · {meta.search}</span>
              </p>
            )}
          </div>
        </header>

        <div className="no-print flex gap-1 pt-4" role="tablist">
          {TABS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                    className={`px-3 py-1.5 rounded-md text-[15px] ${tab === k ? "bg-ink text-white" : "text-ink-2 hover:bg-panel"}`}>{l}</button>
          ))}
        </div>

        {error && <p role="alert" className="mt-4 px-3 py-2 rounded-md bg-violet-soft">{error}</p>}
        {meta && place && (
          <div className="pt-4">
            {tab === "brief" && <BriefGenerator key={`b-${cid}`} meta={meta} place={place} preset={preset} />}
            {tab === "manifesto" && <ManifestoGenerator key={`m-${cid}`} meta={meta} place={place} />}
            {tab === "kb" && <KnowledgeManager key={`k-${cid}`} meta={meta} place={place} onChange={() => setVersion((v) => v + 1)} />}
          </div>
        )}
        {meta && !place && <p className="mt-6 text-ink-3">No constituency is assigned to your account.</p>}
      </div>
    </div>
  );
}

function groupBy<T extends { state: string }>(xs: T[]): Record<string, T[]> {
  return xs.reduce((acc, x) => { (acc[x.state] ??= []).push(x); return acc; }, {} as Record<string, T[]>);
}
