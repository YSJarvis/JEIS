"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmt } from "@/lib/quadrants";
import type { ConsentStatus, PrivacyState } from "@/lib/types";

export const CONSENT_LABEL: Record<ConsentStatus, string> = { GRANTED: "Agreed", PENDING: "Not asked", OPT_OUT: "Opted out" };
const CONSENT_COLOR: Record<ConsentStatus, string> = { GRANTED: "#0F766E", PENDING: "#C9C6D3", OPT_OUT: "#5E5A6B" };

export default function PrivacyPanel({ onClose, onChange }: { onClose: () => void; onChange: () => void }) {
  const [p, setP] = useState<PrivacyState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { api.privacy().then(setP).catch((e) => setError(e.message)); }, []);

  async function toggle() {
    if (!p) return;
    setBusy(true); setError("");
    try { setP(await api.setMasking(!p.pii_masking)); onChange(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const total = p ? Object.values(p.consent).reduce((a, b) => a + b, 0) || 1 : 1;

  return (
    <div className="fixed inset-0 z-[1000] bg-ink/40 flex justify-end" onClick={onClose}>
      <aside role="dialog" aria-modal="true" aria-labelledby="pv" onClick={(e) => e.stopPropagation()}
             className="w-full max-w-md h-full bg-panel p-6 overflow-y-auto">
        <div className="flex items-start justify-between">
          <h2 id="pv" className="text-[22px] font-semibold">Privacy and consent</h2>
          <button onClick={onClose} className="text-ink-3 hover:text-ink text-[15px]">Close</button>
        </div>
        {error && <p role="alert" className="mt-3 text-[14px] text-amber">{error}</p>}
        {p && (
          <>
            <div className="mt-5 flex items-start gap-4">
              <div className="flex-1">
                <p id="mask-label" className="text-[15.5px] font-medium">Mask personal details on the dashboard</p>
                <p className="text-[13.5px] text-ink-2 mt-1">Names become initials, house numbers and phone numbers are hidden, and name search is turned off. Cadres still see full details for their own route.</p>
              </div>
              <button role="switch" aria-checked={p.pii_masking} aria-labelledby="mask-label" onClick={toggle} disabled={busy}
                      className={`relative shrink-0 w-12 h-7 rounded-full transition-colors ${p.pii_masking ? "bg-violet" : "bg-line"} disabled:opacity-60`}>
                <span className={`absolute top-1 w-5 h-5 rounded-full bg-white transition-all ${p.pii_masking ? "left-6" : "left-1"}`} />
              </button>
            </div>
            {p.updated && (
              <p className="text-[12.5px] text-ink-3 mt-2">
                {p.pii_masking ? "On" : "Off"} since {new Date(p.updated.updated_at).toLocaleString()}
              </p>
            )}

            <h3 className="text-[16px] font-semibold mt-8">Consent across all voters</h3>
            <div className="flex h-2.5 rounded-sm overflow-hidden mt-3" aria-hidden>
              {(Object.keys(p.consent) as ConsentStatus[]).map((k) => (
                <div key={k} style={{ width: `${(p.consent[k] / total) * 100}%`, background: CONSENT_COLOR[k] }} />
              ))}
            </div>
            <dl className="mt-3 space-y-2">
              {(Object.keys(p.consent) as ConsentStatus[]).map((k) => (
                <div key={k} className="flex gap-3">
                  <span className="w-2.5 h-2.5 mt-1.5 rounded-full shrink-0" style={{ background: CONSENT_COLOR[k] }} />
                  <div className="flex-1">
                    <dt className="flex justify-between text-[14.5px]"><span>{CONSENT_LABEL[k]}</span>
                      <span className="narrow text-[17px] font-semibold">{fmt(p.consent[k])}</span></dt>
                    <dd className="text-[13px] text-ink-3">{p.policy[k]}</dd>
                  </div>
                </div>
              ))}
            </dl>

            <h3 className="text-[16px] font-semibold mt-8">Recent consent changes</h3>
            {p.recent_consent_changes.length === 0
              ? <p className="text-[14px] text-ink-3 mt-2">No changes recorded yet. Cadres record consent at the door; imports with a consent column are logged here too.</p>
              : (
                <ul className="mt-2 divide-y divide-line border-y border-line">
                  {p.recent_consent_changes.map((c, i) => (
                    <li key={i} className="py-2 text-[13.5px]">
                      <span className="font-medium">{c.voter_id}</span>{" "}
                      {c.old_status ? `${CONSENT_LABEL[c.old_status as ConsentStatus] ?? c.old_status} → ` : ""}{CONSENT_LABEL[c.new_status]}
                      <span className="block text-ink-3">
                        {c.source === "field" ? "At the door" : c.source === "import" ? "Data import" : "Dashboard"}
                        {c.actor ? ` · ${c.actor}` : ""} · {new Date(c.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
          </>
        )}
      </aside>
    </div>
  );
}
