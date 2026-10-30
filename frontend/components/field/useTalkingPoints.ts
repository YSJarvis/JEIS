"use client";

import { useCallback, useEffect, useState } from "react";
import { api, NetworkError } from "@/lib/api";
import { cachedPoints, cachePoints } from "@/lib/offline/outbox";
import type { Stop, TalkingPointResult } from "@/lib/types";

/**
 * Tailored talking points for one stop.
 * - The route already carries baseline points (directives + knowledge base + rules), so
 *   there is always something to show, offline included.
 * - When a stop is opened online and an AI provider is configured, ask the server for
 *   tailored points; keep them in IndexedDB so they're still there without signal.
 */
export function useTalkingPoints(opts: {
  cadreId?: string | null; date?: string; stop: Stop; open: boolean; online: boolean; aiLive: boolean; lang: string;
}) {
  const { cadreId, date, stop, open, online, aiLive, lang } = opts;
  const [result, setResult] = useState<TalkingPointResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchPoints = useCallback(async (refresh: boolean) => {
    if (!cadreId || !date) return;
    setLoading(true); setError("");
    try {
      const r = await api.talkingPoints(stop.household_id, lang, refresh);
      setResult(r);
      if (r.live) await cachePoints(cadreId, r, lang);
    } catch (e) {
      setError(e instanceof NetworkError ? "No connection: showing standard points." : (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [cadreId, date, stop.household_id, lang]);

  useEffect(() => {
    if (!open || !cadreId || !date) return;
    let alive = true;
    (async () => {
      const hit = await cachedPoints(cadreId, date, stop.household_id, lang);
      if (!alive) return;
      if (hit) { setResult(hit); return; }
      setResult(null);
      if (online && aiLive) await fetchPoints(false);
    })();
    return () => { alive = false; };
  }, [open, cadreId, date, stop.household_id, lang, online, aiLive, fetchPoints]);

  const points = result?.points ?? stop.talking_points.map((p) => ({ ...p, sources: (p as { sources?: string[] }).sources ?? [] }));
  const sources = result?.sources ?? stop.sources ?? [];
  return {
    points, sources, loading, error,
    tailored: !!result?.live, note: result?.note ?? null, language: result?.language ?? "en",
    refresh: () => fetchPoints(true), canRefresh: online && aiLive,
  };
}
