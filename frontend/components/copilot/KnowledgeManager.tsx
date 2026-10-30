"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { CpConstituency, CpDocument, CpIngestResult, CpMeta, CpQueryResult } from "@/lib/types";
import { CitedText, DOC_TYPE_LABEL } from "./shared";

export default function KnowledgeManager({ meta, place, onChange }: { meta: CpMeta; place: CpConstituency; onChange: () => void }) {
  const { hasRole } = useAuth();
  const isAdmin = hasRole("ADMIN");
  const [docs, setDocs] = useState<CpDocument[]>([]);
  const [filter, setFilter] = useState({ doc_type: "", topic: "", q: "" });
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.cpDocuments({ ...filter }).then((d) => setDocs(d.filter((x) => !x.constituency_id || x.constituency_id === place.id) .filter((x) => !x.state || x.state === place.state)))
      .catch((e) => setError(e.message));
  }, [filter, place]);
  useEffect(() => { load(); }, [load]);

  async function remove(d: CpDocument) {
    if (!confirm(`Remove “${d.title}” from the knowledge base?`)) return;
    try { await api.cpDeleteDocument(d.doc_id); load(); onChange(); } catch (e) { setError((e as Error).message); }
  }

  return (
    <div className="space-y-5">
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-5 items-start">
        <Uploader meta={meta} place={place} isAdmin={isAdmin} onDone={() => { load(); onChange(); }} />
        <SearchTester meta={meta} place={place} />
      </div>

      <section className="bg-panel border border-line rounded-lg" aria-labelledby="docs-h">
        <div className="flex flex-wrap items-end justify-between gap-3 px-4 pt-4">
          <div>
            <h3 id="docs-h" className="text-[17px] font-semibold">Indexed sources</h3>
            <p className="text-[13px] text-ink-3">{docs.length} documents for {place.name}, {place.state}-wide and national.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <select aria-label="Type" value={filter.doc_type} onChange={(e) => setFilter({ ...filter, doc_type: e.target.value })}
                    className="h-9 px-2 rounded-md border border-line bg-panel text-[14px]">
              <option value="">All types</option>
              {Object.entries(meta.doc_types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <select aria-label="Topic" value={filter.topic} onChange={(e) => setFilter({ ...filter, topic: e.target.value })}
                    className="h-9 px-2 rounded-md border border-line bg-panel text-[14px]">
              <option value="">All topics</option>
              {meta.topics.map((t) => <option key={t}>{t}</option>)}
            </select>
            <input aria-label="Search titles" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })}
                   placeholder="Search titles" className="h-9 px-2 rounded-md border border-line bg-panel text-[14px] w-44" />
          </div>
        </div>
        {error && <p role="alert" className="mx-4 mt-2 text-[14px] text-amber">{error}</p>}
        <div className="overflow-x-auto mt-2">
          <table className="w-full text-[14px]">
            <thead>
              <tr className="text-left text-ink-3 text-[13px] border-y border-line">
                <th className="font-normal px-4 py-2">Document</th><th className="font-normal px-2 py-2">Type</th>
                <th className="font-normal px-2 py-2">Topics</th><th className="font-normal px-2 py-2">Covers</th>
                <th className="font-normal px-2 py-2">Date</th><th className="font-normal px-2 py-2 text-right">Passages</th>
                <th className="font-normal px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <DocRow key={d.doc_id} d={d} meta={meta} open={open === d.doc_id} onToggle={() => setOpen(open === d.doc_id ? null : d.doc_id)}
                        onSaved={load} onRemove={() => remove(d)} />
              ))}
              {!docs.length && <tr><td colSpan={7} className="px-4 py-8 text-center text-ink-3">No documents yet. Upload budget notes, assembly questions, news or survey findings above.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Uploader({ meta, place, isAdmin, onDone }: { meta: CpMeta; place: CpConstituency; isAdmin: boolean; onDone: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [scope, setScope] = useState<"constituency" | "state" | "central" | "detect">("constituency");
  const [docType, setDocType] = useState("");
  const [topics, setTopics] = useState<string[]>([]);
  const [citation, setCitation] = useState("");
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<CpIngestResult[] | null>(null);
  const [drag, setDrag] = useState(false);

  async function upload() {
    setBusy(true);
    try {
      const m: Record<string, unknown> = {};
      if (scope === "constituency") m.constituency_id = place.id;
      if (scope === "state") { m.scope = "state"; m.state = place.state; }
      if (scope === "central") m.scope = "central";
      if (docType) m.doc_type = docType;
      if (topics.length) m.topics = topics;
      if (citation) m.source_citation = citation;
      if (date) m.published_on = date;
      const r = await api.cpIngest(files, m);
      setResults(r.results); setFiles([]); onDone();
    } catch (e) { setResults([{ file: "upload", status: "error", error: (e as Error).message }]); }
    finally { setBusy(false); }
  }

  return (
    <section className="bg-panel border border-line rounded-lg p-4" aria-labelledby="up-h">
      <h3 id="up-h" className="text-[17px] font-semibold">Add documents</h3>
      <p className="text-[13px] text-ink-3">PDF, TXT, Markdown or JSON: budget notes, assembly questions, local news, survey findings, party promises. State, constituency, topic and type are detected; set any of them here to override.</p>
      <label onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
             onDrop={(e) => { e.preventDefault(); setDrag(false); setFiles([...files, ...Array.from(e.dataTransfer.files)]); }}
             className={`mt-3 flex flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed px-4 py-5 cursor-pointer text-center ${drag ? "border-violet bg-violet-soft" : "border-line"}`}>
        <span className="text-[14.5px]">{files.length ? files.map((f) => f.name).join(", ") : "Drop files here or choose them"}</span>
        <span className="text-[12.5px] text-ink-3">Up to 20 MB each. Scanned PDFs need OCR first.</span>
        <input type="file" multiple accept=".pdf,.txt,.md,.json,application/pdf,text/plain,application/json" className="sr-only"
               aria-label="Choose documents" onChange={(e) => setFiles([...files, ...Array.from(e.target.files ?? [])])} />
      </label>
      <div className="grid sm:grid-cols-2 gap-3 mt-3">
        <label className="block">
          <span className="text-[13.5px] font-medium">Applies to</span>
          <select value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}
                  className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[14px]">
            <option value="constituency">{place.name}</option>
            {isAdmin && <option value="state">Whole state ({place.state})</option>}
            {isAdmin && <option value="central">National</option>}
            {isAdmin && <option value="detect">Detect from the text</option>}
          </select>
        </label>
        <label className="block">
          <span className="text-[13.5px] font-medium">Type</span>
          <select value={docType} onChange={(e) => setDocType(e.target.value)} className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[14px]">
            <option value="">Detect</option>
            {Object.entries(meta.doc_types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="block sm:col-span-2">
          <span className="text-[13.5px] font-medium">Source citation</span>
          <input value={citation} onChange={(e) => setCitation(e.target.value)} placeholder="e.g. Vidhan Sabha unstarred question no. 1123, 5 Aug 2026"
                 className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[14px]" />
        </label>
        <fieldset className="sm:col-span-2">
          <legend className="text-[13.5px] font-medium">Topics <span className="font-normal text-ink-3">(leave empty to detect)</span></legend>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {meta.topics.map((t) => (
              <button key={t} type="button" aria-pressed={topics.includes(t)} onClick={() => setTopics(topics.includes(t) ? topics.filter((x) => x !== t) : [...topics, t])}
                      className={`px-2 py-1 rounded-md text-[13px] border ${topics.includes(t) ? "border-ink bg-ink text-white" : "border-line text-ink-2"}`}>{t}</button>
            ))}
          </div>
        </fieldset>
        <label className="block">
          <span className="text-[13.5px] font-medium">Date</span>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[14px]" />
        </label>
        <div className="flex items-end">
          <button onClick={upload} disabled={!files.length || busy} className="h-9 w-full rounded-md bg-violet text-white text-[14.5px] disabled:opacity-40">
            {busy ? "Indexing…" : `Index ${files.length || ""} ${files.length === 1 ? "file" : "files"}`}
          </button>
        </div>
      </div>
      {results && (
        <ul className="mt-3 divide-y divide-line border-y border-line text-[13.5px]" aria-live="polite">
          {results.map((r, i) => (
            <li key={i} className="py-2">
              <span className="font-medium">{r.title ?? r.file}</span>{" "}
              {r.status === "indexed" && <span className="text-gotv">indexed · {r.chunks} passages from {r.pages} page{r.pages === 1 ? "" : "s"}</span>}
              {r.status === "duplicate" && <span className="text-ink-3">already in the knowledge base</span>}
              {r.status === "error" && <span className="text-amber">{r.error}</span>}
              {r.status === "indexed" && (
                <span className="block text-[12.5px] text-ink-3">
                  {DOC_TYPE_LABEL[r.doc_type ?? ""] ?? r.doc_type} · {r.constituency ?? (r.state ? `${r.state}-wide` : "National")} · {r.topics?.join(", ") || "no topic detected"}
                  {r.published_on ? ` · ${r.published_on}` : ""}{r.pages_without_text ? ` · ${r.pages_without_text} page(s) had no text` : ""}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SearchTester({ meta, place }: { meta: CpMeta; place: CpConstituency }) {
  const [q, setQ] = useState("");
  const [topic, setTopic] = useState("");
  const [res, setRes] = useState<CpQueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(query = q) {
    if (!query.trim()) return;
    setBusy(true); setError("");
    try { setRes(await api.cpQuery({ query, constituency_id: place.id, topic: topic || undefined, k: 6 })); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const max = Math.max(0.01, ...(res?.results.map((r) => r.score) ?? [0]));
  const jump = (id: string) => document.getElementById(`hit-${id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });

  return (
    <section className="bg-panel border border-line rounded-lg p-4" aria-labelledby="q-h">
      <h3 id="q-h" className="text-[17px] font-semibold">Ask the knowledge base</h3>
      <p className="text-[13px] text-ink-3">Test what the Copilot will find. Search: {meta.search}.</p>
      <form className="flex gap-2 mt-3" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Question" placeholder="e.g. How much cane payment is pending?"
               className="flex-1 min-w-0 h-10 px-3 rounded-md border border-line bg-panel text-[14.5px]" />
        <select aria-label="Topic filter" value={topic} onChange={(e) => setTopic(e.target.value)} className="h-10 px-2 rounded-md border border-line bg-panel text-[14px]">
          <option value="">Any topic</option>
          {meta.topics.map((t) => <option key={t}>{t}</option>)}
        </select>
        <button disabled={busy || !q.trim()} className="h-10 px-4 rounded-md bg-ink text-white text-[14.5px] disabled:opacity-40">{busy ? "…" : "Ask"}</button>
      </form>
      {!res && (
        <div className="flex flex-wrap gap-1 mt-2">
          {["Which works are pending or delayed?", "What do young people say about jobs?", "Budget for roads and drainage"].map((x) => (
            <button key={x} onClick={() => { setQ(x); run(x); }} className="px-2 py-0.5 rounded-full border border-line text-[12.5px] text-ink-2">{x}</button>
          ))}
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-[14px] text-amber">{error}</p>}
      {res && (
        <div className="mt-3" aria-live="polite">
          {res.answer && (
            <div className="rounded-md bg-violet-soft px-3 py-2.5">
              <p className="text-[15px] leading-relaxed"><CitedText text={res.answer.text} onCite={jump} /></p>
              <p className="text-[12px] text-ink-3 mt-1">{res.answer.generator === "offline" ? "Quoted from the passages below" : "Answer drafted from the passages below"}
                {res.translated ? ` · searched as “${res.translated}”` : ""}</p>
            </div>
          )}
          <ol className="mt-2 divide-y divide-line">
            {res.results.map((r) => (
              <li key={r.chunk_id} id={`hit-${r.id}`} className="py-2">
                <div className="flex items-center gap-2">
                  <span className="text-[12px] font-semibold text-violet w-6">{r.id}</span>
                  <span className="text-[14px] font-medium flex-1 truncate">{r.title}</span>
                  <span className="w-16 h-1.5 rounded-full bg-paper overflow-hidden" title={`relevance ${r.score}`}>
                    <span className="block h-full bg-violet" style={{ width: `${(r.score / max) * 100}%` }} />
                  </span>
                </div>
                <p className="text-[13px] text-ink-2 mt-0.5 pl-8">“{r.snippet}”</p>
                <p className="text-[12px] text-ink-3 pl-8">{r.citation}</p>
              </li>
            ))}
            {!res.results.length && <li className="py-3 text-[14px] text-ink-3">No passages matched. Try other words, or add documents on this topic.</li>}
          </ol>
        </div>
      )}
    </section>
  );
}

function DocRow({ d, meta, open, onToggle, onSaved, onRemove }: {
  d: CpDocument; meta: CpMeta; open: boolean; onToggle: () => void; onSaved: () => void; onRemove: () => void;
}) {
  const [edit, setEdit] = useState(false);
  const [form, setForm] = useState({ title: d.title, doc_type: d.doc_type, topics: d.topics, source_citation: d.source_citation, published_on: d.published_on ?? "" });
  const [chunks, setChunks] = useState<{ chunk_id: string; page: number | null; text: string }[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => { if (open && !chunks) api.cpDocument(d.doc_id).then((x) => setChunks(x.chunks)).catch((e) => setError(e.message)); }, [open, chunks, d.doc_id]);

  async function save() {
    try { await api.cpUpdateDocument(d.doc_id, form); setEdit(false); onSaved(); } catch (e) { setError((e as Error).message); }
  }

  return (
    <>
      <tr className={`border-b border-line align-top ${open ? "bg-paper" : ""}`}>
        <td className="px-4 py-2">
          <button onClick={onToggle} aria-expanded={open} className="text-left">
            <span className="block font-medium hover:text-violet">{d.title}</span>
            <span className="block text-[12.5px] text-ink-3">{d.source_citation}</span>
          </button>
        </td>
        <td className="px-2 py-2 whitespace-nowrap">{DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type}</td>
        <td className="px-2 py-2">{d.topics.join(", ") || <span className="text-ink-3">–</span>}</td>
        <td className="px-2 py-2 whitespace-nowrap">{d.constituency ?? (d.state ? `${d.state}-wide` : "National")}</td>
        <td className="px-2 py-2 whitespace-nowrap">{d.published_on ?? <span className="text-ink-3">–</span>}</td>
        <td className="px-2 py-2 text-right">{d.chunks}</td>
        <td className="px-4 py-2 whitespace-nowrap text-right">
          {d.editable && (
            <>
              <button onClick={() => { setEdit(!edit); if (!open) onToggle(); }} className="text-[13.5px] text-violet underline underline-offset-2">Edit</button>
              <button onClick={onRemove} className="ml-3 text-[13.5px] text-ink-3 underline underline-offset-2">Remove</button>
            </>
          )}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-line bg-paper">
          <td colSpan={7} className="px-4 pb-4">
            {error && <p role="alert" className="text-[13.5px] text-amber">{error}</p>}
            {edit && (
              <div className="grid sm:grid-cols-4 gap-2 py-2">
                <input aria-label="Title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="sm:col-span-4 h-9 px-2 rounded-md border border-line bg-panel text-[14px]" />
                <select aria-label="Type" value={form.doc_type} onChange={(e) => setForm({ ...form, doc_type: e.target.value })} className="h-9 px-2 rounded-md border border-line bg-panel text-[14px]">
                  {Object.entries(meta.doc_types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <input aria-label="Citation" value={form.source_citation} onChange={(e) => setForm({ ...form, source_citation: e.target.value })} className="sm:col-span-2 h-9 px-2 rounded-md border border-line bg-panel text-[14px]" />
                <input aria-label="Date" type="date" value={form.published_on} onChange={(e) => setForm({ ...form, published_on: e.target.value })} className="h-9 px-2 rounded-md border border-line bg-panel text-[14px]" />
                <div className="sm:col-span-3 flex flex-wrap gap-1.5">
                  {meta.topics.map((t) => (
                    <button key={t} type="button" aria-pressed={form.topics.includes(t)}
                            onClick={() => setForm({ ...form, topics: form.topics.includes(t) ? form.topics.filter((x) => x !== t) : [...form.topics, t] })}
                            className={`px-2 py-1 rounded-md text-[13px] border ${form.topics.includes(t) ? "border-ink bg-ink text-white" : "border-line text-ink-2 bg-panel"}`}>{t}</button>
                  ))}
                </div>
                <button onClick={save} className="h-9 rounded-md bg-violet text-white text-[14px]">Save</button>
              </div>
            )}
            <p className="text-[12.5px] text-ink-3 pt-1">{d.file_name} · {d.pages} page{d.pages === 1 ? "" : "s"} · added {new Date(d.uploaded_at).toLocaleDateString()}{d.uploader ? ` by ${d.uploader}` : ""}</p>
            <ol className="mt-2 space-y-1.5">
              {(chunks ?? []).map((c, i) => (
                <li key={c.chunk_id} className="text-[13px] text-ink-2"><span className="text-ink-3">{c.page ? `p. ${c.page}` : `#${i + 1}`} · </span>{c.text}</li>
              ))}
            </ol>
          </td>
        </tr>
      )}
    </>
  );
}
