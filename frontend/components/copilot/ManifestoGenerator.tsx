"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CpConstituency, CpManifesto, CpMeta } from "@/lib/types";
import { CitedText, ExportBar, manifestoText, Notes, Refs, SourcesList, useCiteFocus } from "./shared";

export default function ManifestoGenerator({ meta, place }: { meta: CpMeta; place: CpConstituency }) {
  const [scope, setScope] = useState<"constituency" | "cluster">("constituency");
  const [booths, setBooths] = useState<{ booth_id: string; name?: string }[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [topics, setTopics] = useState<string[]>(meta.topics);
  const [party, setParty] = useState("");
  const [language, setLanguage] = useState("en");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [doc, setDoc] = useState<CpManifesto | null>(null);
  const { active, cite } = useCiteFocus();

  useEffect(() => {
    api.booths(place.state)
      .then((bs) => setBooths((bs as { booth_id: string; constituency: string; name?: string }[]).filter((b) => b.constituency === place.name)))
      .catch(() => setBooths([]));
  }, [place]);

  async function generate() {
    setBusy(true); setError("");
    try {
      setDoc(await api.cpManifesto({ constituency_id: place.id, language, party_name: party, topics,
                                     booth_ids: scope === "cluster" ? picked : undefined }));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="grid lg:grid-cols-[340px_minmax(0,1fr)] gap-6 items-start">
      <form className="no-print bg-panel border border-line rounded-lg p-4 lg:sticky lg:top-4 space-y-4"
            onSubmit={(e) => { e.preventDefault(); generate(); }}>
        <fieldset>
          <legend className="text-[14px] font-medium">Area</legend>
          {([["constituency", `All of ${place.name}`], ["cluster", "A cluster of booths"]] as const).map(([k, l]) => (
            <label key={k} className="flex items-center gap-2 mt-1.5 text-[14.5px]">
              <input type="radio" checked={scope === k} onChange={() => setScope(k)} /> {l}
            </label>
          ))}
          {scope === "cluster" && (
            <div className="mt-2 max-h-44 overflow-y-auto rounded-md border border-line p-2">
              {booths.map((b) => (
                <label key={b.booth_id} className="flex items-center gap-2 py-0.5 text-[13.5px]">
                  <input type="checkbox" checked={picked.includes(b.booth_id)}
                         onChange={(e) => setPicked(e.target.checked ? [...picked, b.booth_id] : picked.filter((x) => x !== b.booth_id))} />
                  <span className="whitespace-nowrap">{b.booth_id}</span>
                  {b.name ? <span className="text-ink-3 truncate min-w-0">{b.name}</span> : null}
                </label>
              ))}
              {!booths.length && <p className="text-[13px] text-ink-3">No booths found.</p>}
            </div>
          )}
        </fieldset>
        <fieldset>
          <legend className="text-[14px] font-medium">Topics</legend>
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {meta.topics.map((t) => (
              <button key={t} type="button" aria-pressed={topics.includes(t)}
                      onClick={() => setTopics(topics.includes(t) ? topics.filter((x) => x !== t) : [...topics, t])}
                      className={`px-2.5 py-1.5 rounded-md text-[13.5px] border ${topics.includes(t) ? "border-ink bg-ink text-white" : "border-line text-ink-2"}`}>
                {t}
              </button>
            ))}
          </div>
        </fieldset>
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="text-[14px] font-medium">Your party</span>
            <input value={party} onChange={(e) => setParty(e.target.value)} maxLength={60} placeholder="our party"
                   className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]" />
          </label>
          <label className="block">
            <span className="text-[14px] font-medium">Language</span>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}
                    className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[15px]">
              {Object.entries(meta.languages).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        </div>
        <button disabled={busy || !topics.length || (scope === "cluster" && !picked.length)}
                className="w-full h-11 rounded-md bg-violet text-white font-medium disabled:opacity-50">
          {busy ? "Drafting…" : "Draft one-page manifesto"}
        </button>
        <p className="text-[12.5px] text-ink-3">Blends your party’s state and national promises with commitments that answer the local evidence. Each commitment says how it will be delivered.</p>
        {error && <p role="alert" className="text-[14px] text-amber">{error}</p>}
      </form>

      <div aria-live="polite">
        {!doc && (
          <div className="bg-panel border border-dashed border-line rounded-lg p-10 text-center text-ink-3">
            {busy ? `Reading ${place.name}’s documents…` : "Pick the area and topics, then draft."}
          </div>
        )}
        {doc && (
          <article className={`print-area bg-panel border border-line rounded-lg ${busy ? "opacity-60" : ""}`} lang={doc.generated_language}>
            <div className="no-print flex flex-wrap items-center justify-between gap-3 px-6 pt-5">
              <Notes warnings={doc.warnings} generator={doc.generator} live={doc.live} />
              <ExportBar id={doc.output_id} english={doc.generated_language === "en"} text={manifestoText(doc)} />
            </div>
            {/* the page itself: a narrow, printable sheet */}
            <div className="max-w-[720px] mx-auto px-6 py-8">
              <p className="text-[13px] text-ink-3">{doc.constituency.name} · {doc.party_name ?? "Local manifesto"}</p>
              <h2 className="wide text-[30px] font-semibold leading-tight mt-1">{doc.title}</h2>
              {doc.preamble.text && <p className="text-[16px] leading-relaxed mt-3 text-ink-2"><CitedText text={doc.preamble.text} onCite={cite} /></p>}
              <div className="mt-6 space-y-6">
                {doc.sections.map((s, i) => (
                  <section key={i} className="grid sm:grid-cols-[150px_minmax(0,1fr)] gap-x-5 gap-y-1">
                    <h3 className="text-[17px] font-semibold text-violet">{s.topic}</h3>
                    <div>
                      <p className="text-[14px] text-ink-2"><span className="text-ink-3">What we found: </span>
                        <CitedText text={s.local_context} onCite={cite} /><Refs ids={s.context_sources} inline={s.local_context} onCite={cite} /></p>
                      <ul className="mt-2 space-y-2">
                        {s.commitments.map((c, j) => (
                          <li key={j} className="flex gap-2.5">
                            <span aria-hidden className={`mt-2 w-1.5 h-1.5 rounded-full shrink-0 ${c.type === "party" ? "bg-ink" : "bg-gotv"}`} />
                            <span className="text-[15.5px] leading-snug">
                              <CitedText text={c.text} onCite={cite} /><Refs ids={c.sources} inline={c.text} onCite={cite} />
                              {c.delivery && <span className="block text-[13px] text-ink-3">How: {c.delivery}</span>}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </section>
                ))}
              </div>
              {doc.closing && <p className="mt-6 text-[15px] font-medium">{doc.closing}</p>}
              <p className="mt-4 text-[12px] text-ink-3"><span className="inline-block w-1.5 h-1.5 rounded-full bg-ink mr-1" />Party promise
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-gotv ml-3 mr-1" />Local commitment</p>
            </div>
            <div className="px-6 pb-6 pt-5 border-t border-line"><SourcesList sources={doc.sources} active={active} /></div>
          </article>
        )}
      </div>
    </div>
  );
}
