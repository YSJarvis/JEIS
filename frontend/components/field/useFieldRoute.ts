"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, NetworkError } from "@/lib/api";
import {
  applyOps, cachedRoute, cacheRoute, enqueue, latestCachedRoute, markAttempt, newOpId, prunePoints, queued, removeOps,
} from "@/lib/offline/outbox";
import type { ConsentStatus, FieldRoute, Member, Stop, StopStatus, SyncOp } from "@/lib/types";

export type Connection = "online" | "offline";
export interface SyncIssue { op_id: string; result: string; reason?: string }

const RETRY_MS = 20_000;
const BATCH = 200;
export const APP_VERSION = "web-3";

/**
 * Offline-first state for the cadre's route.
 *
 * Reads: show the cached route instantly, then refresh from the server.
 * Writes: every action is queued in IndexedDB first, shown immediately, and
 * flushed to /api/cadre/sync whenever the connection allows. The server
 * response replaces the cached route, so the device converges on the
 * server's view once the queue is empty.
 */
export function useFieldRoute(cadreId: string | null | undefined, today?: string) {
  const [base, setBase] = useState<FieldRoute | null>(null);
  const [ops, setOps] = useState<SyncOp[]>([]);
  const [connection, setConnection] = useState<Connection>(() =>
    typeof navigator !== "undefined" && navigator.onLine === false ? "offline" : "online");
  const [syncing, setSyncing] = useState(false);
  const [lastSynced, setLastSynced] = useState<number | null>(null);
  const [cachedAt, setCachedAt] = useState<number | null>(null);
  const [issues, setIssues] = useState<SyncIssue[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const flushing = useRef(false);
  const flushRef = useRef<(() => Promise<void>) | null>(null);

  const reloadOps = useCallback(async () => {
    if (!cadreId) return;
    setOps((await queued(cadreId)).map((q) => q.op));
  }, [cadreId]);

  const accept = useCallback(async (route: FieldRoute) => {
    if (!cadreId) return;
    setBase(route);
    setCachedAt(null);
    setLastSynced(Date.now());
    setConnection("online");
    await cacheRoute(cadreId, route);
    await prunePoints(cadreId, route.route_date);
  }, [cadreId]);

  /** Push the queue (if any) and pull the latest route. Safe to call often. */
  const flush = useCallback(async () => {
    if (!cadreId || flushing.current) return;
    flushing.current = true;
    setSyncing(true);
    const all = await queued(cadreId);
    const items = all.slice(0, BATCH);
    let more = false;
    try {
      if (items.length) {
        const res = await api.sync(items.map((q) => q.op), { remaining: all.length - items.length, app_version: APP_VERSION });
        more = all.length > items.length;
        // every op got a definitive answer from the server: it leaves the queue
        await removeOps(res.results.map((r) => r.op_id));
        const bad = res.results.filter((r) => r.result !== "applied" && !r.duplicate);
        if (bad.length) setIssues((prev) => [...prev, ...bad]);
        if (res.route) await accept(res.route);
      } else {
        await accept(await api.myRoute());
      }
      setError("");
    } catch (e) {
      if (e instanceof NetworkError) {
        setConnection("offline");
        if (items.length) await markAttempt(items, "offline");
      } else if (e instanceof ApiError) {
        setError(e.message);
        if (items.length) await markAttempt(items, e.message);
      }
    } finally {
      await reloadOps();
      flushing.current = false;
      setSyncing(false);
      setLoaded(true);
      if (more) setTimeout(() => { flushRef.current?.(); }, 0);   // send the next batch
    }
  }, [cadreId, accept, reloadOps]);

  useEffect(() => { flushRef.current = flush; }, [flush]);

  // boot: cache first, then network
  useEffect(() => {
    if (!cadreId) return;
    let alive = true;
    (async () => {
      const hit = (today && (await cachedRoute(cadreId, today))) || (await latestCachedRoute(cadreId));
      if (alive && hit) { setBase(hit.route); setCachedAt(hit.savedAt); setLoaded(true); }
      await reloadOps();
      if (alive) await flush();
    })();
    return () => { alive = false; };
  }, [cadreId, today, flush, reloadOps]);

  // connectivity triggers
  useEffect(() => {
    const goOnline = () => { setConnection("online"); flush(); };
    const goOffline = () => setConnection("offline");
    const onVisible = () => { if (document.visibilityState === "visible") flush(); };
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [flush]);

  // retry while anything is queued (navigator.onLine is not reliable on its own)
  useEffect(() => {
    if (!ops.length && connection === "online") return;
    const t = setInterval(() => { flush(); }, RETRY_MS);
    return () => clearInterval(t);
  }, [ops.length, connection, flush]);

  const queue = useCallback(async (op: SyncOp) => {
    if (!cadreId) return;
    await enqueue(cadreId, op);
    setOps((prev) => [...prev, op]);          // instant feedback
    if (connection === "online") flush();
  }, [cadreId, connection, flush]);

  const markStop = useCallback((stop: Stop, status: StopStatus, notes: string) =>
    queue({ op_id: newOpId(), type: "stop_status", stop_id: stop.stop_id, household_id: stop.household_id,
            status, notes: notes || undefined, client_ts: new Date().toISOString() }), [queue]);

  const setConsent = useCallback((m: Member, status: ConsentStatus) =>
    queue({ op_id: newOpId(), type: "consent", voter_id: m.voter_id, status, client_ts: new Date().toISOString() }), [queue]);

  /** Building or rebuilding needs the server and an empty queue. */
  const build = useCallback(async (maxStops: number, regenerate: boolean) => {
    if (ops.length) await flush();
    if (cadreId && (await queued(cadreId)).length) throw new Error("Sync your saved updates first.");
    try {
      await accept(await api.buildMyRoute(maxStops, regenerate));
    } catch (e) {
      if (e instanceof NetworkError) { setConnection("offline"); throw new Error("You're offline. Connect to build a route."); }
      throw e;
    }
  }, [ops.length, flush, cadreId, accept]);

  const route = useMemo(() => (base ? applyOps(base, ops) : null), [base, ops]);

  return {
    route, ops, loaded, connection, syncing, pending: ops.length, lastSynced, cachedAt, error, issues,
    dismissIssues: () => setIssues([]), flush, markStop, setConsent, build,
  };
}
