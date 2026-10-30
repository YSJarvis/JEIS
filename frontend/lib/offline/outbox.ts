/**
 * Offline queue ("outbox") and route cache for the cadre app.
 *
 * Every field action becomes a SyncOp with a client-generated op_id and is
 * written here first; the sync loop then posts the queue to /api/cadre/sync.
 * The server applies each op_id at most once, so re-sending after a dropped
 * connection is always safe.
 */
import type { FieldRoute, Stop, SyncOp, TalkingPointResult } from "../types";
import { all, del, destroy, get, put, STORES } from "./idb";

interface QueuedOp { op_id: string; cadreId: string; op: SyncOp; queuedAt: number; attempts: number; lastError?: string }
interface CachedRoute { key: string; cadreId: string; date: string; route: FieldRoute; savedAt: number }

const routeKey = (cadreId: string, date: string) => `${cadreId}:${date}`;

export function newOpId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// ---------- route cache ----------

export async function cacheRoute(cadreId: string, route: FieldRoute): Promise<void> {
  await put<CachedRoute>(STORES.routes, { key: routeKey(cadreId, route.route_date), cadreId, date: route.route_date, route, savedAt: Date.now() });
  // only today's route is kept on the device
  const stale = (await all<CachedRoute>(STORES.routes)).filter((r) => r.cadreId !== cadreId || r.date !== route.route_date);
  await del(STORES.routes, stale.map((r) => r.key));
}

export async function cachedRoute(cadreId: string, date: string): Promise<{ route: FieldRoute; savedAt: number } | null> {
  const r = await get<CachedRoute>(STORES.routes, routeKey(cadreId, date));
  return r ? { route: r.route, savedAt: r.savedAt } : null;
}

export async function latestCachedRoute(cadreId: string): Promise<{ route: FieldRoute; savedAt: number } | null> {
  const rs = (await all<CachedRoute>(STORES.routes)).filter((r) => r.cadreId === cadreId).sort((a, b) => b.savedAt - a.savedAt);
  return rs[0] ? { route: rs[0].route, savedAt: rs[0].savedAt } : null;
}

// ---------- tailored talking points (so they survive going offline) ----------

interface CachedPoints { key: string; cadreId: string; date: string; result: TalkingPointResult }
const pointsKey = (cadreId: string, date: string, householdId: string, lang: string) => `${cadreId}:${date}:${householdId}:${lang}`;

export async function cachePoints(cadreId: string, result: TalkingPointResult, lang: string): Promise<void> {
  await put<CachedPoints>(STORES.points, { key: pointsKey(cadreId, result.date, result.household_id, lang), cadreId, date: result.date, result });
}

export async function cachedPoints(cadreId: string, date: string, householdId: string, lang: string): Promise<TalkingPointResult | null> {
  return (await get<CachedPoints>(STORES.points, pointsKey(cadreId, date, householdId, lang)))?.result ?? null;
}

export async function prunePoints(cadreId: string, date: string): Promise<void> {
  const stale = (await all<CachedPoints>(STORES.points)).filter((p) => p.cadreId !== cadreId || p.date !== date);
  await del(STORES.points, stale.map((p) => p.key));
}

// ---------- outbox ----------

export async function enqueue(cadreId: string, op: SyncOp): Promise<void> {
  await put<QueuedOp>(STORES.outbox, { op_id: op.op_id, cadreId, op, queuedAt: Date.now(), attempts: 0 });
}

export async function queued(cadreId: string): Promise<QueuedOp[]> {
  return (await all<QueuedOp>(STORES.outbox, { name: "cadreId", value: cadreId })).sort((a, b) => a.queuedAt - b.queuedAt);
}

export async function pendingCount(cadreId: string): Promise<number> {
  return (await queued(cadreId)).length;
}

export async function removeOps(ids: string[]): Promise<void> {
  await del(STORES.outbox, ids);
}

export async function markAttempt(items: QueuedOp[], error: string): Promise<void> {
  for (const q of items) await put<QueuedOp>(STORES.outbox, { ...q, attempts: q.attempts + 1, lastError: error });
}

export async function clearOfflineData(): Promise<void> {
  await destroy();
}

// ---------- local overlay ----------

/** Apply not-yet-synced ops on top of a route so the UI shows what the cadre did. */
export function applyOps(route: FieldRoute, ops: SyncOp[]): FieldRoute {
  if (!ops.length) return route;
  const stops: Stop[] = route.stops.map((s) => ({ ...s, members: [...s.members] }));
  for (const op of ops) {
    if (op.type === "stop_status") {
      const s = stops.find((x) => x.stop_id === op.stop_id) ?? stops.find((x) => x.household_id === op.household_id);
      if (s) { s.status = op.status; if (op.notes !== undefined) s.notes = op.notes; }
    } else {
      for (const s of stops) {
        const i = s.members.findIndex((m) => m.voter_id === op.voter_id);
        if (i >= 0) {
          // an opt-out stops processing immediately on the device too
          if (op.status === "OPT_OUT") s.members.splice(i, 1);
          else s.members[i] = { ...s.members[i], consent_status: op.status, phone: op.status === "GRANTED" ? s.members[i].phone : null };
        }
      }
    }
  }
  return { ...route, stops };
}
