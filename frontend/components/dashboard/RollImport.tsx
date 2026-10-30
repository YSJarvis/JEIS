"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { fmt } from "@/lib/quadrants";
import type { RollIssue, RollOptions, RollPreview, RollReport, RollTarget, ScoringJob } from "@/lib/types";

const STATES = [["UP", "Uttar Pradesh"], ["PB", "Punjab"], ["UK", "Uttarakhand"], ["GA", "Goa"]] as const;
const ORDER: RollTarget[] = ["epic", "name", "relative_name", "age", "gender", "house_no", "booth", "relation_type",
  "serial_no", "section", "constituency", "state"];
const CONF: Record<string, { label: string; cls: string }> = {
  high: { label: "Matched", cls: "text-gotv" },
  medium: { label: "Check this", cls: "text-amber" },
  low: { label: "Guess: check", cls: "text-amber" },
};
const KIND_LABEL: Record<RollIssue["kind"], string> = { error: "Rejected", duplicate: "Duplicate", warning: "Warning" };

export default function RollImport({ defaultState, onDone }: { defaultState: string; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [options, setOptions] = useState<RollOptions>({ state: defaultState, constituency: "", on_existing: "update" });
  const [constituencies, setConstituencies] = useState<string[]>([]);
  const [preview, setPreview] = useState<RollPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null> | null>(null);
  const [checking, setChecking] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<RollReport | null>(null);
  const [job, setJob] = useState<ScoringJob | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<RollIssue["kind"] | "all">("all");
  const seq = useRef(0);

  useEffect(() => {
    api.booths(options.state).then((b) => setConstituencies([...new Set(b.map((x) => x.constituency))].sort())).catch(() => setConstituencies([]));
  }, [options.state]);

  // (re)check the file whenever it, the options or the mapping change
  useEffect(() => {
    if (!file) return;
    const n = ++seq.current;
    setChecking(true); setError(""); setResult(null);
    const t = setTimeout(async () => {
      try {
        const p = await api.rollPreview(file, options, mapping ?? undefined);
        if (n !== seq.current) return;
        setPreview(p);
        if (!mapping) setMapping(p.mapping);
      } catch (e) {
        if (n === seq.current) { setError((e as Error).message); setPreview(null); }
      } finally {
        if (n === seq.current) setChecking(false);
      }
    }, mapping ? 400 : 0);
    return () => clearTimeout(t);
  }, [file, options, mapping]);

  // poll the scoring job that follows an import
  useEffect(() => {
    if (!job || job.status === "DONE" || job.status === "FAILED") return;
    const t = setTimeout(() => api.scoringJob(job.job_id).then((j) => { setJob(j); if (j.status === "DONE") onDone(); }), 700);
    return () => clearTimeout(t);
  }, [job, onDone]);

  const report = preview?.report;
  const s = report?.stats;
  const ready = s ? s.created + s.updated + s.linked_epic : 0;
  const issues = useMemo(() => (report?.issues ?? []).filter((i) => filter === "all" || i.kind === filter), [report, filter]);

  async function commit() {
    if (!file || !mapping) return;
    setImporting(true); setError("");
    try {
      const r = await api.rollImport(file, options, mapping);
      setResult(r);
      if (r.scoring && "job_id" in r.scoring) setJob(r.scoring);
      onDone();
    } catch (e) { setError((e as Error).message); }
    finally { setImporting(false); }
  }

  function chooseFile(f: File | null) {
    setFile(f); setMapping(null); setPreview(null); setResult(null); setJob(null);
  }

  return (
    <div className="mt-4">
      <p className="text-[14.5px] text-ink-2">
        Upload a roll as CSV, or the JSON your PDF roll parser produces. Columns are matched automatically (English or Hindi headers);
        check the matches, review duplicates and problems, then import. Roll updates never overwrite scheme, survey or consent data already held.
      </p>

      <div className="grid sm:grid-cols-3 gap-3 mt-4">
        <label className="block sm:col-span-3">
          <span className="text-[14px] font-medium">Roll file</span>
          <input type="file" accept=".csv,.json,text/csv,application/json" onChange={(e) => chooseFile(e.target.files?.[0] ?? null)}
                 className="mt-1 block w-full text-[14px] file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border file:border-line file:bg-paper" />
        </label>
        <label className="block">
          <span className="text-[14px] font-medium">State</span>
          <select value={options.state} onChange={(e) => setOptions({ ...options, state: e.target.value, constituency: "" })}
                  className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]">
            {STATES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="text-[14px] font-medium">Constituency</span>
          <input list="ac-list" value={options.constituency} placeholder="Needed for part numbers"
                 onChange={(e) => setOptions({ ...options, constituency: e.target.value })}
                 className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]" />
          <datalist id="ac-list">{constituencies.map((c) => <option key={c} value={c} />)}</datalist>
        </label>
        <label className="block">
          <span className="text-[14px] font-medium">Voters already on file</span>
          <select value={options.on_existing} onChange={(e) => setOptions({ ...options, on_existing: e.target.value as RollOptions["on_existing"] })}
                  className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]">
            <option value="update">Update roll details</option>
            <option value="skip">Leave unchanged</option>
          </select>
        </label>
      </div>

      {error && <p role="alert" className="mt-4 px-3 py-2 rounded-md bg-violet-soft text-[14px]">{error}</p>}
      {checking && !preview && <p className="mt-4 text-[14px] text-ink-3">Reading the file…</p>}

      {preview && mapping && (
        <section className="mt-6" aria-labelledby="map-h">
          <div className="flex items-baseline justify-between gap-3">
            <h3 id="map-h" className="text-[17px] font-semibold">Match columns</h3>
            <span className="text-[13px] text-ink-3">{fmt(preview.rows)} rows · {preview.format.toUpperCase()} · {preview.columns.length} columns</span>
          </div>
          <table className="w-full mt-2 text-[14px]">
            <thead>
              <tr className="text-left text-ink-3 text-[13px] border-y border-line">
                <th className="font-normal py-2 pr-2">Field</th>
                <th className="font-normal py-2 px-2">Column in your file</th>
                <th className="font-normal py-2 px-2">Sample values</th>
              </tr>
            </thead>
            <tbody>
              {ORDER.map((t) => {
                const meta = preview.targets[t];
                const col = mapping[t];
                const conf = col && preview.suggested[t] === col ? preview.confidence[t] : col ? "manual" : null;
                return (
                  <tr key={t} className="border-b border-line align-top">
                    <td className="py-2 pr-2">
                      <span className="font-medium">{meta.label}</span>
                      {meta.required && <span className="text-ink-3 text-[12.5px]"> · required</span>}
                      {conf && conf !== "manual" && <span className={`block text-[12.5px] ${CONF[conf].cls}`}>{CONF[conf].label}</span>}
                      {conf === "manual" && <span className="block text-[12.5px] text-ink-3">Set by you</span>}
                    </td>
                    <td className="py-2 px-2">
                      <select aria-label={`Column for ${meta.label}`} value={col ?? ""}
                              onChange={(e) => setMapping({ ...mapping, [t]: e.target.value || null })}
                              className={`h-9 w-full px-2 rounded-md border bg-panel text-[14px] ${meta.required && !col ? "border-amber" : "border-line"}`}>
                        <option value="">Not in file</option>
                        {preview.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </td>
                    <td className="py-2 px-2 text-ink-2 text-[13px]">
                      {col ? preview.sample.slice(0, 3).map((r) => String(r[col] ?? "")).filter(Boolean).join(" · ") || "—" : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {preview.blocking && <p role="alert" className="mt-3 text-[14px] text-amber">{preview.blocking}</p>}
        </section>
      )}

      {s && !result && (
        <section className="mt-6" aria-labelledby="chk-h" aria-busy={checking}>
          <h3 id="chk-h" className="text-[17px] font-semibold">Check {checking && <span className="text-[13px] font-normal text-ink-3">updating…</span>}</h3>
          <dl className="grid grid-cols-2 sm:grid-cols-4 border-y border-line mt-2">
            {[
              ["Ready to import", fmt(ready), `${fmt(s.created)} new · ${fmt(s.updated)} updated${s.linked_epic ? ` · ${s.linked_epic} linked to EPIC` : ""}`],
              ["Duplicates removed", fmt(s.duplicates_in_file + s.possible_duplicates), `${fmt(s.duplicates_in_file)} in file · ${fmt(s.possible_duplicates)} already on file`],
              ["Rejected rows", fmt(s.invalid), s.nonstandard_epic ? `${s.nonstandard_epic} odd EPIC formats kept` : "missing or invalid fields"],
              ["New booths", fmt(s.booths_created), s.booths_created ? "need map locations" : `${fmt(s.unchanged + s.skipped_existing)} voters unchanged`],
            ].map(([k, v, sub], i) => (
              <div key={k} className={`px-3 py-2.5 ${i ? "sm:border-l border-line" : ""}`}>
                <dt className="text-[13px] text-ink-3">{k}</dt>
                <dd className="narrow text-[28px] font-semibold leading-tight">{v}</dd>
                <dd className="text-[12.5px] text-ink-3">{sub}</dd>
              </div>
            ))}
          </dl>
          {report!.new_booths.length > 0 && (
            <p className="mt-3 text-[13.5px] text-ink-2">
              New booths {report!.new_booths.slice(0, 6).map((b) => b.booth_id).join(", ")}{report!.new_booths.length > 6 ? "…" : ""} will be placed at the
              {report!.new_booths[0].geo_fallback === "state_centre" ? " state" : " constituency"} centre until you add coordinates, so routes there follow house numbers loosely.
            </p>
          )}

          {report!.issues.length > 0 && (
            <div className="mt-4">
              <div className="flex gap-1" role="tablist" aria-label="Filter issues">
                {(["all", "error", "duplicate", "warning"] as const).map((k) => (
                  <button key={k} role="tab" aria-selected={filter === k} onClick={() => setFilter(k)}
                          className={`px-2.5 py-1 rounded-full text-[13px] border ${filter === k ? "border-ink bg-ink text-white" : "border-line text-ink-2"}`}>
                    {k === "all" ? "All issues" : KIND_LABEL[k]}
                  </button>
                ))}
              </div>
              <ul className="mt-2 max-h-56 overflow-y-auto divide-y divide-line border-y border-line text-[13.5px]">
                {issues.map((i, n) => (
                  <li key={n} className="py-1.5 flex gap-3">
                    <span className="w-14 shrink-0 text-ink-3">Row {i.row ?? "–"}</span>
                    <span className={`w-20 shrink-0 ${i.kind === "error" ? "text-amber" : "text-ink-3"}`}>{KIND_LABEL[i.kind]}</span>
                    <span className="flex-1 min-w-0">{[i.name, i.epic].filter(Boolean).join(" · ")}{(i.name || i.epic) ? ": " : ""}{i.message}</span>
                  </li>
                ))}
                {!issues.length && <li className="py-2 text-ink-3">Nothing in this group.</li>}
              </ul>
            </div>
          )}

          <button onClick={commit} disabled={importing || checking || !ready || !!preview?.blocking}
                  className="mt-5 h-10 px-4 rounded-md bg-violet text-white font-medium disabled:opacity-40">
            {importing ? "Importing…" : `Import ${fmt(ready)} voters`}
          </button>
        </section>
      )}

      {result && (
        <section className="mt-6 rounded-md bg-paper p-4" role="status" aria-live="polite">
          <p className="text-[15px] font-medium">
            Imported: {fmt(result.stats.created)} new, {fmt(result.stats.updated)} updated
            {result.stats.linked_epic ? `, ${result.stats.linked_epic} linked to EPIC` : ""}. {fmt(result.stats.households_created)} households and {fmt(result.stats.booths_created)} booths added.
          </p>
          <p className="text-[14px] text-ink-2 mt-1">
            {result.scoring && "error" in result.scoring && result.scoring.status === "DEFERRED" ? result.scoring.error
              : !job ? "" : job.status === "DONE" ? `Scored with the current ${result.states.join(", ")} weights: ${fmt(job.result.scored)} voters.`
              : job.status === "FAILED" ? `Scoring failed: ${job.error?.split("\n")[0]}` : "Scoring the new voters…"}
          </p>
          <button onClick={() => chooseFile(null)} className="mt-2 text-[14px] text-violet underline underline-offset-2">Import another file</button>
        </section>
      )}
    </div>
  );
}
