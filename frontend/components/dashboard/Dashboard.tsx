"use client";

import { useCallback, useEffect, useState } from "react";
import CadreOps from "./CadreOps";
import CadrePanel from "./CadrePanel";
import PrivacyPanel, { CONSENT_LABEL } from "@/components/admin/PrivacyPanel";
import ScoringConfigModal from "@/components/admin/ScoringConfigModal";
import Nav from "@/components/Nav";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmt } from "@/lib/quadrants";
import type { Cluster, ConsentStatus, Dashboard as DashboardData, Meta, StateCode } from "@/lib/types";
import BoothTable from "./BoothTable";
import ClusterMap from "./ClusterMap";
import ImportPanel from "./ImportPanel";
import QuadrantField from "./QuadrantField";
import SegmentTable from "./SegmentTable";
import VoterTable from "./VoterTable";

type Panel = null | "import" | "weights" | "privacy";

export default function Dashboard() {
  const { hasRole } = useAuth();
  const isAdmin = hasRole("ADMIN");
  const [meta, setMeta] = useState<Meta | null>(null);
  const [state, setState] = useState<StateCode | null>(null);
  const [booth, setBooth] = useState<string>("");
  const [data, setData] = useState<DashboardData | null>(null);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [boothOptions, setBoothOptions] = useState<DashboardData["booths"]>([]);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel>(null);
  const [view, setView] = useState<"overview" | "ops">("overview");
  useEffect(() => { try { if (localStorage.getItem("ve.dashView") === "ops") setView("ops"); } catch { /* ignore */ } }, []);
  const chooseView = (v: "overview" | "ops") => { setView(v); try { localStorage.setItem("ve.dashView", v); } catch { /* ignore */ } };
  const [version, setVersion] = useState(0); // bump to refetch after import / re-score / privacy change
  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    api.meta().then((m) => {
      setMeta(m);
      setState((s) => (s && m.states.some((x) => x.code === s) ? s : m.states[0]?.code ?? null));
    }).catch((e) => setError(e.message));
  }, [version]);

  const load = useCallback(async () => {
    if (!state) return;
    setError("");
    try {
      const [d, c] = await Promise.all([api.dashboard(state, booth || undefined), api.clusters(state, booth || undefined)]);
      setData(d); setClusters(c);
      if (!booth) setBoothOptions(d.booths);
    } catch (e) {
      setError(`${(e as Error).message}. Is the API running on port 8000?`);
    }
  }, [state, booth]);
  useEffect(() => { load(); }, [load, version]);

  const t = data?.totals;
  const qn = data?.quadrants ?? {};
  const manager = meta?.role === "MANAGER";
  const stateName = meta?.states.find((s) => s.code === state)?.name;

  return (
    <div className="min-h-dvh">
      <Nav active="dashboard" right={isAdmin && (
        <>
          <button onClick={() => setPanel("weights")} className="px-3 py-1.5 rounded-md border border-line text-[15px] hover:border-ink-3">Scoring weights</button>
          <button onClick={() => setPanel("privacy")} className="px-3 py-1.5 rounded-md border border-line text-[15px] hover:border-ink-3">Privacy</button>
          <button onClick={() => setPanel("import")} className="px-3 py-1.5 rounded-md border border-line text-[15px] hover:border-ink-3">Import voter data</button>
        </>
      )} />

      <div className="max-w-[1440px] mx-auto px-5 pb-16">
        {manager ? (
          <div className="pt-6 pb-4 border-b border-line">
            <h1 className="wide text-[34px] font-semibold leading-none">{meta?.constituency}</h1>
            <p className="text-[14px] text-ink-3 mt-2">{stateName} · your constituency</p>
          </div>
        ) : (
          /* State switcher: the place names, set wide, are the page's headline */
          <div className="flex flex-wrap items-end gap-x-8 gap-y-2 pt-6 pb-4 border-b border-line" role="tablist" aria-label="State">
            {meta?.states.map((s) => {
              const on = s.code === state;
              return (
                <button key={s.code} role="tab" aria-selected={on}
                        onClick={() => { setState(s.code); setBooth(""); }}
                        className={`wide text-left leading-none pb-2 border-b-[3px] -mb-[17px] ${on ? "border-violet text-ink" : "border-transparent text-ink-3 hover:text-ink-2"}`}>
                  <span className={`block font-semibold ${on ? "text-[34px]" : "text-[22px]"}`}>{s.name}</span>
                  <span className="block text-[13px] font-normal text-ink-3 mt-1.5" style={{ fontStretch: "100%" }}>{fmt(s.voters)} voters</span>
                </button>
              );
            })}
          </div>
        )}

        <div className="flex gap-1 pt-4" role="tablist" aria-label="Dashboard view">
          {([["overview", "Overview"], ["ops", "Cadre operations"]] as const).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={view === k} onClick={() => chooseView(k)}
                    className={`px-3 py-1.5 rounded-md text-[15px] ${view === k ? "bg-ink text-white" : "text-ink-2 hover:bg-panel"}`}>{l}</button>
          ))}
        </div>

        {view === "ops" && state && <CadreOps key={state} state={state} isAdmin={isAdmin} />}
        {view === "overview" && (<>
        <div className="flex flex-wrap items-center gap-3 py-4">
          <label className="text-[14px] text-ink-2" htmlFor="booth">Booth</label>
          <select id="booth" value={booth} onChange={(e) => setBooth(e.target.value)}
                  className="h-9 px-2 rounded-md border border-line bg-panel text-[15px] min-w-64">
            <option value="">All booths in {manager ? meta?.constituency : stateName}</option>
            {boothOptions.map((b) => <option key={b.booth_id} value={b.booth_id}>{b.booth_id} · {b.name}</option>)}
          </select>
          {booth && <button onClick={() => setBooth("")} className="text-[14px] text-violet underline underline-offset-2">Show all booths</button>}
        </div>

        {error && <p role="alert" className="mb-4 px-4 py-3 rounded-md bg-violet-soft text-ink">{error}</p>}

        {/* Key figures as one ruled strip rather than a row of cards */}
        <dl className="grid grid-cols-2 md:grid-cols-6 border-y border-line bg-panel">
          {[
            ["Voters", fmt(t?.voters), `${fmt(t?.households)} households`],
            ["Avg swing", t?.avg_swing ?? "–", "50 = state average"],
            ["Avg turnout", t ? `${t.avg_turnout}%` : "–", `${fmt(t?.migrants)} migrant / absentee`],
            ["To persuade", fmt(qn.PERSUADE), "persuadable, likely voters"],
            ["To turn out", fmt(qn.GOTV), "supporters at risk of not voting"],
            ["Doors done", data ? `${fmt(data.field.done)} / ${fmt(data.field.planned)}` : "–", "on today’s routes"],
          ].map(([k, v, sub], i) => (
            <div key={k as string} className={`px-4 py-3 ${i ? "md:border-l border-line" : ""}`}>
              <dt className="text-[13px] text-ink-3">{k}</dt>
              <dd className="narrow text-[32px] font-semibold leading-tight">{v}</dd>
              <dd className="text-[12.5px] text-ink-3">{sub}</dd>
            </div>
          ))}
        </dl>

        <div className="grid lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] gap-5 mt-5">
          <section className="bg-panel border border-line rounded-lg p-4" aria-labelledby="qf">
            <h2 id="qf" className="text-[18px] font-semibold">Who to talk to</h2>
            <p className="text-[14px] text-ink-3 mb-2">Each dot is a voter, placed by swing propensity and turnout probability.</p>
            {data && meta && <QuadrantField points={data.scatter} counts={qn} thresholds={meta.thresholds} />}
          </section>
          <section className="bg-panel border border-line rounded-lg p-4 flex flex-col" aria-labelledby="map">
            <div className="flex items-baseline justify-between gap-3">
              <h2 id="map" className="text-[18px] font-semibold">Priority clusters</h2>
              <span className="text-[13px] text-ink-3">{clusters.length} clusters · ranked by combined household priority</span>
            </div>
            <div className="flex-1 min-h-[420px] mt-3 rounded-md overflow-hidden border border-line">
              {data && <ClusterMap clusters={clusters} booths={data.booths} />}
            </div>
          </section>
        </div>

        <div className="grid lg:grid-cols-[minmax(0,7fr)_minmax(0,4fr)] gap-5 mt-5">
          <section className="bg-panel border border-line rounded-lg" aria-labelledby="booths">
            <h2 id="booths" className="text-[18px] font-semibold px-4 pt-4">Booths by priority</h2>
            <p className="text-[14px] text-ink-3 px-4 mb-2">Priority weighs actionable voters, last margin and turnout headroom. Select a booth to focus.</p>
            {data && <BoothTable booths={data.booths} selected={booth} onSelect={setBooth} />}
          </section>
          <section className="bg-panel border border-line rounded-lg" aria-labelledby="seg">
            <h2 id="seg" className="text-[18px] font-semibold px-4 pt-4">Segments</h2>
            {data && <SegmentTable community={data.segments} schemes={data.schemes} />}
          </section>
        </div>

        <section className="bg-panel border border-line rounded-lg mt-5" aria-labelledby="cadres">
          <h2 id="cadres" className="text-[18px] font-semibold px-4 pt-4">Field cadres</h2>
          <p className="text-[14px] text-ink-3 px-4 mb-2">Who covers which booth, and how far they are through today’s doors.</p>
          {state && <CadrePanel key={`${state}-${version}`} state={state} booths={boothOptions} canEdit />}
        </section>

        <section className="bg-panel border border-line rounded-lg mt-5" aria-labelledby="voters">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 pt-4">
            <h2 id="voters" className="text-[18px] font-semibold">Priority voters</h2>
            {data && (
              <p className="text-[13px] text-ink-3">
                Consent: {(Object.keys(data.consent) as ConsentStatus[]).map((k) => `${fmt(data.consent[k])} ${CONSENT_LABEL[k].toLowerCase()}`).join(" · ")}
                {data.consent.OPT_OUT ? " (excluded)" : ""}
              </p>
            )}
          </div>
          {meta?.pii_masking && (
            <p className="mx-4 mt-2 px-3 py-2 rounded-md bg-paper text-[13.5px] text-ink-2">
              Names, phone numbers and house numbers are masked.{" "}
              {isAdmin ? <button onClick={() => setPanel("privacy")} className="text-violet underline underline-offset-2">Change in Privacy</button>
                : "Your strategist controls this setting."}
            </p>
          )}
          {state && <VoterTable state={state} booth={booth} masked={!!meta?.pii_masking} key={`${state}-${booth}-${version}`} />}
        </section>
        </>)}
      </div>

      {panel === "import" && <ImportPanel state={state ?? "UP"} onClose={() => setPanel(null)} onDone={refresh} />}
      {panel === "weights" && <ScoringConfigModal initialState={state ?? "UP"} onClose={() => setPanel(null)} onDone={refresh} />}
      {panel === "privacy" && <PrivacyPanel onClose={() => setPanel(null)} onChange={refresh} />}
    </div>
  );
}
