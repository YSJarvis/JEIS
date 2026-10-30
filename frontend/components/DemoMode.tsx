"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { clearOfflineData } from "@/lib/offline/outbox";
import type { DemoStatus } from "@/lib/types";

/** Leave cached data from the other workspace behind, then reload into the new one. */
async function switchTo(path: string) {
  try { await clearOfflineData(); } catch { /* nothing cached */ }
  try { localStorage.removeItem("ve.session"); } catch { /* storage unavailable */ }
  window.location.assign(path);
}

export async function startDemo(rebuild = false) {
  await api.demoStart(rebuild);
  await switchTo("/dashboard");
}

export async function exitDemo() {
  let restored = false;
  try { restored = !!(await api.demoExit()).restored_session; } catch { /* offline: cookies expire */ }
  await switchTo(restored ? "/dashboard" : "/login");
}

/** "Load Pitch Demo Data" switch for the top bar. Strategists see it in the real workspace; anyone can leave the demo. */
export function DemoToggle() {
  const { session } = useAuth();
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inDemo = session?.workspace === "demo";

  useEffect(() => { api.demoStatus().then(setStatus).catch(() => setStatus(null)); }, []);
  if (!session || !status?.allowed) return null;
  if (!inDemo && session.user.role !== "ADMIN") return null;

  async function toggle() {
    setBusy(true); setError("");
    try { inDemo ? await exitDemo() : await startDemo(); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }

  return (
    <span className="no-print flex items-center gap-2">
      <button type="button" role="switch" aria-checked={inDemo} onClick={toggle} disabled={busy}
              title={inDemo ? "Leave the pitch demo and return to your campaign data"
                            : "Load realistic synthetic data for all four states into a separate demo workspace. Your campaign data is not touched."}
              className={`h-8 pl-1.5 pr-2.5 rounded-full border text-[13.5px] inline-flex items-center gap-2 whitespace-nowrap disabled:opacity-60
                          ${inDemo ? "border-amber bg-amber text-white" : "border-line bg-panel text-ink-2 hover:border-ink-3"}`}>
        <span aria-hidden className={`w-8 h-5 rounded-full relative transition-colors ${inDemo ? "bg-white/35" : "bg-line"}`}>
          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${inDemo ? "left-[14px]" : "left-0.5"}`} />
        </span>
        <span className="hidden lg:inline">{busy ? (inDemo ? "Leaving demo…" : "Loading demo data…") : "Pitch demo"}</span>
        <span className="lg:hidden">{busy ? "…" : "Demo"}</span>
      </button>
      {error && <span role="alert" className="text-[12.5px] text-amber max-w-48">{error}</span>}
    </span>
  );
}

/** Persistent banner while a session is inside the demo workspace, so no one mistakes it for real data. */
export function DemoRibbon() {
  const { session } = useAuth();
  const [busy, setBusy] = useState<"" | "reset" | "exit">("");
  if (session?.workspace !== "demo") return null;
  const admin = session.user.role === "ADMIN";
  return (
    <div role="status" className="no-print bg-amber text-white text-[13px] px-3 sm:px-5 py-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
      <span className="font-semibold tracking-wide">PITCH DEMO</span>
      <span className="opacity-90 min-w-0">Synthetic data for Uttar Pradesh, Punjab, Uttarakhand and Goa. Your campaign data is untouched.</span>
      <span className="ml-auto flex gap-3">
        {admin && (
          <button disabled={!!busy} className="underline underline-offset-2 disabled:opacity-60"
                  onClick={async () => { setBusy("reset"); try { await startDemo(true); } catch { setBusy(""); } }}>
            {busy === "reset" ? "Resetting…" : "Reset demo data"}
          </button>
        )}
        <button disabled={!!busy} className="underline underline-offset-2 font-medium disabled:opacity-60"
                onClick={async () => { setBusy("exit"); await exitDemo(); }}>
          {busy === "exit" ? "Leaving…" : "Exit demo"}
        </button>
      </span>
    </div>
  );
}
