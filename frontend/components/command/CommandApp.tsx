"use client";

import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmt, Q, Q_ORDER } from "@/lib/quadrants";
import type { CmdOverview, DispatchResult, Quadrant } from "@/lib/types";
import { SEV, STATE_NAME, STATE_SHORT, SeverityTag, ThreatScore, ago } from "@/components/warroom/shared";

const REFRESH_MS = 60000;

export default function CommandApp() {
  const { session } = useAuth();
  const manager = session?.user.role === "MANAGER";
  const [state, setState] = useState("");
  const [data, setData] = useState<CmdOverview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try { setData(await api.commandOverview(state || undefined)); setError(""); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, [state]);

  useEffect(() => { setLoading(true); load(); }, [load]);
  useEffect(() => {
    const id = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const k = data?.kpis;
  return (
    <div className="min-h-dvh">
      <Nav active="command" />
      <main className="max-w-[1440px] mx-auto px-4 sm:px-5 pb-16">
        <header className="flex flex-wrap items-end justify-between gap-4 pt-6 pb-4">
          <div>
            <p className="text-[12.5px] font-semibold tracking-wide text-violet uppercase">Jarvis Election Intelligence Suite</p>
            <h1 className="wide text-[32px] font-semibold leading-none mt-1">Command Center</h1>
            <p className="text-[14px] text-ink-3 mt-2">
              Voters, messaging and media in one view{data ? ` for ${data.area.label === "All states" ? "all four states" : data.area.label}` : ""}.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {data && <span className="text-[13px] text-ink-3" aria-live="polite">Updated {ago(data.generated_at)}</span>}
            <a href={api.briefingUrl(state || undefined)} download
               className="h-10 px-4 inline-flex items-center gap-2 rounded-md bg-violet text-white text-[14.5px] font-medium">
              <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M8 2v8m0 0L4.8 6.8M8 10l3.2-3.2M2.5 12.5v1h11v-1" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Executive briefing (PDF)
            </a>
          </div>
        </header>

        {!manager && (
          <div className="flex flex-wrap gap-1 border-b border-line pb-4" role="tablist" aria-label="State">
            {["", "UP", "PB", "UK", "GA"].map((s) => (
              <button key={s || "all"} role="tab" aria-selected={state === s} onClick={() => setState(s)}
                      className={`h-9 px-3.5 rounded-md text-[15px] whitespace-nowrap ${state === s ? "bg-ink text-white" : "text-ink-2 hover:bg-panel"}`}>
                {s ? STATE_NAME[s] : "All states"}
              </button>
            ))}
          </div>
        )}

        {error && <p role="alert" className="mt-4 px-3 py-2 rounded-md bg-violet-soft">{error}</p>}

        {/* headline numbers */}
        <section aria-label="Campaign overview" className={`grid grid-cols-2 lg:grid-cols-4 gap-3 mt-5 ${loading && data ? "opacity-60" : ""}`}>
          <Kpi product="VoterEngine AI" label="Active booths" value={k ? fmt(k.active_booths) : "–"}
               sub={k ? `${k.booths_covered_today} with cadres in the field today` : ""} href="/" />
          <Kpi product="VoterEngine AI" label="Priority voters to persuade" value={k ? fmt(k.persuade) : "–"}
               sub={k ? `${fmt(k.gotv)} supporters to turn out` : ""} href="/" accent="#5B2E91" />
          <Kpi product="JanVani Copilot" label="Generated local manifestos" value={k ? fmt(k.manifestos) : "–"}
               sub={k ? `${k.briefs} speech brief${k.briefs === 1 ? "" : "s"}` : ""} href="/copilot" />
          <Kpi product="CounterPulse AI" label="Active critical narrative threats" value={k ? fmt(k.critical_threats) : "–"}
               sub={k ? `${k.high_threats} high · ${k.active_narratives} active narratives` : ""} href="/warroom"
               accent={k?.critical_threats ? SEV.Critical.color : undefined} />
        </section>

        {data && <QuickActions data={data} state={state} onChanged={load} />}

        {data?.by_state && <StateTable rows={data.by_state} onPick={setState} />}

        {data && (
          <div className="grid xl:grid-cols-3 gap-4 mt-6 items-start">
            <VoterPanel data={data} />
            <CopilotPanel data={data} />
            <WarroomPanel data={data} />
          </div>
        )}
        {!data && !error && <div className="mt-6 h-64 rounded-lg bg-panel border border-line animate-pulse" />}
      </main>
    </div>
  );
}

function Kpi({ product, label, value, sub, href, accent }: {
  product: string; label: string; value: string; sub: string; href: string; accent?: string;
}) {
  return (
    <a href={href} className="group bg-panel border border-line rounded-lg px-4 pt-3 pb-4 hover:border-ink-3 relative overflow-hidden">
      {accent && <span aria-hidden className="absolute inset-x-0 top-0 h-1" style={{ background: accent }} />}
      <span className="block text-[11.5px] font-semibold uppercase tracking-wide text-ink-3">{product}</span>
      <span className="block narrow text-[40px] font-semibold leading-none mt-2" style={accent ? { color: accent } : undefined}>{value}</span>
      <span className="block text-[15px] font-medium mt-2 leading-snug">{label}</span>
      <span className="block text-[13px] text-ink-3 mt-0.5">{sub}</span>
    </a>
  );
}

function QuickActions({ data, state, onChanged }: { data: CmdOverview; state: string; onChanged: () => void }) {
  const a = data.actions;
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DispatchResult | null>(null);
  const [error, setError] = useState("");

  async function dispatch() {
    setBusy(true); setError("");
    try { setResult(await api.dispatchRoutes(state || undefined)); onChanged(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const speechHref = a.speech
    ? `/copilot?cid=${a.speech.constituency_id}&event=rally_speech&audience=${a.speech.audience}&go=1` : "/copilot";
  const threatHref = a.threat ? `/warroom?cluster=${a.threat.cluster_id}&medium=tv_brief&go=1` : "/warroom";

  return (
    <section aria-labelledby="qa-h" className="mt-6">
      <h2 id="qa-h" className="text-[16px] font-semibold">Quick actions</h2>
      <div className="grid md:grid-cols-3 gap-3 mt-2">
        <Action product="VoterEngine AI" title="Dispatch cadre routes"
                body={result
                  ? `Dispatched ${result.dispatched} route${result.dispatched === 1 ? "" : "s"} with ${fmt(result.stops)} doors. ${result.field.cadres_dispatched} of ${result.field.cadres} cadres are now on route.`
                  : a.dispatch.pending
                    ? `${a.dispatch.pending} of ${a.dispatch.cadres} cadres have no route for today. Each gets a walking route through their highest-priority doors.`
                    : `All ${a.dispatch.cadres} cadres have today’s route. ${data.voterengine.field.progress}% of doors are done.`}
                cta={busy ? "Dispatching…" : a.dispatch.pending ? `Dispatch ${a.dispatch.pending} route${a.dispatch.pending === 1 ? "" : "s"}` : "All dispatched"}
                onClick={dispatch} disabled={busy || !a.dispatch.pending} error={error} />
        <Action product="JanVani Copilot" title="Draft rally speech"
                body={a.speech
                  ? `${a.speech.name}, ${STATE_SHORT[a.speech.state]}: ${fmt(a.speech.persuadable)} persuadable voters. A rally speech for ${a.speech.audience_label.toLowerCase()}, sourced from local records.`
                  : "Add constituency documents in JanVani Copilot to draft speeches."}
                cta="Draft rally speech" href={speechHref} />
        <Action product="CounterPulse AI" title="Counter viral threat"
                body={a.threat
                  ? `“${a.threat.label}” in ${STATE_SHORT[a.threat.state] ?? "national media"}: ${a.threat.threat_score}/100, ${a.threat.severity.toLowerCase()}. ${
                      a.threat.response_status === "approved" ? "A response is approved; draft a TV brief for tonight." :
                      a.threat.has_response ? "A drafted response is waiting for approval." : "No response drafted yet."}`
                  : "No active narratives. Add media in CounterPulse AI."}
                cta="Counter viral threat" href={threatHref}
                severity={a.threat?.severity} />
      </div>
    </section>
  );
}

function Action({ product, title, body, cta, href, onClick, disabled, error, severity }: {
  product: string; title: string; body: string; cta: string; href?: string; onClick?: () => void; disabled?: boolean;
  error?: string; severity?: keyof typeof SEV;
}) {
  const btn = "mt-3 h-10 px-4 inline-flex items-center justify-center rounded-md text-[14.5px] font-medium";
  return (
    <div className="bg-panel border border-line rounded-lg p-4 flex flex-col">
      <span className="flex items-center justify-between gap-2">
        <span className="text-[11.5px] font-semibold uppercase tracking-wide text-ink-3">{product}</span>
        {severity && <SeverityTag severity={severity} />}
      </span>
      <span className="block text-[18px] font-semibold mt-1">{title}</span>
      <p className="text-[14px] text-ink-2 mt-1 flex-1" aria-live="polite">{body}</p>
      {href
        ? <a href={href} className={`${btn} bg-ink text-white self-start`}>{cta} →</a>
        : <button onClick={onClick} disabled={disabled} className={`${btn} bg-ink text-white self-start disabled:bg-line disabled:text-ink-3`}>{cta}</button>}
      {error && <p role="alert" className="text-[13px] text-amber mt-2">{error}</p>}
    </div>
  );
}

function StateTable({ rows, onPick }: { rows: NonNullable<CmdOverview["by_state"]>; onPick: (s: string) => void }) {
  return (
    <section aria-labelledby="st-h" className="mt-6">
      <h2 id="st-h" className="text-[16px] font-semibold">By state</h2>
      <div className="mt-2 bg-panel border border-line rounded-lg overflow-x-auto">
        <table className="w-full text-[14.5px] min-w-[720px]">
          <thead className="text-left text-[12.5px] text-ink-3 border-b border-line">
            <tr>
              {["State", "Active booths", "Covered today", "To persuade", "To turn out", "Manifestos", "Narratives", "Critical / high"].map((h, i) => (
                <th key={h} scope="col" className={`font-medium px-4 py-2 ${i ? "text-right" : ""}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((r) => (
              <tr key={r.state} className="hover:bg-paper">
                <th scope="row" className="px-4 py-2.5 text-left font-medium">
                  <button onClick={() => onPick(r.state)} className="hover:underline underline-offset-2">{r.name}</button>
                </th>
                <td className="px-4 py-2.5 text-right narrow">{r.active_booths}</td>
                <td className="px-4 py-2.5 text-right narrow">{r.booths_covered_today}</td>
                <td className="px-4 py-2.5 text-right narrow font-semibold text-violet">{fmt(r.persuade)}</td>
                <td className="px-4 py-2.5 text-right narrow text-gotv">{fmt(r.gotv)}</td>
                <td className="px-4 py-2.5 text-right narrow">{r.manifestos}</td>
                <td className="px-4 py-2.5 text-right narrow">{r.active_narratives}</td>
                <td className="px-4 py-2.5 text-right">
                  <span className="narrow font-semibold" style={{ color: r.critical_threats ? SEV.Critical.color : undefined }}>{r.critical_threats}</span>
                  <span className="text-ink-3"> / </span><span className="narrow">{r.high_threats}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Panel({ product, title, href, children }: { product: string; title: string; href: string; children: React.ReactNode }) {
  return (
    <section className="bg-panel border border-line rounded-lg p-4 min-w-0" aria-label={product}>
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <span className="block text-[11.5px] font-semibold uppercase tracking-wide text-violet">{product}</span>
          <h2 className="text-[17px] font-semibold">{title}</h2>
        </div>
        <a href={href} className="text-[13.5px] text-ink-2 underline underline-offset-2 whitespace-nowrap">Open {product.split(" ")[0]} →</a>
      </div>
      <div className="mt-3 space-y-4">{children}</div>
    </section>
  );
}

const Sub = ({ children }: { children: React.ReactNode }) => (
  <h3 className="text-[12.5px] font-semibold uppercase tracking-wide text-ink-3">{children}</h3>
);

function VoterPanel({ data }: { data: CmdOverview }) {
  const v = data.voterengine;
  const total = v.quadrants.reduce((s, q) => s + q.voters, 0) || 1;
  const f = v.field;
  return (
    <Panel product="VoterEngine AI" title="Who to reach, and where" href="/">
      <div>
        <Sub>Voter segments</Sub>
        <div className="mt-2 flex h-3 rounded-full overflow-hidden" role="img"
             aria-label={v.quadrants.map((q) => `${Q[q.quadrant as Quadrant]?.name ?? q.label} ${q.share}%`).join(", ")}>
          {Q_ORDER.map((key) => {
            const q = v.quadrants.find((x) => x.quadrant === key);
            return q ? <span key={key} style={{ width: `${(100 * q.voters) / total}%`, background: Q[key].color }} /> : null;
          })}
        </div>
        <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[13px]">
          {Q_ORDER.map((key) => {
            const q = v.quadrants.find((x) => x.quadrant === key);
            return (
              <li key={key} className="flex items-center gap-1.5 min-w-0">
                <span aria-hidden className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: Q[key].color }} />
                <span className="truncate">{Q[key].action}</span>
                <span className="ml-auto narrow text-ink-2">{fmt(q?.voters)}</span>
              </li>
            );
          })}
        </ul>
      </div>
      <div>
        <Sub>Field today</Sub>
        <div className="mt-2 flex items-baseline justify-between text-[14px]">
          <span><span className="narrow text-[22px] font-semibold">{f.progress}%</span> of {fmt(f.planned)} doors done</span>
          <span className="text-ink-3 text-[13px]">{f.cadres_dispatched}/{f.cadres} cadres on route</span>
        </div>
        <div className="mt-1.5 h-2 rounded-full bg-line overflow-hidden" role="progressbar" aria-valuenow={f.progress} aria-valuemin={0} aria-valuemax={100}
             aria-label="Doors done today">
          <span className="block h-full bg-gotv rounded-full" style={{ width: `${f.progress}%` }} />
        </div>
        <p className="text-[13px] text-ink-3 mt-1">{fmt(f.visited)} visited · {fmt(f.consents_granted_today)} consents granted at the door</p>
      </div>
      <div>
        <Sub>Priority booths</Sub>
        <ol className="mt-1 divide-y divide-line">
          {v.top_booths.map((b) => (
            <li key={b.booth_id} className="py-2 flex items-center gap-3 text-[14px]">
              <span className="narrow w-9 text-[17px] font-semibold text-violet">{Math.round(b.priority_score)}</span>
              <span className="min-w-0 flex-1">
                <span className="block font-medium truncate">{b.booth_id} · {b.constituency}</span>
                <span className="block text-[12.5px] text-ink-3 truncate">{b.focus} · {fmt(b.persuade)} to persuade · {fmt(b.gotv)} to turn out</span>
              </span>
              <span className="text-[12.5px] text-ink-3 whitespace-nowrap">{Math.round(b.historical_turnout)}% turnout</span>
            </li>
          ))}
        </ol>
      </div>
    </Panel>
  );
}

function CopilotPanel({ data }: { data: CmdOverview }) {
  const c = data.copilot;
  return (
    <Panel product="JanVani Copilot" title="What voters are telling us" href="/copilot">
      <div>
        <Sub>Top local grievances</Sub>
        {c.grievances.length ? (
          <ul className="mt-1 divide-y divide-line">
            {c.grievances.slice(0, 6).map((g, i) => (
              <li key={i} className="py-2 text-[14px]">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="font-medium">{g.title}</span>
                  {g.figure && <span className="narrow font-semibold text-violet whitespace-nowrap">{g.figure}</span>}
                </span>
                <span className="block text-[12.5px] text-ink-3 truncate" title={g.source}>{g.constituency} · {g.source.replace(/^SAMPLE:\s*/, "Sample: ")}</span>
              </li>
            ))}
          </ul>
        ) : <p className="text-[14px] text-ink-3 mt-1">No speech briefs yet. Draft one to put local grievances on record.</p>}
      </div>
      {c.promises.length > 0 && (
        <div>
          <Sub>Key campaign promises</Sub>
          <ul className="mt-1 space-y-1.5">
            {c.promises.slice(0, 3).map((p, i) => (
              <li key={i} className="text-[14px] leading-snug flex gap-2">
                <span aria-hidden className="mt-2 w-1.5 h-1.5 rounded-full bg-ink shrink-0" />
                <span>{p.text} <span className="text-[12px] text-ink-3">({p.scope})</span></span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <Sub>Latest drafts</Sub>
        <ul className="mt-1 text-[13.5px] space-y-1">
          {c.recent.map((r) => (
            <li key={r.output_id} className="flex justify-between gap-2">
              <span className="truncate">{r.kind === "manifesto" ? "Local manifesto" : r.event ?? "Brief"} · {r.place}</span>
              <span className="text-ink-3 whitespace-nowrap">{ago(r.created_at)}</span>
            </li>
          ))}
          {!c.recent.length && <li className="text-ink-3">Nothing drafted yet.</li>}
        </ul>
      </div>
    </Panel>
  );
}

function WarroomPanel({ data }: { data: CmdOverview }) {
  const w = data.warroom;
  const s = w.sentiment;
  const pct = (n: number) => (s.items ? (100 * n) / s.items : 0);
  return (
    <Panel product="CounterPulse AI" title="What the media is saying" href="/warroom">
      <div>
        <Sub>Narrative threats</Sub>
        <ol className="mt-1 divide-y divide-line">
          {w.narratives.slice(0, 5).map((n) => (
            <li key={n.cluster_id}>
              <a href={`/warroom?cluster=${n.cluster_id}`} className="py-2 flex items-center gap-3 hover:bg-paper -mx-1 px-1 rounded">
                <ThreatScore score={n.threat_score} severity={n.severity} size="sm" />
                <span className="min-w-0 flex-1">
                  <span className="block text-[14px] font-medium leading-snug">{n.label}</span>
                  <span className="block text-[12.5px] text-ink-3 truncate">
                    {n.state ? STATE_SHORT[n.state] : "National"} · {n.items_24h} in 24 h ·{" "}
                    {n.response ? (n.response.status === "approved"
                      ? <span className="text-gotv">{n.response.medium_label} approved</span>
                      : <span className="text-amber">response awaiting approval</span>)
                      : "no response yet"}
                  </span>
                </span>
              </a>
            </li>
          ))}
          {!w.narratives.length && <li className="py-2 text-[14px] text-ink-3">No active narratives.</li>}
        </ol>
      </div>
      <div>
        <Sub>Media sentiment, last {s.days} days</Sub>
        <p className="text-[14px] mt-1"><span className="narrow text-[22px] font-semibold text-amber">{s.negative_share}%</span> negative across {fmt(s.items)} items</p>
        <div className="mt-1.5 flex h-2 rounded-full overflow-hidden bg-line" role="img"
             aria-label={`${s.negative} negative, ${s.neutral} neutral, ${s.positive} positive`}>
          <span style={{ width: `${pct(s.negative)}%`, background: "#A1620C" }} />
          <span style={{ width: `${pct(s.neutral)}%`, background: "#A8A3B8" }} />
          <span style={{ width: `${pct(s.positive)}%`, background: "#0F766E" }} />
        </div>
        <p className="text-[12.5px] text-ink-3 mt-1">{s.by_source.map((b) => `${b.source} ${b.negative + b.neutral + b.positive}`).join(" · ")}</p>
      </div>
      {w.pending_approvals > 0 && (
        <p className="text-[14px] rounded-md bg-violet-soft px-3 py-2">
          {w.pending_approvals} counter-brief{w.pending_approvals === 1 ? " is" : "s are"} waiting for approval.
        </p>
      )}
    </Panel>
  );
}
