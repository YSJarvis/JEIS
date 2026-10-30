"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmt } from "@/lib/quadrants";
import type { OpsSummary } from "@/lib/types";

const REFRESH_MS = 30_000;
const STATUS_TONE: Record<string, string> = {
  "On route": "text-gotv", Done: "text-gotv", "Out of contact": "text-amber", "Not started": "text-ink-3", "No route": "text-ink-3",
};

function since(m: number | null): string {
  if (m === null) return "Never";
  if (m < 1) return "Just now";
  if (m < 60) return `${m} min ago`;
  if (m < 24 * 60) return `${Math.floor(m / 60)} h ago`;
  return "Over a day";
}

/** Manager / strategist view of today's field work. Auto-refreshes while visible. */
export default function CadreOps({ state, isAdmin }: { state: string; isAdmin: boolean }) {
  const [data, setData] = useState<OpsSummary | null>(null);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try { setData(await api.opsSummary(isAdmin ? state : undefined)); setError(""); }
    catch (e) { setError((e as Error).message); }
  }, [state, isAdmin]);

  useEffect(() => {
    load();
    const t = setInterval(() => { if (document.visibilityState === "visible") load(); }, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  async function addDirective() {
    if (!text.trim()) return;
    setSaving(true);
    try { await api.addDirective(text.trim(), isAdmin ? state : undefined); setText(""); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setSaving(false); }
  }
  async function removeDirective(id: number) {
    try { await api.removeDirective(id); await load(); } catch (e) { setError((e as Error).message); }
  }

  if (!data) return <p className="py-8 text-ink-3">{error || "Loading today’s field work…"}</p>;
  const t = data.totals;
  const pct = t.targeted ? Math.round((t.completed / t.targeted) * 100) : 0;
  const unsynced = t.stale_pending + t.reported_unsynced;
  const maxHour = Math.max(1, ...data.hourly.map((h) => h.completed));
  const day = new Date(`${data.date}T00:00:00`).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  const updated = new Date(data.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

  return (
    <div className="pb-4">
      <div className="flex flex-wrap items-end justify-between gap-3 py-4">
        <div>
          <h2 className="text-[20px] font-semibold">Today in {data.scope}</h2>
          <p className="text-[13.5px] text-ink-3">{day} · updated {updated}, refreshes every 30 seconds</p>
        </div>
        <div className="flex gap-2">
          <a href={api.opsExportUrl("pdf", isAdmin ? state : undefined)} download
             className="h-9 px-3 grid place-items-center rounded-md bg-violet text-white text-[14.5px]">Download daily booth summary (PDF)</a>
          <a href={api.opsExportUrl("csv", isAdmin ? state : undefined)} download
             className="h-9 px-3 grid place-items-center rounded-md border border-line text-[14.5px]">CSV</a>
        </div>
      </div>
      {error && <p role="alert" className="mb-3 px-3 py-2 rounded-md bg-violet-soft text-[14px]">{error}</p>}

      <dl className="grid grid-cols-2 md:grid-cols-5 border-y border-line bg-panel">
        <div className="px-4 py-3 col-span-2 md:col-span-1">
          <dt className="text-[13px] text-ink-3">Doors completed</dt>
          <dd className="narrow text-[32px] font-semibold leading-tight">{fmt(t.completed)}<span className="text-ink-3 text-[20px]"> / {fmt(t.targeted)}</span></dd>
          <dd className="mt-1 h-1.5 rounded-full bg-paper overflow-hidden" aria-hidden><div className="h-full bg-gotv" style={{ width: `${pct}%` }} /></dd>
          <dd className="text-[12.5px] text-ink-3 mt-1">{pct}% of today’s target</dd>
        </div>
        {[
          ["Still to visit", fmt(t.remaining), `${fmt(t.not_home + t.revisit)} to try again`],
          ["Not yet synced", fmt(unsynced), unsynced ? `open on routes quiet for ${data.stale_minutes}+ min` : "every phone is up to date"],
          ["Consent rate", data.consent.rate === null ? "–" : `${data.consent.rate}%`,
            `${fmt(data.consent.households_consented)} of ${fmt(data.consent.households_visited)} visited households`],
          ["Consents today", fmt(data.consent.captured_today), "recorded at the door"],
        ].map(([k, v, sub]) => (
          <div key={k} className="px-4 py-3 md:border-l border-line">
            <dt className="text-[13px] text-ink-3">{k}</dt>
            <dd className={`narrow text-[32px] font-semibold leading-tight ${k === "Not yet synced" && unsynced ? "text-amber" : ""}`}>{v}</dd>
            <dd className="text-[12.5px] text-ink-3">{sub}</dd>
          </div>
        ))}
      </dl>

      <div className="grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-5 mt-5">
        <section className="bg-panel border border-line rounded-lg p-4" aria-labelledby="dir-h">
          <h3 id="dir-h" className="text-[17px] font-semibold">Today’s focus for cadres</h3>
          <p className="text-[13.5px] text-ink-3">Shown at the top of every cadre’s route and used to tailor their talking points.</p>
          <ul className="mt-3 space-y-2">
            {data.directives.map((d) => (
              <li key={d.directive_id} className="flex items-start gap-3 rounded-md bg-violet-soft px-3 py-2">
                <span className="flex-1 text-[14.5px]">{d.text}
                  <span className="block text-[12.5px] text-ink-3">{d.constituency ?? (d.state ? "Whole state" : "All states")}{d.author ? ` · ${d.author}` : ""}</span>
                </span>
                {(isAdmin || d.constituency) && (
                  <button onClick={() => removeDirective(d.directive_id)} className="text-[13px] text-ink-3 underline underline-offset-2">Remove</button>
                )}
              </li>
            ))}
            {!data.directives.length && <li className="text-[14px] text-ink-3">No focus set for today.</li>}
          </ul>
          <form className="flex gap-2 mt-3" onSubmit={(e) => { e.preventDefault(); addDirective(); }}>
            <input value={text} onChange={(e) => setText(e.target.value)} maxLength={280} aria-label="New focus for today"
                   placeholder="e.g. Ask about yesterday’s sugarcane price announcement"
                   className="flex-1 min-w-0 h-10 px-3 rounded-md border border-line bg-panel text-[14.5px]" />
            <button disabled={saving || !text.trim()} className="h-10 px-4 rounded-md bg-ink text-white text-[14.5px] disabled:opacity-40">
              {saving ? "Adding…" : "Add for today"}
            </button>
          </form>
        </section>

        <section className="bg-panel border border-line rounded-lg p-4" aria-labelledby="hr-h">
          <h3 id="hr-h" className="text-[17px] font-semibold">Doors completed by hour</h3>
          <svg viewBox="0 0 420 150" className="w-full h-auto mt-2" role="img"
               aria-label={data.hourly.map((h) => `${h.hour}:00 ${h.completed}`).join(", ")}>
            {data.hourly.map((h, i) => {
              const bh = (h.completed / maxHour) * 96;   // leaves room above the tallest bar for its label
              return (
                <g key={h.hour}>
                  <rect x={8 + i * 29} y={120 - bh} width={22} height={Math.max(bh, h.completed ? 2 : 0)} rx={3} fill="#0F766E" />
                  {h.completed > 0 && <text x={19 + i * 29} y={114 - bh} textAnchor="middle" fontSize="11" fill="#4A4659">{h.completed}</text>}
                  {i % 2 === 0 && <text x={19 + i * 29} y={140} textAnchor="middle" fontSize="11" fill="#7C7889">{h.hour > 12 ? h.hour - 12 : h.hour}{h.hour >= 12 ? "pm" : "am"}</text>}
                </g>
              );
            })}
            <line x1="4" x2="416" y1="120.5" y2="120.5" stroke="#DCDDE3" />
          </svg>
          <p className="text-[13px] text-ink-3">{fmt(t.cadres_active)} of {fmt(t.cadres_total)} cadres active{t.cadres_out_of_contact ? ` · ${t.cadres_out_of_contact} out of contact` : ""}</p>
        </section>
      </div>

      <section className="bg-panel border border-line rounded-lg mt-5" aria-labelledby="cad-h">
        <h3 id="cad-h" className="text-[17px] font-semibold px-4 pt-4">Cadres</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-[14px] mt-2">
            <thead>
              <tr className="text-left text-ink-3 text-[13px] border-y border-line">
                <th className="font-normal px-4 py-2">Cadre</th><th className="font-normal px-2 py-2 w-48">Progress</th>
                <th className="font-normal px-2 py-2 text-right">Visited</th><th className="font-normal px-2 py-2 text-right">Not home</th>
                <th className="font-normal px-2 py-2 text-right">Come back</th><th className="font-normal px-2 py-2 text-right">Refused</th>
                <th className="font-normal px-2 py-2 text-right">Consent</th><th className="font-normal px-2 py-2">Last sync</th>
                <th className="font-normal px-4 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.cadres.map((c) => (
                <tr key={c.cadre_id} className="border-b border-line">
                  <td className="px-4 py-2"><span className="block font-medium">{c.name}</span>
                    <span className="block text-[12.5px] text-ink-3">{c.booth_ids.join(", ")}</span></td>
                  <td className="px-2 py-2">
                    {c.targeted ? (
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 rounded-full bg-paper overflow-hidden"><div className="h-full bg-gotv" style={{ width: `${(c.completed / c.targeted) * 100}%` }} /></div>
                        <span className="narrow font-semibold text-[15px] w-12 text-right">{c.completed}/{c.targeted}</span>
                      </div>
                    ) : <span className="text-ink-3">–</span>}
                  </td>
                  <td className="px-2 py-2 text-right">{c.visited}</td><td className="px-2 py-2 text-right">{c.not_home}</td>
                  <td className="px-2 py-2 text-right">{c.revisit}</td><td className="px-2 py-2 text-right">{c.refused}</td>
                  <td className="px-2 py-2 text-right">{c.consent_rate === null ? "–" : `${c.consent_rate}%`}</td>
                  <td className="px-2 py-2 whitespace-nowrap">{since(c.minutes_since_sync)}{c.reported_queue ? ` · ${c.reported_queue} queued` : ""}</td>
                  <td className={`px-4 py-2 whitespace-nowrap ${STATUS_TONE[c.status]}`}>{c.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="bg-panel border border-line rounded-lg mt-5" aria-labelledby="bth-h">
        <h3 id="bth-h" className="text-[17px] font-semibold px-4 pt-4">Booths</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-[14px] mt-2">
            <thead>
              <tr className="text-left text-ink-3 text-[13px] border-y border-line">
                <th className="font-normal px-4 py-2">Booth</th><th className="font-normal px-2 py-2 text-right">Target</th>
                <th className="font-normal px-2 py-2 text-right">Done</th><th className="font-normal px-2 py-2 text-right">Left</th>
                <th className="font-normal px-2 py-2 text-right">Not yet synced</th><th className="font-normal px-2 py-2 text-right">Consent rate</th>
                <th className="font-normal px-2 py-2 text-right">New consents</th><th className="font-normal px-2 py-2 text-right">Persuadables reached</th>
                <th className="font-normal px-4 py-2 text-right">Supporters reached</th>
              </tr>
            </thead>
            <tbody>
              {data.booths.map((b) => (
                <tr key={b.booth_id} className="border-b border-line">
                  <td className="px-4 py-2"><span className="block font-medium">{b.name}</span>
                    <span className="block text-[12.5px] text-ink-3">{b.booth_id}{b.cadres.length ? ` · ${b.cadres.join(", ")}` : ""}</span></td>
                  <td className="px-2 py-2 text-right">{b.targeted}</td><td className="px-2 py-2 text-right">{b.completed}</td>
                  <td className="px-2 py-2 text-right">{b.remaining}</td>
                  <td className={`px-2 py-2 text-right ${b.stale_pending ? "text-amber" : ""}`}>{b.stale_pending}</td>
                  <td className="px-2 py-2 text-right">{b.consent_rate === null ? "–" : `${b.consent_rate}%`}</td>
                  <td className="px-2 py-2 text-right">{b.consents_captured_today}</td>
                  <td className="px-2 py-2 text-right">{b.persuadables_reached}</td><td className="px-4 py-2 text-right">{b.supporters_reached}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-4 py-3 text-[12.5px] text-ink-3">
          “Not yet synced” counts doors still open on routes whose phone hasn’t been in touch for {data.stale_minutes} minutes or more: some may be visited but not sent yet.
          Consent rate is the share of visited households where at least one member has agreed to be contacted.
        </p>
      </section>
    </div>
  );
}
