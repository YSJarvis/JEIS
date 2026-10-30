"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { fmt, Q, Q_ORDER } from "@/lib/quadrants";
import type { Meta, ScoringConfig, ScoringJob, StateCode } from "@/lib/types";

type Group = "swing_weights" | "turnout_weights";
interface Draft { swing_weights: Record<string, number>; turnout_weights: Record<string, number>; migrant_penalty: number }

const pct = (w: Record<string, number>) => Object.fromEntries(Object.entries(w).map(([k, v]) => [k, Math.round(v * 1000) / 10]));
const same = (a: object, b: object) => JSON.stringify(a) === JSON.stringify(b);

export default function ScoringConfigModal({ initialState, onClose, onDone }: {
  initialState: StateCode; onClose: () => void; onDone: () => void;
}) {
  const [cfg, setCfg] = useState<ScoringConfig | null>(null);
  const [state, setState] = useState<StateCode>(initialState);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [thresholds, setThresholds] = useState<Meta["thresholds"] | null>(null);
  const [job, setJob] = useState<ScoringJob | null>(null);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDivElement>(null);

  function load(c: ScoringConfig) {
    setCfg(c);
    setDrafts(Object.fromEntries(Object.entries(c.states).map(([k, v]) => [k, {
      swing_weights: pct(v.current.swing_weights), turnout_weights: pct(v.current.turnout_weights),
      migrant_penalty: Math.round(v.current.migrant_penalty * 100),
    }])));
    setThresholds(c.thresholds.current);
  }
  useEffect(() => { api.scoringConfig().then(load).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !running) onClose(); };
    window.addEventListener("keydown", onKey);
    dialog.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  });

  // poll the background job
  useEffect(() => {
    if (!job || job.status === "DONE" || job.status === "FAILED") return;
    const t = setTimeout(() => api.scoringJob(job.job_id).then((j) => {
      setJob(j);
      if (j.status === "DONE") { onDone(); api.scoringConfig().then(load); }
    }).catch((e) => setError(e.message)), 600);
    return () => clearTimeout(t);
  }, [job, onDone]);

  const running = job?.status === "QUEUED" || job?.status === "RUNNING";
  const d = drafts[state];
  const s = cfg?.states[state];
  const original = useMemo(() => s && {
    swing_weights: pct(s.current.swing_weights), turnout_weights: pct(s.current.turnout_weights),
    migrant_penalty: Math.round(s.current.migrant_penalty * 100),
  }, [s]);
  const weightsDirty = !!(d && original && !same(d, original));
  const thresholdsDirty = !!(thresholds && cfg && !same(thresholds, cfg.thresholds.current));

  /** Set one factor's share; the others scale proportionally so shares stay at 100%. */
  function setWeight(group: Group, key: string, v: number) {
    setDrafts((all) => {
      const cur = all[state][group];
      const others = Object.keys(cur).filter((k) => k !== key);
      const rest = others.reduce((a, k) => a + cur[k], 0);
      const next: Record<string, number> = { [key]: v };
      for (const k of others) next[k] = Math.round((rest > 0 ? (cur[k] / rest) * (100 - v) : (100 - v) / others.length) * 10) / 10;
      return { ...all, [state]: { ...all[state], [group]: { ...cur, ...next } } };
    });
  }

  async function save(reset = false) {
    if (!d) return;
    setError("");
    try {
      const body = reset ? { state, reset: true } : {
        state,
        ...(weightsDirty ? { swing_weights: d.swing_weights, turnout_weights: d.turnout_weights, migrant_penalty: d.migrant_penalty / 100 } : {}),
        ...(thresholdsDirty && thresholds ? { thresholds: { swing_high: thresholds.swing_high, turnout_high: thresholds.turnout_high } } : {}),
      };
      const res = await api.recalibrate(body);
      setJob(res.job);
    } catch (e) { setError((e as Error).message); }
  }

  return (
    <div className="fixed inset-0 z-[1000] bg-ink/40 grid place-items-center p-4" onClick={() => !running && onClose()}>
      <div ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="sw-title" onClick={(e) => e.stopPropagation()}
           className="w-full max-w-3xl max-h-[92dvh] overflow-y-auto bg-panel rounded-lg p-6 outline-none">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="sw-title" className="text-[22px] font-semibold">Scoring weights</h2>
            <p className="text-[14.5px] text-ink-2 mt-1">How much each factor counts toward a voter’s scores. Saving recalculates every voter in the state in the background.</p>
          </div>
          <button onClick={onClose} disabled={running} className="text-ink-3 hover:text-ink text-[15px] disabled:opacity-40">Close</button>
        </div>

        {!cfg && !error && <p className="mt-6 text-ink-3">Loading weights…</p>}
        {cfg && d && s && (
          <>
            <div className="flex flex-wrap gap-1 mt-5" role="tablist" aria-label="State">
              {(Object.keys(cfg.states) as StateCode[]).map((k) => (
                <button key={k} role="tab" aria-selected={k === state} onClick={() => setState(k)} disabled={running}
                        className={`px-3 py-1.5 rounded-md text-[14.5px] ${k === state ? "bg-ink text-white" : "text-ink-2 hover:bg-paper"}`}>
                  {cfg.states[k].name}{cfg.states[k].customised ? " •" : ""}
                </button>
              ))}
            </div>
            <p className="text-[13px] text-ink-3 mt-2">
              {s.customised && s.updated ? `Customised · last changed ${new Date(s.updated.updated_at).toLocaleString()}` : "Using default weights"}
            </p>

            <div className="grid md:grid-cols-2 gap-6 mt-4">
              <WeightGroup title="Swing propensity" labels={cfg.labels.swing} values={d.swing_weights}
                           defaults={pct(s.default.swing_weights)} onChange={(k, v) => setWeight("swing_weights", k, v)} disabled={running} />
              <div>
                <WeightGroup title="Turnout probability" labels={cfg.labels.turnout} values={d.turnout_weights}
                             defaults={pct(s.default.turnout_weights)} onChange={(k, v) => setWeight("turnout_weights", k, v)} disabled={running} />
                <Slider label="Turnout cut for migrants and NRIs" value={d.migrant_penalty} max={95} suffix="%"
                        hint={`Default ${Math.round(s.default.migrant_penalty * 100)}%`} disabled={running}
                        onChange={(v) => setDrafts((all) => ({ ...all, [state]: { ...all[state], migrant_penalty: v } }))} />
              </div>
            </div>

            {thresholds && (
              <details className="mt-6 border-t border-line pt-4">
                <summary className="cursor-pointer text-[15px] font-semibold">Segment cut-offs (all states)</summary>
                <div className="grid md:grid-cols-2 gap-6 mt-3">
                  <Slider label="Persuadable when swing is at least" value={thresholds.swing_high} min={30} max={90} disabled={running}
                          hint={`Default ${cfg.thresholds.default.swing_high}`} onChange={(v) => setThresholds({ ...thresholds, swing_high: v })} />
                  <Slider label="Likely voter when turnout is at least" value={thresholds.turnout_high} min={30} max={90} disabled={running}
                          hint={`Default ${cfg.thresholds.default.turnout_high}`} onChange={(v) => setThresholds({ ...thresholds, turnout_high: v })} />
                </div>
                <p className="text-[13px] text-ink-3 mt-2">Changing a cut-off recalculates all four states.</p>
              </details>
            )}

            {job && <JobResult job={job} />}
            {error && <p role="alert" className="mt-4 text-[14px] text-amber">{error}</p>}

            <div className="flex flex-wrap items-center gap-3 mt-6 pt-4 border-t border-line">
              <button onClick={() => save(true)} disabled={running || !s.customised}
                      className="text-[14.5px] text-ink-2 underline underline-offset-2 disabled:opacity-40 disabled:no-underline">
                Reset {s.name} to defaults
              </button>
              <button onClick={() => original && setDrafts((all) => ({ ...all, [state]: original }))} disabled={running || !weightsDirty}
                      className="text-[14.5px] text-ink-2 underline underline-offset-2 disabled:opacity-40 disabled:no-underline">
                Undo changes
              </button>
              <button onClick={() => save(false)} disabled={running || (!weightsDirty && !thresholdsDirty)}
                      className="ml-auto h-10 px-4 rounded-md bg-violet text-white font-medium disabled:opacity-40">
                {running ? "Recalculating…" : "Save and recalculate"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function WeightGroup({ title, labels, values, defaults, onChange, disabled }: {
  title: string; labels: Record<string, string>; values: Record<string, number>; defaults: Record<string, number>;
  onChange: (k: string, v: number) => void; disabled: boolean;
}) {
  return (
    <fieldset>
      <legend className="text-[16px] font-semibold">{title}</legend>
      <p className="text-[13px] text-ink-3">Share of the score. Moving one factor adjusts the others so they add up to 100%.</p>
      {Object.keys(values).map((k) => {
        const share = Math.round(values[k]);
        return (
          <div key={k} className="mt-3">
            <div className="flex items-baseline justify-between gap-2">
              <label htmlFor={`w-${title}-${k}`} className="text-[14.5px]">{labels[k] ?? k}</label>
              <span className="narrow text-[19px] font-semibold w-12 text-right">{share}%</span>
            </div>
            <input id={`w-${title}-${k}`} type="range" min={0} max={100} step={1} value={values[k]} disabled={disabled}
                   onChange={(e) => onChange(k, +e.target.value)} className="w-full accent-[#5B2E91]"
                   aria-valuetext={`${share}% of score`} />
            {Math.round(defaults[k]) !== Math.round(values[k]) && (
              <p className="text-[12px] text-ink-3">Default {Math.round(defaults[k])}%</p>
            )}
          </div>
        );
      })}
    </fieldset>
  );
}

function Slider({ label, value, onChange, min = 0, max = 100, suffix = "", hint, disabled }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; suffix?: string; hint?: string; disabled: boolean;
}) {
  return (
    <div className="mt-4">
      <div className="flex items-baseline justify-between gap-2">
        <label className="text-[14.5px]">{label}</label>
        <span className="narrow text-[19px] font-semibold">{value}{suffix}</span>
      </div>
      <input type="range" min={min} max={max} value={value} disabled={disabled} onChange={(e) => onChange(+e.target.value)}
             className="w-full accent-[#5B2E91]" aria-label={label} />
      {hint && <p className="text-[12px] text-ink-3">{hint}</p>}
    </div>
  );
}

function JobResult({ job }: { job: ScoringJob }) {
  if (job.status === "FAILED") return <p role="alert" className="mt-5 text-[14px] text-amber">Recalculation failed: {job.error?.split("\n")[0]}</p>;
  if (job.status !== "DONE") {
    return <p role="status" className="mt-5 text-[14.5px] text-ink-2">Recalculating {job.scope === "ALL" ? "all states" : job.scope}… you can keep this open.</p>;
  }
  const { before = {}, after = {}, scored } = job.result;
  return (
    <div role="status" className="mt-5 rounded-md bg-paper p-4">
      <p className="text-[14.5px] font-medium">Recalculated {fmt(scored)} voters. Segment sizes before and after:</p>
      <table className="w-full mt-2 text-[14px]">
        <thead><tr className="text-left text-ink-3 text-[13px]"><th className="font-normal py-1">State</th>
          {Q_ORDER.map((q) => <th key={q} className="font-normal py-1 text-right">{Q[q].action}</th>)}</tr></thead>
        <tbody>
          {Object.keys(after).map((st) => (
            <tr key={st} className="border-t border-line">
              <td className="py-1.5">{st}</td>
              {Q_ORDER.map((q) => {
                const b = before[st]?.[q] ?? 0, a = after[st]?.[q] ?? 0, diff = a - b;
                return (
                  <td key={q} className="py-1.5 text-right">
                    {fmt(a)} <span className={`text-[12.5px] ${diff > 0 ? "text-gotv" : diff < 0 ? "text-amber" : "text-ink-3"}`}>
                      {diff === 0 ? "±0" : `${diff > 0 ? "+" : "−"}${fmt(Math.abs(diff))}`}
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
