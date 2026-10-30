"use client";

import { useAuth } from "@/lib/auth";
import { DemoRibbon, DemoToggle } from "./DemoMode";

type Active = "command" | "dashboard" | "field" | "copilot" | "warroom";

export default function Nav({ active, right }: { active: Active; right?: React.ReactNode }) {
  const { session, logout } = useAuth();
  const role = session?.user.role;

  async function signOut() {
    const r = await logout();
    if (r && "blocked" in r && confirm(`${r.blocked} updates haven't been sent yet. Sign out and discard them?`)) {
      await logout({ force: true });
    }
  }

  const link = (href: string, label: string, on: boolean) => (
    <a href={href} aria-current={on ? "page" : undefined}
       className={`px-2.5 sm:px-3 py-1.5 rounded-md text-[15px] whitespace-nowrap ${on ? "bg-ink text-white" : "text-ink-2 hover:text-ink"}`}>
      {label}
    </a>
  );
  return (
    <>
    <DemoRibbon />
    <header className="flex items-center gap-2 sm:gap-4 px-3 sm:px-5 h-14 border-b border-line bg-panel">
      <a href={role === "CADRE" ? "/field" : "/dashboard"} className="flex items-center gap-2 sm:mr-1 shrink-0"
         aria-label="Jarvis Election Intelligence Suite, home">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden>
          <rect x="1" y="1" width="20" height="20" rx="5" fill="#5B2E91" />
          <path d="M7 6.5v9" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" />
        </svg>
        <span className="hidden md:inline text-[17px] font-semibold tracking-tight leading-none">Jarvis
          <span className="hidden 2xl:inline text-ink-3 font-normal"> Election Intelligence</span></span>
      </a>
      <nav className="flex gap-1 min-w-0 overflow-x-auto" aria-label="Products">
        {role === "CADRE" ? link("/field", "Today’s route", active === "field") : (
          <>
            {link("/dashboard", "Command", active === "command")}
            {link("/", "VoterEngine", active === "dashboard")}
            {link("/copilot", "JanVani Copilot", active === "copilot")}
            {link("/warroom", "CounterPulse AI", active === "warroom")}
          </>
        )}
      </nav>
      <div className="ml-auto flex items-center gap-2 shrink-0">
        <DemoToggle />
        {right}
        {session && (
          <div className="flex items-center gap-3 sm:pl-3 sm:ml-1 sm:border-l border-line">
            <span className="hidden sm:block text-right leading-tight">
              <span className="block text-[14px] font-medium">{session.user.display_name}</span>
              {session.user.role_label !== session.user.display_name &&
                <span className="block text-[12px] text-ink-3">{session.user.role_label}</span>}
            </span>
            <button onClick={signOut} className="text-[14px] text-ink-3 hover:text-ink underline underline-offset-2">Sign out</button>
          </div>
        )}
      </div>
    </header>
    </>
  );
}
