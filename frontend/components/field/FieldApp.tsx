"use client";

import { useEffect, useMemo, useState } from "react";
import Nav from "@/components/Nav";
import { DemoRibbon } from "@/components/DemoMode";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import StopCard from "./StopCard";
import SyncStatus from "./SyncStatus";
import { useFieldRoute } from "./useFieldRoute";

export default function FieldApp() {
  const { session, loading, logout } = useAuth();
  const cadreId = session?.user.cadre_id;
  const f = useFieldRoute(cadreId, session?.today);
  const [stopsWanted, setStopsWanted] = useState(20);
  const [busy, setBusy] = useState(false);
  const [buildError, setBuildError] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [aiLive, setAiLive] = useState(false);
  const [lang, setLangState] = useState("en");
  useEffect(() => { try { setLangState(localStorage.getItem("ve.lang") || "en"); } catch { /* ignore */ } }, []);
  const setLang = (l: string) => { setLangState(l); try { localStorage.setItem("ve.lang", l); } catch { /* ignore */ } };
  useEffect(() => { api.llmStatus().then((s) => setAiLive(s.live)).catch(() => setAiLive(false)); }, []);

  useEffect(() => {
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => { /* offline shell unavailable; data still cached */ });
    }
  }, []);

  const stops = f.route?.stops ?? [];
  const done = stops.filter((s) => s.status !== "PENDING").length;
  const unsynced = useMemo(() => {
    const ids = new Set<string>();
    for (const op of f.ops) {
      if (op.type === "stop_status") ids.add(op.household_id);
      else for (const s of f.route?.stops ?? []) if (s.members.some((m) => m.voter_id === op.voter_id)) ids.add(s.household_id);
    }
    return ids;
  }, [f.ops, f.route]);

  // open the first pending stop once the route arrives
  useEffect(() => {
    if (openId === null && stops.length) setOpenId(stops.find((s) => s.status === "PENDING")?.stop_id ?? null);
  }, [stops, openId]);

  async function build(regenerate: boolean) {
    setBusy(true); setBuildError("");
    try { await f.build(stopsWanted, regenerate); setOpenId(null); }
    catch (e) { setBuildError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function signOut() {
    const r = await logout();
    if (r && "blocked" in r && confirm(`${r.blocked} updates haven't been sent yet. Sign out and discard them?`)) {
      await logout({ force: true });
    }
  }

  if (loading || (!f.loaded && !f.route)) {
    return <p className="p-6 text-ink-3">Loading your route…</p>;
  }
  if (!session) return null;

  const cadre = f.route?.cadre;
  const offline = f.connection === "offline";

  return (
    <div className="min-h-dvh">
      <div className="hidden md:block"><Nav active="field" /></div>
      <div className="md:hidden"><DemoRibbon /></div>
      <main className="max-w-xl mx-auto pb-24">
        <div className="px-4 pt-4 pb-3 bg-panel border-b border-line md:border md:rounded-b-lg">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="wide text-[26px] font-semibold leading-tight">Today’s doors</h1>
              <p className="text-[14.5px] text-ink-2 truncate">
                {session.user.display_name}{cadre ? `, booth ${cadre.booth_ids.replaceAll(",", ", ")}` : ""}
                {f.route?.route_id ? ` · ${stops.length} households · ${f.route.total_km} km` : ""}
              </p>
            </div>
            <button onClick={signOut} className="md:hidden shrink-0 text-[14px] text-ink-3 underline underline-offset-2">Sign out</button>
          </div>

          {stops.length > 0 && (
            <div className="mt-3" aria-label={`${done} of ${stops.length} households done`}>
              <div className="flex gap-[3px]">
                {stops.map((s) => (
                  <span key={s.stop_id} className={`h-1.5 flex-1 rounded-full ${
                    s.status === "VISITED" ? "bg-gotv" : s.status === "PENDING" ? "bg-line"
                      : s.status === "NOT_HOME" || s.status === "REVISIT" ? "bg-amber" : "bg-ink-3"}`} />
                ))}
              </div>
              <p className="text-[13px] text-ink-3 mt-1.5">{done} of {stops.length} done</p>
            </div>
          )}
        </div>

        {/* stays visible while the cadre scrolls through doors */}
        <div className="sticky top-0 z-10 px-4 py-2 bg-panel/95 backdrop-blur border-b border-line md:border-x md:rounded-b-lg">
          <SyncStatus connection={f.connection} pending={f.pending} syncing={f.syncing} lastSynced={f.lastSynced} onSync={f.flush} />
        </div>

        {(f.route?.directives?.length ?? 0) > 0 && (
          <section className="mx-4 mt-3 px-3 py-2.5 rounded-md bg-ink text-white" aria-labelledby="focus-h">
            <h2 id="focus-h" className="text-[13px] text-white/70">Today’s focus</h2>
            <ul className="space-y-1 mt-0.5">
              {f.route!.directives!.map((d) => <li key={d.id} className="text-[15px] leading-snug">{d.text}</li>)}
            </ul>
          </section>
        )}

        {f.cachedAt && offline && (
          <p className="mx-4 mt-3 px-3 py-2 rounded-md bg-paper border border-line text-[14px] text-ink-2">
            Showing the route saved at {new Date(f.cachedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.
            Keep marking doors; everything sends when you’re back online.
          </p>
        )}
        {f.issues.length > 0 && (
          <div role="alert" className="mx-4 mt-3 px-3 py-2 rounded-md bg-violet-soft text-[14px]">
            <p>{f.issues.length} {f.issues.length === 1 ? "update was" : "updates were"} not saved:</p>
            <ul className="list-disc pl-5 text-ink-2">
              {[...new Set(f.issues.map((i) => i.reason ?? i.result))].map((r) => <li key={r}>{r}</li>)}
            </ul>
            <button onClick={f.dismissIssues} className="mt-1 text-violet underline underline-offset-2">Dismiss</button>
          </div>
        )}
        {f.error && <p role="alert" className="mx-4 mt-3 px-3 py-2 rounded-md bg-violet-soft text-[14px]">{f.error}</p>}

        {f.route && !f.route.route_id && (
          <div className="mx-4 mt-6 p-5 bg-panel border border-line rounded-lg">
            <h2 className="text-[18px] font-semibold">No route for today yet</h2>
            <p className="text-[14.5px] text-ink-2 mt-1">
              {f.route.message ?? "Build one from the highest-priority households near each other in your booth."}
            </p>
            <label className="block mt-4 text-[14px]" htmlFor="n">Households to visit</label>
            <select id="n" value={stopsWanted} onChange={(e) => setStopsWanted(+e.target.value)}
                    className="mt-1 h-10 px-2 rounded-md border border-line bg-panel text-[15px] w-full">
              {[10, 15, 20, 25, 30].map((n) => <option key={n} value={n}>{n} households</option>)}
            </select>
            <button onClick={() => build(false)} disabled={busy || offline}
                    className="mt-4 w-full h-11 rounded-md bg-violet text-white font-medium disabled:opacity-50">
              {busy ? "Building route…" : "Build today’s route"}
            </button>
            {offline && <p className="mt-2 text-[13.5px] text-ink-3">Connect to the internet to build a route.</p>}
            {buildError && <p role="alert" className="mt-2 text-[14px] text-amber">{buildError}</p>}
          </div>
        )}

        <ol className="mt-3 space-y-2 px-2 md:px-0">
          {stops.map((s) => (
            <StopCard key={s.stop_id} stop={s} open={openId === s.stop_id} unsynced={unsynced.has(s.household_id)}
                      onToggle={() => setOpenId(openId === s.stop_id ? null : s.stop_id)}
                      onUpdate={(st, notes) => {
                        f.markStop(s, st, notes);
                        setOpenId(stops.find((x) => x.seq > s.seq && x.status === "PENDING")?.stop_id ?? null);
                      }}
                      onConsent={(m, st) => f.setConsent(m, st)}
                      points={{ cadreId, date: f.route?.route_date, online: !offline, aiLive, lang, setLang }} />
          ))}
        </ol>

        {f.route?.route_id && (
          <div className="px-4 mt-6 text-center">
            <button onClick={() => confirm("Rebuild today’s route? Doors you have already marked stay recorded.") && build(true)}
                    disabled={busy || offline || f.pending > 0}
                    className="text-[14px] text-ink-3 underline underline-offset-2 disabled:no-underline disabled:opacity-60">
              {busy ? "Rebuilding…" : offline ? "Rebuild needs a connection" : f.pending ? "Sync before rebuilding" : "Rebuild route from remaining households"}
            </button>
            {buildError && <p role="alert" className="mt-2 text-[14px] text-amber">{buildError}</p>}
          </div>
        )}
      </main>
    </div>
  );
}
