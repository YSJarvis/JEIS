"use client";

import type { Severity, WrCounter } from "@/lib/types";

export const STATE_NAME: Record<string, string> = { UP: "Uttar Pradesh", PB: "Punjab", UK: "Uttarakhand", GA: "Goa" };
export const STATE_SHORT: Record<string, string> = { UP: "UP", PB: "Punjab", UK: "Uttarakhand", GA: "Goa" };

/** Threat heat ramp: one warm hue getting darker, deliberately not a party colour. Always paired with a text label. */
export const SEV: Record<Severity, { color: string; soft: string; text: string }> = {
  Critical: { color: "#6B2D0A", soft: "#F3E6DC", text: "text-white" },
  High: { color: "#A1620C", soft: "#F6ECDA", text: "text-white" },
  Medium: { color: "#C9A25A", soft: "#F7F1E3", text: "text-ink" },
  Low: { color: "#BDB9C6", soft: "#F1F0F4", text: "text-ink" },
};
export const SEV_ORDER: Severity[] = ["Critical", "High", "Medium", "Low"];

export const SOURCE_SHORT: Record<string, string> = { social: "Social", print: "Print", tv: "TV", youtube: "YouTube", survey: "Survey", other: "Other" };

export function ThreatScore({ score, severity, size = "md" }: { score: number; severity: Severity; size?: "sm" | "md" | "lg" }) {
  const s = SEV[severity];
  const dims = size === "lg" ? "w-16 h-16 text-[28px]" : size === "sm" ? "w-9 h-9 text-[15px]" : "w-12 h-12 text-[21px]";
  return (
    <span className={`narrow ${dims} ${s.text} shrink-0 grid place-items-center rounded-md font-semibold leading-none`}
          style={{ background: s.color }} title={`Threat score ${score} of 100 (${severity})`}>
      {score}
      <span className="sr-only"> of 100, {severity}</span>
    </span>
  );
}

export function SeverityTag({ severity }: { severity: Severity }) {
  const s = SEV[severity];
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px] font-medium whitespace-nowrap">
      <span aria-hidden className="w-2 h-2 rounded-full" style={{ background: s.color }} />{severity}
    </span>
  );
}

/** 72-hour volume in 6-hour buckets, oldest to newest. */
export function Sparkline({ values, severity, height = 22 }: { values: number[]; severity: Severity; height?: number }) {
  if (!values.length) return null;
  const max = Math.max(1, ...values);
  const W = values.length * 10;
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" role="img" className="block"
         aria-label={`Items per 6 hours over the last 72 hours, oldest first: ${values.join(", ")}`}>
      {values.map((v, i) => {
        const h = v ? Math.max(3, (v / max) * (height - 1)) : 1.5;
        return <rect key={i} x={i * 10 + 1} y={height - h} width={8} height={h} rx={1}
                     fill={v ? SEV[severity].color : "#DCDDE3"} opacity={v && i < values.length - 4 ? 0.55 : 1} />;
      })}
    </svg>
  );
}

export function NegativityBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className="inline-flex items-center gap-1.5" title={`Negativity ${pct}%`}>
      <span className="w-12 h-1.5 rounded-full bg-line overflow-hidden">
        <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: value >= 0.4 ? "#A1620C" : "#7C7889" }} />
      </span>
      <span className="narrow text-[12.5px] text-ink-3 w-7">{pct}%</span>
    </span>
  );
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "–";
  const m = Math.round((now - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export function CopyButton({ text, label = "Copy", className = "" }: { text: string; label?: string; className?: string }) {
  return (
    <button type="button" data-copy={label}
            className={`no-print h-8 px-2.5 rounded-md border border-line text-[13px] text-ink-2 hover:border-ink hover:text-ink ${className}`}
            onClick={async (e) => {
              const btn = e.currentTarget;
              try {
                await navigator.clipboard.writeText(text);
                btn.textContent = "Copied";
              } catch {
                btn.textContent = "Copy blocked";
              }
              window.setTimeout(() => { btn.textContent = label; }, 1500);
            }}>
      {label}
    </button>
  );
}

const strip = (t: string) => t.replace(/\s*\[S\d+\]/g, "");

/** Copy-ready text of the medium-specific output (no [S#] markers, which are for the war room, not the public). */
export function copyText(r: WrCounter): string {
  const c = r.copy;
  switch (r.medium) {
    case "tv_brief":
      return [c.headline_message && `HEADLINE MESSAGE\n${c.headline_message}`,
              c.talking_points?.length && `TALKING POINTS\n${c.talking_points.map((x) => `• ${strip(x)}`).join("\n")}`,
              c.bridging_lines?.length && `BRIDGING LINES\n${c.bridging_lines.map((x) => `• ${x}`).join("\n")}`,
              c.likely_questions?.length && `LIKELY QUESTIONS\n${c.likely_questions.map((x) => `Q: ${x.q}\nA: ${strip(x.a)}`).join("\n\n")}`]
        .filter(Boolean).join("\n\n");
    case "whatsapp":
      return c.text ?? "";
    case "press_release":
      return [c.headline, ...(c.body ?? []).map(strip), c.quote?.text ? `“${c.quote.text}” — ${c.quote.attribution}` : ""]
        .filter(Boolean).join("\n\n");
    default:
      return (c.variants ?? []).map((v, i) => `Variant ${i + 1}\nHeadline: ${v.headline}\nBody: ${v.body}\nCTA: ${v.cta}`).join("\n\n");
  }
}

export function fullText(r: WrCounter): string {
  const used = r.sources.filter((s) => s.used);
  return [`${r.medium_label}: ${r.label}`, "",
    "1. THREAT SUMMARY", r.threat_summary, `Core claim: ${r.core_claim}`, `Assessment: ${r.assessment.verdict_label}. ${r.assessment.explanation}`, "",
    "2. VERIFIED COUNTER-FACTS", ...r.counter_facts.map((f) => `• ${f.fact}`), "",
    `3. ${r.medium_label.toUpperCase()}`, copyText(r), "",
    "4. ECI / MCC SAFETY CHECK", ...r.safety_check.checks.map((c) => `[${c.status}] ${c.rule}: ${c.detail}`), "",
    "SOURCES", ...used.map((s) => `[${s.id}] ${s.citation}`)].join("\n");
}
