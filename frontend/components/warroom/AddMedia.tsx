"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { WrIngestResult, WrMeta } from "@/lib/types";
import { STATE_NAME, ThreatScore } from "./shared";

function localNow() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export default function AddMedia({ meta, isAdmin, onClose, onDone }: {
  meta: WrMeta; isAdmin: boolean; onClose: () => void; onDone: () => void;
}) {
  const manager = meta.scope.role === "MANAGER";
  const [mode, setMode] = useState<"item" | "file">("item");
  const [text, setText] = useState("");
  const [state, setState] = useState(manager ? meta.scope.state ?? "" : "");
  const [constituency, setConstituency] = useState(manager ? meta.scope.constituency ?? "" : "");
  const [source, setSource] = useState("social");
  const [channel, setChannel] = useState("");
  const [when, setWhen] = useState(localNow());
  const [url, setUrl] = useState("");
  const [reach, setReach] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<WrIngestResult[] | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("textarea, input, select")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [onClose]);

  const defaults = { state: state || undefined, constituency: constituency || undefined, source_type: source,
                     channel: channel || undefined, timestamp: when ? new Date(when).toISOString() : undefined };

  async function submit() {
    setBusy(true); setError(""); setResults(null);
    try {
      const r = mode === "item"
        ? await api.wrIngest([{ ...defaults, text, url: url || undefined, reach: reach ? Number(reach) : undefined }])
        : await api.wrIngestFiles(files, defaults);
      setResults(r.results);
      if (r.summary.ingested) { onDone(); if (mode === "item") setText(""); }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function removeSample() {
    if (!confirm("Remove every synthetic sample item? Real items you added stay.")) return;
    try { await api.wrSample({ remove: true }); onDone(); onClose(); } catch (e) { setError((e as Error).message); }
  }

  const input = "mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[14.5px]";
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink/30" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="add-h"
           className="h-full w-full max-w-[520px] bg-panel shadow-xl overflow-y-auto">
        <div className="sticky top-0 bg-panel flex items-center justify-between px-5 h-14 border-b border-line">
          <h2 id="add-h" className="text-[18px] font-semibold">Add media</h2>
          <button onClick={onClose} className="text-[14px] text-ink-3 underline">Close</button>
        </div>
        <form className="px-5 py-4 space-y-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="flex gap-1" role="tablist">
            {([["item", "One item"], ["file", "Upload a file"]] as const).map(([k, l]) => (
              <button key={k} type="button" role="tab" aria-selected={mode === k} onClick={() => setMode(k)}
                      className={`px-3 py-1.5 rounded-md text-[14.5px] ${mode === k ? "bg-ink text-white" : "text-ink-2 border border-line"}`}>{l}</button>
            ))}
          </div>

          {mode === "item" ? (
            <label className="block">
              <span className="text-[14px] font-medium">Text</span>
              <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} required minLength={20} maxLength={5000}
                        placeholder="Paste the news snippet, post, transcript excerpt or survey finding"
                        className="mt-1 w-full px-2.5 py-2 rounded-md border border-line text-[14.5px]" />
              <span className="text-[12.5px] text-ink-3">Phone numbers and email addresses are removed automatically.</span>
            </label>
          ) : (
            <label className="block">
              <span className="text-[14px] font-medium">CSV, JSON or TXT</span>
              <input type="file" multiple accept=".csv,.json,.txt" onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                     className="mt-1 block w-full text-[14px]" />
              <span className="block text-[12.5px] text-ink-3 mt-1">
                CSV columns: text, channel, source_type, state, constituency, timestamp, url, reach. TXT: one item per paragraph.
                The fields below fill in anything a row leaves out.
              </span>
            </label>
          )}

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-[14px] font-medium">Source type</span>
              <select value={source} onChange={(e) => setSource(e.target.value)} className={input}>
                {Object.entries(meta.source_types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="text-[14px] font-medium">Channel name</span>
              <input value={channel} onChange={(e) => setChannel(e.target.value)} required={mode === "item"} maxLength={120}
                     placeholder="outlet, page or programme" className={input} />
            </label>
            <label className="block">
              <span className="text-[14px] font-medium">State</span>
              <select value={state} onChange={(e) => setState(e.target.value)} disabled={manager} className={input}>
                <option value="">Detect from text</option>
                {Object.entries(STATE_NAME).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="text-[14px] font-medium">Constituency</span>
              <input value={constituency} onChange={(e) => setConstituency(e.target.value)} disabled={manager}
                     placeholder="detect from text" className={input} />
            </label>
            <label className="block">
              <span className="text-[14px] font-medium">Published</span>
              <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} className={input} />
            </label>
            {mode === "item" && (
              <label className="block">
                <span className="text-[14px] font-medium">Reach <span className="font-normal text-ink-3">(optional)</span></span>
                <input type="number" min={0} value={reach} onChange={(e) => setReach(e.target.value)} placeholder="views or circulation" className={input} />
              </label>
            )}
          </div>
          {mode === "item" && (
            <label className="block">
              <span className="text-[14px] font-medium">Link <span className="font-normal text-ink-3">(optional)</span></span>
              <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} className={input} />
            </label>
          )}

          <button disabled={busy || (mode === "file" ? !files.length : text.trim().length < 20)}
                  className="w-full h-11 rounded-md bg-violet text-white font-medium disabled:opacity-50">
            {busy ? "Scoring…" : mode === "item" ? "Add and score" : `Upload ${files.length || ""} file${files.length === 1 ? "" : "s"}`}
          </button>
          {error && <p role="alert" className="text-[14px] text-amber">{error}</p>}
        </form>

        {results && (
          <div className="px-5 pb-5" aria-live="polite">
            <h3 className="text-[15px] font-semibold">Result</h3>
            <ul className="mt-2 space-y-2">
              {results.slice(0, 50).map((r, i) => (
                <li key={i} className="rounded-md border border-line px-3 py-2 text-[14px] flex items-center gap-3">
                  {r.status === "error" ? <span className="text-amber">{r.ref}: {r.error}</span> : (
                    <>
                      {r.severity && <ThreatScore score={r.threat_score ?? 0} severity={r.severity} size="sm" />}
                      <span>
                        {r.status === "duplicate" ? "Already logged" : r.new_cluster ? "Started a new narrative" : "Joined"}{" "}
                        <span className="font-medium">{r.cluster_label}</span>
                        <span className="block text-[12.5px] text-ink-3">{[r.sentiment, r.constituency, r.state].filter(Boolean).join(" · ")}</span>
                      </span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {isAdmin && meta.media.sample_items > 0 && (
          <div className="mx-5 mb-6 p-3 rounded-md bg-paper text-[13.5px]">
            <p>{meta.media.sample_items} synthetic sample items are loaded for the demo.</p>
            <button onClick={removeSample} className="mt-1 underline">Remove the sample feed</button>
          </div>
        )}
      </div>
    </div>
  );
}
