"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Role } from "@/lib/types";

const HOME: Record<Role, string> = { ADMIN: "/dashboard", MANAGER: "/dashboard", CADRE: "/field" };

// Demo accounts created by the mock-data seeder (never in production).
const SHORTCUTS: { role: Role; label: string; who: string; username: string }[] = [
  { role: "ADMIN", label: "Central Strategist", who: "All four states, imports, weights, privacy", username: "admin" },
  { role: "MANAGER", label: "Constituency Manager", who: "Sardhana, Uttar Pradesh", username: "mgr.sardhana" },
  { role: "CADRE", label: "Field Cadre", who: "Booth UP-SAR-001, today’s route", username: "cad-up-sar-001" },
];
const DEMO_PASSWORD = process.env.NEXT_PUBLIC_DEMO_PASSWORD ?? "voterengine";
const SHOW_DEMO = process.env.NODE_ENV !== "production" || process.env.NEXT_PUBLIC_DEMO_LOGINS === "1";

function safeNext(role: Role): string {
  const next = new URLSearchParams(window.location.search).get("next");
  if (!next || !next.startsWith("/") || next.startsWith("//")) return HOME[role];
  // send each role only where it is allowed
  const isField = next === "/field" || next.startsWith("/field/");
  return (role === "CADRE") === isField ? next : HOME[role];
}

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [inDemo, setInDemo] = useState(false);

  useEffect(() => { api.demoStatus().then((s) => setInDemo(s.active)).catch(() => setInDemo(false)); }, []);

  async function signIn(u: string, p: string) {
    setBusy(u); setError("");
    try {
      const s = await api.login(u, p);
      window.location.assign(safeNext(s.user.role));   // full navigation so middleware sees the new cookie
    } catch (e) {
      setError((e as Error).message === "No connection" ? "Can’t reach the server. Check your connection." : (e as Error).message);
      setBusy(null);
    }
  }

  return (
    <main className="min-h-dvh grid place-items-center px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="flex items-center gap-2.5">
          <svg width="30" height="30" viewBox="0 0 22 22" aria-hidden>
            <rect x="1" y="1" width="20" height="20" rx="5" fill="#5B2E91" />
            <path d="M7 6.5v9" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" />
          </svg>
          <span className="wide text-[24px] font-semibold tracking-tight leading-none">Jarvis
            <span className="block text-[13px] font-normal text-ink-3 tracking-normal mt-1" style={{ fontStretch: "100%" }}>Election Intelligence Suite</span></span>
        </div>
        {inDemo && (
          <div role="status" className="mt-6 rounded-md bg-amber text-white px-3 py-2 text-[14px]">
            <b>Pitch demo is on in this browser.</b> Sign in with a demo role below; everything you see is synthetic.{" "}
            <button className="underline" onClick={async () => { await api.demoExit(); setInDemo(false); }}>Leave the demo</button>
          </div>
        )}
        <h1 className="text-[20px] font-semibold mt-8">Sign in</h1>

        <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); signIn(username, password); }}>
          <label className="block">
            <span className="text-[14px] text-ink-2">Username</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required
                   autoCapitalize="none" spellCheck={false}
                   className="mt-1 w-full h-11 px-3 rounded-md border border-line bg-panel text-[16px]" />
          </label>
          <label className="block">
            <span className="text-[14px] text-ink-2">Password</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required
                   className="mt-1 w-full h-11 px-3 rounded-md border border-line bg-panel text-[16px]" />
          </label>
          {error && <p role="alert" className="text-[14px] text-amber">{error}</p>}
          <button disabled={!!busy} className="w-full h-11 rounded-md bg-violet text-white font-medium disabled:opacity-60">
            {busy === username ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {(SHOW_DEMO || inDemo) && (
          <section className="mt-10" aria-labelledby="demo">
            <h2 id="demo" className="text-[15px] font-semibold">Try a demo role</h2>
            <p className="text-[13.5px] text-ink-3">Mock data only. Each role sees a different part of the app.</p>
            <ul className="mt-3 divide-y divide-line border-y border-line">
              {SHORTCUTS.map((s) => (
                <li key={s.role}>
                  <button onClick={() => signIn(s.username, DEMO_PASSWORD)} disabled={!!busy}
                          className="w-full flex items-center gap-3 py-3 text-left hover:bg-panel disabled:opacity-60">
                    <span className="flex-1 min-w-0">
                      <span className="block text-[15.5px] font-medium">{s.label}</span>
                      <span className="block text-[13px] text-ink-3">{s.who}</span>
                    </span>
                    <span className="text-[13.5px] text-violet">{busy === s.username ? "Signing in…" : "Sign in"}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </main>
  );
}
