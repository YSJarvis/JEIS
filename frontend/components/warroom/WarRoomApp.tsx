"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Nav from "@/components/Nav";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { Severity, WrCluster, WrMeta } from "@/lib/types";
import AddMedia from "./AddMedia";
import MediaLog from "./MediaLog";
import NarrativeMonitor from "./NarrativeMonitor";
import Workbench from "./Workbench";
import { SEV, SEV_ORDER, STATE_SHORT, ago } from "./shared";

const POLL_MS = 20000;
type View = "monitor" | "log";

export default function WarRoomApp() {
  const { session } = useAuth();
  const isAdmin = session?.user.role === "ADMIN";
  const [meta, setMeta] = useState<WrMeta | null>(null);
  const [clusters, setClusters] = useState<WrCluster[] | null>(null);
  const [state, setState] = useState("");
  const [sev, setSev] = useState<Severity[]>([]);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<View>("monitor");
  const [live, setLive] = useState(true);
  const [updated, setUpdated] = useState<number | null>(null);
  const [, tick] = useState(0);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [version, setVersion] = useState(0);       // bumps when data changes so children reload
  const prevScores = useRef<Record<string, number>>({});
  const [rising, setRising] = useState<Record<string, number>>({});
  const [preset, setPreset] = useState<{ cluster: string; medium?: string; go: boolean } | null>(null);

  // deep link from the Command Center ("Counter Viral Threat"): /warroom?cluster=…&medium=tv_brief&go=1
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const cluster = q.get("cluster");
    if (cluster) {
      setSelected(cluster);
      setPreset({ cluster, medium: q.get("medium") ?? undefined, go: q.get("go") === "1" });
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const [m, t] = await Promise.all([api.wrMeta(), api.wrThreats({ state, severity: sev.join(","), q })]);
      const up: Record<string, number> = {};
      for (const c of t.clusters) {
        const before = prevScores.current[c.cluster_id];
        if (before !== undefined && c.threat_score > before) up[c.cluster_id] = c.threat_score - before;
        prevScores.current[c.cluster_id] = c.threat_score;
      }
      setRising(up);
      setMeta(m); setClusters(t.clusters); setUpdated(Date.now()); setError("");
      setSelected((s) => (s && t.clusters.some((c) => c.cluster_id === s) ? s : t.clusters[0]?.cluster_id ?? null));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [state, sev, q]);

  useEffect(() => { load(); }, [load, version]);
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [live, load]);
  useEffect(() => { const id = window.setInterval(() => tick((n) => n + 1), 10000); return () => window.clearInterval(id); }, []);

  const changed = () => setVersion((v) => v + 1);
  const selectedCluster = clusters?.find((c) => c.cluster_id === selected) ?? null;

  async function simulate() {
    try { await api.wrSample({ simulate: 3 }); changed(); }
    catch (e) { setError((e as Error).message); }
  }

  return (
    <div className="min-h-dvh">
      <div className="no-print"><Nav active="warroom" /></div>
      <div className="max-w-[1440px] mx-auto px-5 pb-16">
        <header className="no-print flex flex-wrap items-end justify-between gap-4 pt-6 pb-4 border-b border-line">
          <div>
            <h1 className="wide text-[32px] font-semibold leading-none">CounterPulse AI</h1>
            <p className="text-[14px] text-ink-3 mt-2">Narratives spreading across TV, print, YouTube and social media, ranked by threat, with fact-checked responses ready to review.</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-[13px] text-ink-3 leading-snug text-right" aria-live="polite">
              <button onClick={() => setLive(!live)} className="inline-flex items-center gap-1.5 text-ink-2 hover:text-ink"
                      aria-pressed={live} title={live ? "Pause auto-refresh" : "Resume auto-refresh"}>
                <span aria-hidden className={`w-2 h-2 rounded-full ${live ? "bg-gotv animate-pulse" : "bg-ink-3"}`} />
                {live ? "Live" : "Paused"}
              </button>
              <span className="block">Updated {ago(updated ? new Date(updated).toISOString() : null)}{meta?.media.last_ingested_at ? ` · last item ${ago(meta.media.last_ingested_at)}` : ""}</span>
            </p>
            {meta && (
              <p className="text-[13px] text-ink-3 leading-snug text-right pl-3 border-l border-line">
                <span className={meta.llm.live ? "text-gotv" : "text-ink-2"}>{meta.llm.live ? "AI drafting on" : "Offline drafting"}</span>
                <span className="block">{meta.media.items.toLocaleString("en-IN")} media items</span>
              </p>
            )}
            {meta?.can_simulate && (
              <button onClick={simulate} className="h-9 px-3 rounded-md border border-line bg-panel text-[14px]"
                      title="Adds three synthetic items so you can watch scores move">Simulate feed</button>
            )}
            <button onClick={() => setAdding(true)} className="h-9 px-3.5 rounded-md bg-violet text-white text-[14px] font-medium">Add media</button>
          </div>
        </header>

        {meta && meta.media.sample_items > 0 && (
          <p className="no-print mt-3 text-[13px] px-3 py-2 rounded-md bg-violet-soft text-ink-2">
            Showing a synthetic sample feed (channels marked “sample”). {isAdmin ? "Remove it from Add media before monitoring real coverage." : ""}
          </p>
        )}

        {/* filters shared by the monitor and the log */}
        <div className="no-print flex flex-wrap items-center gap-x-5 gap-y-3 pt-4">
          <div className="flex gap-1" role="tablist" aria-label="View">
            {([["monitor", "Narrative monitor"], ["log", "Media log"]] as const).map(([k, l]) => (
              <button key={k} role="tab" aria-selected={view === k} onClick={() => setView(k)}
                      className={`px-3 py-1.5 rounded-md text-[15px] ${view === k ? "bg-ink text-white" : "text-ink-2 hover:bg-panel"}`}>{l}</button>
            ))}
          </div>
          <fieldset className="flex flex-wrap items-center gap-1">
            <legend className="sr-only">State</legend>
            {["", "UP", "PB", "UK", "GA"].map((s) => (
              <button key={s || "all"} aria-pressed={state === s} onClick={() => setState(s)}
                      className={`h-8 px-2.5 rounded-md text-[14px] border whitespace-nowrap ${state === s ? "border-ink bg-ink text-white" : "border-line bg-panel text-ink-2"}`}>
                {s ? STATE_SHORT[s] : "All states"}
              </button>
            ))}
          </fieldset>
          <fieldset className="flex flex-wrap items-center gap-1">
            <legend className="sr-only">Severity</legend>
            {SEV_ORDER.map((s) => {
              const on = sev.includes(s);
              const n = meta?.active_by_severity[s] ?? 0;
              return (
                <button key={s} aria-pressed={on} onClick={() => setSev(on ? sev.filter((x) => x !== s) : [...sev, s])}
                        className={`h-8 pl-2 pr-2.5 rounded-md text-[14px] border inline-flex items-center gap-1.5 ${on ? "border-ink bg-panel shadow-[inset_0_0_0_1px_var(--color-ink)]" : "border-line bg-panel text-ink-2"}`}>
                  <span aria-hidden className="w-2.5 h-2.5 rounded-sm" style={{ background: SEV[s].color }} />
                  {s}<span className="narrow text-ink-3">{n}</span>
                </button>
              );
            })}
          </fieldset>
          {view === "monitor" && (
            <label className="lg:ml-auto w-full sm:w-auto">
              <span className="sr-only">Search narratives</span>
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search narratives"
                     className="h-8 w-full sm:w-56 px-2.5 rounded-md border border-line bg-panel text-[14px]" />
            </label>
          )}
        </div>

        {error && <p role="alert" className="mt-4 px-3 py-2 rounded-md bg-violet-soft">{error}</p>}

        {view === "monitor" && (
          <div className="grid lg:grid-cols-[360px_minmax(0,1fr)] gap-5 pt-4 items-start">
            <NarrativeMonitor clusters={clusters} selected={selected} onSelect={(id) => { setPreset(null); setSelected(id); }} rising={rising} />
            {selectedCluster && meta
              ? <Workbench key={selectedCluster.cluster_id} cluster={selectedCluster} meta={meta} isAdmin={isAdmin} version={version} onChange={changed}
                           preset={preset?.cluster === selectedCluster.cluster_id ? preset : undefined} />
              : <div className="bg-panel border border-dashed border-line rounded-lg p-10 text-center text-ink-3">
                  {clusters?.length === 0 ? "No active narratives match these filters." : "Loading…"}
                </div>}
          </div>
        )}
        {view === "log" && meta && (
          <MediaLog meta={meta} state={state} severity={sev} version={version}
                    onOpen={(cid) => { setPreset(null); setSelected(cid); setView("monitor"); }} />
        )}
      </div>
      {adding && meta && <AddMedia meta={meta} isAdmin={isAdmin} onClose={() => setAdding(false)} onDone={changed} />}
    </div>
  );
}
