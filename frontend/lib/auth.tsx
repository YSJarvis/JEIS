"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, NetworkError } from "./api";
import { clearOfflineData, pendingCount } from "./offline/outbox";
import type { Role, Session } from "./types";

interface AuthValue {
  session: Session | null;
  loading: boolean;
  hasRole: (...roles: Role[]) => boolean;
  refresh: () => Promise<void>;
  logout: (opts?: { force?: boolean }) => Promise<{ blocked: number } | void>;
}

const AuthContext = createContext<AuthValue | null>(null);

/** Session state for client components. Route protection itself happens in
 *  middleware.ts and on the API; this provider only exposes who is signed in. */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const s = await api.me();
      setSession(s);
      try { localStorage.setItem("ve.session", JSON.stringify(s)); } catch { /* storage unavailable */ }
    } catch (e) {
      // offline: fall back to the last known session so the field app can still open
      let cached: Session | null = null;
      try { cached = JSON.parse(localStorage.getItem("ve.session") ?? "null"); } catch { /* ignore */ }
      setSession(e instanceof NetworkError ? cached : null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const logout = useCallback(async (opts?: { force?: boolean }) => {
    const cadreId = session?.user.cadre_id;
    if (cadreId && !opts?.force) {
      const n = await pendingCount(cadreId);
      if (n > 0) return { blocked: n };   // caller confirms before unsynced work is discarded
    }
    try { await api.logout(); } catch { /* offline: cookie expires on its own */ }
    // DPDP: nothing about voters stays on a shared device after sign-out
    await clearOfflineData();
    try { localStorage.removeItem("ve.session"); } catch { /* ignore */ }
    navigator.serviceWorker?.controller?.postMessage({ type: "CLEAR_CACHES" });
    setSession(null);
    window.location.assign("/login");
  }, [session]);

  const value = useMemo<AuthValue>(() => ({
    session, loading, refresh, logout,
    hasRole: (...roles) => !!session && roles.includes(session.user.role),
  }), [session, loading, refresh, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth must be used inside <AuthProvider>");
  return v;
}
