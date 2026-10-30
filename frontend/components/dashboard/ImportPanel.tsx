"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { IngestReport } from "@/lib/types";
import RollImport from "./RollImport";

const COLUMNS = "voter_id (or EPIC), name, age, booth_id (or part_no), state · optional: gender, household_id, community_group (or caste), scheme_beneficiary, scheme_types, voting_history_index, party_affinity, is_migrant, phone, lat, lng, address, consent";

type Tab = "roll" | "enriched";

export default function ImportPanel({ state, onClose, onDone }: { state: string; onClose: () => void; onDone: () => void }) {
  const [tab, setTab] = useState<Tab>("roll");
  return (
    <div className="fixed inset-0 z-[1000] bg-ink/40 flex justify-end" onClick={onClose}>
      <aside role="dialog" aria-modal="true" aria-labelledby="imp" onClick={(e) => e.stopPropagation()}
             className="w-full max-w-3xl h-full bg-panel p-6 overflow-y-auto">
        <div className="flex items-start justify-between">
          <h2 id="imp" className="text-[22px] font-semibold">Import voter data</h2>
          <button onClick={onClose} className="text-ink-3 hover:text-ink text-[15px]">Close</button>
        </div>
        <div className="flex gap-1 mt-4" role="tablist">
          {([["roll", "Electoral roll"], ["enriched", "Survey or CRM data"]] as const).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                    className={`px-3 py-1.5 rounded-md text-[14.5px] ${tab === k ? "bg-ink text-white" : "text-ink-2 hover:bg-paper"}`}>{l}</button>
          ))}
        </div>
        {tab === "roll" ? <RollImport defaultState={state} onDone={onDone} /> : <EnrichedImport state={state} onDone={onDone} />}
      </aside>
    </div>
  );
}

function EnrichedImport({ state, onDone }: { state: string; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"append" | "replace">("append");
  const [busy, setBusy] = useState("");
  const [report, setReport] = useState<IngestReport | null>(null);
  const [error, setError] = useState("");

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label); setError("");
    try { await fn(); onDone(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  }

  return (
    <div className="max-w-md">
      <p className="text-[14.5px] text-ink-2 mt-4">For records that already carry survey or scheme data. Column names are matched loosely, extra columns are kept, and every voter is re-scored after import.</p>
      <p className="text-[13px] text-ink-3 mt-2">{COLUMNS}</p>

      <label className="block mt-5 text-[14px] font-medium" htmlFor="csv">CSV file</label>
      <input id="csv" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
             className="mt-1 block w-full text-[14px] file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border file:border-line file:bg-paper" />

      <fieldset className="mt-4">
        <legend className="text-[14px] font-medium">If records already exist</legend>
        {([["append", "Add and update records"], ["replace", "Replace all data for the states in this file"]] as const).map(([v, l]) => (
          <label key={v} className="flex items-center gap-2 mt-1.5 text-[14.5px]">
            <input type="radio" name="mode" value={v} checked={mode === v} onChange={() => setMode(v)} /> {l}
          </label>
        ))}
      </fieldset>

      <button disabled={!file || !!busy}
              onClick={() => file && run("import", async () => setReport(await api.ingestCsv(file, mode, state)))}
              className="mt-5 w-full h-10 rounded-md bg-violet text-white font-medium disabled:opacity-40">
        {busy === "import" ? "Importing…" : "Import and re-score"}
      </button>

      {error && <p role="alert" className="mt-4 text-[14px] text-amber">{error}</p>}
      {report && (
        <div className="mt-5 border-t border-line pt-4 text-[14.5px]" aria-live="polite">
          <p><b>{report.accepted}</b> voters imported, <b>{report.rejected}</b> rejected, {report.booths_created} new booths.</p>
          {report.errors.length > 0 && (
            <ul className="mt-2 max-h-48 overflow-y-auto text-[13px] text-ink-2">
              {report.errors.map((e, i) => <li key={i}>Row {e.row ?? "–"} {e.voter_id ? `(${e.voter_id})` : ""}: {e.error}</li>)}
            </ul>
          )}
        </div>
      )}

      <div className="mt-8 border-t border-line pt-4 space-y-2">
        <a href="/voters_template.csv" download className="block text-[14.5px] text-violet underline underline-offset-2">Download CSV template</a>
        <button disabled={!!busy} onClick={() => run("score", api.runScoring)} className="block text-[14.5px] text-ink-2 hover:text-ink">
          {busy === "score" ? "Re-scoring…" : "Re-run scoring on current data"}
        </button>
        <button disabled={!!busy} onClick={() => confirm("Replace all data with freshly generated mock data?") && run("seed", api.reseed)}
                className="block text-[14.5px] text-ink-2 hover:text-ink">
          {busy === "seed" ? "Generating…" : "Reset to mock data"}
        </button>
      </div>
    </div>
  );
}
