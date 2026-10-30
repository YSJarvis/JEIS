"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { WrCheck, WrCluster, WrClusterDetail, WrCounter, WrMedium, WrMeta, WrResponseRow } from "@/lib/types";
import { CitedText, Refs, SourcesList, useCiteFocus } from "@/components/copilot/shared";
import {
  CopyButton, NegativityBar, SOURCE_SHORT, STATE_NAME, SeverityTag, ThreatScore, ago, copyText, fullText,
} from "./shared";

const MEDIUMS: [WrMedium, string, string][] = [
  ["tv_brief", "TV spokesperson brief", "Talking points, bridges and likely questions"],
  ["whatsapp", "WhatsApp broadcast", "Short, forwardable, under 700 characters"],
  ["press_release", "Press release", "Headline, three paragraphs and a quote"],
  ["digital_ad", "Digital ad text", "Headline 40 · body 125 · CTA 20 characters"],
];
const FACTORS: [keyof WrCluster["breakdown"], string][] = [
  ["volume", "Volume"], ["velocity", "Acceleration"], ["negativity", "Negativity"], ["reach", "Reach"],
  ["spread", "Spread"], ["state_significance", "State weight"],
];
const CHECK_TONE: Record<WrCheck["status"], { mark: string; cls: string; label: string }> = {
  pass: { mark: "✓", cls: "text-gotv", label: "Pass" },
  fixed: { mark: "✓", cls: "text-amber", label: "Fixed" },
  info: { mark: "i", cls: "text-ink-3", label: "Note" },
  action: { mark: "!", cls: "text-violet", label: "Action" },
  fail: { mark: "✕", cls: "text-amber", label: "Fail" },
};

export default function Workbench({ cluster, meta, isAdmin, version, onChange, preset }: {
  cluster: WrCluster; meta: WrMeta; isAdmin: boolean; version: number; onChange: () => void;
  preset?: { medium?: string; go: boolean };
}) {
  const [detail, setDetail] = useState<WrClusterDetail | null>(null);
  const [history, setHistory] = useState<WrResponseRow[]>([]);
  const [medium, setMedium] = useState<WrMedium>(
    preset?.medium && preset.medium in meta.mediums ? (preset.medium as WrMedium) : "tv_brief");
  const [language, setLanguage] = useState("en");
  const [party, setParty] = useState("");
  const [spokes, setSpokes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [doc, setDoc] = useState<WrCounter | null>(null);
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(cluster.label);
  const { active, cite } = useCiteFocus();

  useEffect(() => {
    api.wrThreat(cluster.cluster_id).then(setDetail).catch((e) => setError(e.message));
    api.wrResponses(cluster.cluster_id).then(setHistory).catch(() => setHistory([]));
  }, [cluster.cluster_id, version, cluster.item_count]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("ve.warroom.form") ?? "{}");
      if (saved.party) setParty(saved.party);
      if (saved.spokes) setSpokes(saved.spokes);
    } catch { /* storage unavailable */ }
  }, []);

  // deep link ("Counter viral threat"): generate once, even if the preset arrives after mount
  const fired = useRef(false);
  useEffect(() => {
    if (!preset?.go || fired.current) return;
    fired.current = true;
    if (preset.medium && preset.medium in meta.mediums) setMedium(preset.medium as WrMedium);
    generate(preset.medium as WrMedium | undefined);
  }, [preset]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function generate(override?: WrMedium) {
    setBusy(true); setError("");
    try { localStorage.setItem("ve.warroom.form", JSON.stringify({ party, spokes })); } catch { /* ignore */ }
    try {
      const r = await api.wrCounter({ narrative_cluster_id: cluster.cluster_id, target_medium: override ?? medium, language,
                                      party_name: party, spokesperson: spokes });
      setDoc(r);
      setHistory(await api.wrResponses(cluster.cluster_id));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function open(id: string) {
    try { const r = await api.wrResponse(id); setDoc(r); setMedium(r.medium); }
    catch (e) { setError((e as Error).message); }
  }

  async function review(status: "approved" | "rejected" | "draft") {
    if (!doc) return;
    try {
      const r = await api.wrReview(doc.response_id, status);
      setDoc(r); setHistory(await api.wrResponses(cluster.cluster_id)); onChange();
    } catch (e) { setError((e as Error).message); }
  }

  async function saveLabel(next: string | null) {
    try {
      await api.wrUpdateThreat(cluster.cluster_id, next === null ? { label: "" } : { label: next });
      setEditing(false); onChange();
    } catch (e) { setError((e as Error).message); }
  }

  async function setStatus(status: string) {
    try { await api.wrUpdateThreat(cluster.cluster_id, { status }); onChange(); }
    catch (e) { setError((e as Error).message); }
  }

  const b = cluster.breakdown;
  return (
    <section aria-label={`Workbench: ${cluster.label}`} className="min-w-0">
      {/* narrative header */}
      <div className="no-print bg-panel border border-line rounded-lg px-4 py-3 flex flex-wrap items-center gap-4">
        <ThreatScore score={cluster.threat_score} severity={cluster.severity} size="lg" />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); saveLabel(label.trim()); }}>
              <label className="sr-only" htmlFor="nlabel">Narrative name</label>
              <input id="nlabel" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} autoFocus
                     className="h-9 flex-1 px-2 rounded-md border border-line text-[17px]" />
              <button className="h-9 px-3 rounded-md bg-ink text-white text-[14px]">Save</button>
              <button type="button" onClick={() => saveLabel(null)} className="h-9 px-2 text-[13px] text-ink-3 underline">Use automatic name</button>
            </form>
          ) : (
            <h2 className="wide text-[22px] font-semibold leading-tight">
              {cluster.label}
              {isAdmin && <button onClick={() => { setLabel(cluster.label); setEditing(true); }}
                                  className="ml-2 align-middle text-[13px] font-normal text-ink-3 underline underline-offset-2" style={{ fontStretch: "100%" }}>Rename</button>}
            </h2>
          )}
          <p className="text-[13.5px] text-ink-3 mt-0.5">
            <SeverityTag severity={cluster.severity} /> · {cluster.state ? STATE_NAME[cluster.state] : "National"}
            {cluster.constituencies.length ? ` · ${cluster.constituencies.join(", ")}` : ""} · first seen {ago(cluster.first_seen)}
            {cluster.label_source === "manual" ? " · named by the war room" : ""}
          </p>
        </div>
        <dl className="grid grid-cols-3 sm:grid-cols-6 gap-x-3 gap-y-1" aria-label="What drives the threat score">
          {FACTORS.map(([k, l]) => {
            const v = b[k] ?? 0;
            const pct = k === "velocity" ? Math.round(Math.min(1, Math.max(0, (v + 1) / 2)) * 100) : Math.round(Math.min(1, v) * 100);
            return (
              <div key={k} className="w-[64px]">
                <dt className="text-[11.5px] text-ink-3 whitespace-nowrap">{l}</dt>
                <dd className="mt-1 h-1.5 rounded-full bg-line overflow-hidden" title={`${l}: ${v}`}>
                  <span className="block h-full rounded-full bg-ink-2" style={{ width: `${pct}%` }} />
                </dd>
              </div>
            );
          })}
        </dl>
        {isAdmin && (
          <label className="text-[13px]">
            <span className="sr-only">Narrative status</span>
            <select value={cluster.status} onChange={(e) => setStatus(e.target.value)}
                    className="h-8 px-2 rounded-md border border-line bg-panel text-[13.5px]">
              {meta.statuses.map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
            </select>
          </label>
        )}
      </div>

      {error && <p role="alert" className="mt-3 px-3 py-2 rounded-md bg-violet-soft text-[14px]">{error}</p>}

      <div className="grid xl:grid-cols-2 gap-4 mt-4 items-start">
        {/* LEFT: what the opposition is saying */}
        <section aria-labelledby="opp-h" className="no-print bg-panel border border-line rounded-lg">
          <div className="px-4 pt-3 pb-2 border-b border-line">
            <h3 id="opp-h" className="text-[13px] font-semibold uppercase tracking-wide text-ink-3">Opposition narrative</h3>
            {cluster.claim && (
              <blockquote className="mt-2 text-[17px] leading-snug font-medium border-l-[3px] border-amber pl-3">“{cluster.claim}”</blockquote>
            )}
            <p className="text-[13px] text-ink-3 mt-2">
              {cluster.items_72h} items in 72 h across {cluster.channels.length} channels · {cluster.negative_items} negative ·
              treat these as claims, not evidence
            </p>
          </div>
          <ol className="divide-y divide-line max-h-[620px] overflow-y-auto">
            {(detail?.items ?? []).map((it) => (
              <li key={it.item_id} className="px-4 py-2.5">
                <div className="flex items-baseline justify-between gap-2 text-[12.5px] text-ink-3">
                  <span className="truncate"><span className="font-medium text-ink-2">{SOURCE_SHORT[it.source_type] ?? it.source_type}</span> · {it.channel}</span>
                  <span className="whitespace-nowrap">{ago(it.published_at)}</span>
                </div>
                <p className="text-[14.5px] leading-snug mt-1" lang={it.language}>{it.text}</p>
                <div className="mt-1 flex items-center gap-3 text-[12px] text-ink-3">
                  <NegativityBar value={it.negativity} />
                  {it.reach ? <span>reach {Math.round(it.reach).toLocaleString("en-IN")}</span> : null}
                  {it.constituency && <span>{it.constituency}</span>}
                  {it.url && <a href={it.url} target="_blank" rel="noreferrer" className="underline">open</a>}
                </div>
              </li>
            ))}
            {!detail && <li className="px-4 py-6 text-ink-3 text-[14px]">Loading items…</li>}
          </ol>
        </section>

        {/* RIGHT: counter-brief */}
        <section aria-labelledby="ctr-h" className="bg-panel border border-line rounded-lg">
          <form className="no-print px-4 pt-3 pb-4 border-b border-line" onSubmit={(e) => { e.preventDefault(); generate(); }}>
            <h3 id="ctr-h" className="text-[13px] font-semibold uppercase tracking-wide text-ink-3">Counter-brief</h3>
            <fieldset className="mt-2 grid grid-cols-2 gap-1.5">
              <legend className="sr-only">Target medium</legend>
              {MEDIUMS.map(([k, l, hint]) => (
                <label key={k} className={`rounded-md border px-2.5 py-2 cursor-pointer ${medium === k ? "border-ink bg-paper" : "border-line"}`}>
                  <input type="radio" name="medium" value={k} checked={medium === k} onChange={() => setMedium(k)} className="sr-only" />
                  <span className="block text-[14.5px] font-medium">{l}</span>
                  <span className="block text-[12px] text-ink-3 leading-tight">{hint}</span>
                </label>
              ))}
            </fieldset>
            <div className="grid grid-cols-3 gap-2 mt-2">
              <label className="block">
                <span className="text-[13px] font-medium">Party</span>
                <input value={party} onChange={(e) => setParty(e.target.value)} maxLength={60} placeholder="our party"
                       className="mt-1 h-9 w-full px-2 rounded-md border border-line text-[14.5px]" />
              </label>
              <label className="block">
                <span className="text-[13px] font-medium">Spokesperson</span>
                <input value={spokes} onChange={(e) => setSpokes(e.target.value)} maxLength={80} placeholder="name, role"
                       className="mt-1 h-9 w-full px-2 rounded-md border border-line text-[14.5px]" />
              </label>
              <label className="block">
                <span className="text-[13px] font-medium">Language</span>
                <select value={language} onChange={(e) => setLanguage(e.target.value)}
                        className="mt-1 h-9 w-full px-2 rounded-md border border-line bg-panel text-[14.5px]">
                  {Object.entries(meta.languages).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </label>
            </div>
            <button disabled={busy} className="mt-3 w-full h-11 rounded-md bg-violet text-white font-medium disabled:opacity-50">
              {busy ? "Checking the record…" : doc ? "Generate again" : "Generate counter-brief"}
            </button>
            {history.length > 0 && (
              <details className="mt-2 text-[13px]">
                <summary className="cursor-pointer text-ink-3">{history.length} earlier draft{history.length === 1 ? "" : "s"}</summary>
                <ul className="mt-1 space-y-0.5">
                  {history.map((h) => (
                    <li key={h.response_id}>
                      <button type="button" onClick={() => open(h.response_id)} className="text-left hover:underline">
                        {h.medium_label} · {h.status} · {ago(h.created_at)}{h.author ? ` · ${h.author}` : ""}
                      </button>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </form>

          <div aria-live="polite">
            {!doc && !busy && (
              <p className="px-4 py-8 text-center text-[14px] text-ink-3">
                Pick a medium and generate. The draft uses only facts from the JanVani knowledge base, with a source for each.
              </p>
            )}
            {busy && !doc && <p className="px-4 py-8 text-center text-[14px] text-ink-3">Reading the knowledge base…</p>}
            {doc && <CounterDoc doc={doc} isAdmin={isAdmin} onReview={review} cite={cite} active={active} dim={busy} />}
          </div>
        </section>
      </div>
    </section>
  );
}

function SectionHead({ n, title, copy }: { n: number; title: string; copy?: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <h4 className="text-[15px] font-semibold"><span className="narrow text-violet mr-1.5">{n}</span>{title}</h4>
      {copy !== undefined && <CopyButton text={copy} />}
    </div>
  );
}

function CounterDoc({ doc, isAdmin, onReview, cite, active, dim }: {
  doc: WrCounter; isAdmin: boolean; onReview: (s: "approved" | "rejected" | "draft") => void;
  cite: (id: string) => void; active: string | null; dim: boolean;
}) {
  const c = doc.copy;
  const english = doc.generated_language === "en";
  const strip = (t: string) => t.replace(/\s*\[S\d+\]/g, "");
  const blocked = doc.safety_check.status === "blocked";
  return (
    <article className={`print-area px-4 py-4 space-y-5 ${dim ? "opacity-60" : ""}`} lang={doc.generated_language}>
      <div className="no-print flex flex-wrap items-center justify-between gap-2">
        <span className={`text-[12.5px] px-2 py-0.5 rounded ${doc.status === "approved" ? "bg-gotv-soft text-gotv" : doc.status === "rejected" ? "bg-line text-ink-3" : "bg-violet-soft text-violet"}`}>
          {doc.status === "approved" ? "Approved for release" : doc.status === "rejected" ? "Rejected" : "Draft: needs approval"}
        </span>
        <div className="flex flex-wrap gap-1.5">
          <a href={api.wrExportUrl(doc.response_id, "pdf")} download aria-disabled={!english}
             onClick={(e) => { if (!english) e.preventDefault(); }}
             title={english ? "Download PDF" : "PDF export supports English. Use Markdown or Print."}
             className={`h-8 px-2.5 grid place-items-center rounded-md text-[13px] ${english ? "bg-ink text-white" : "bg-line text-ink-3 cursor-not-allowed"}`}>PDF</a>
          <a href={api.wrExportUrl(doc.response_id, "md")} download className="h-8 px-2.5 grid place-items-center rounded-md border border-line text-[13px]">Markdown</a>
          <button onClick={() => window.print()} className="h-8 px-2.5 rounded-md border border-line text-[13px]">Print</button>
          <CopyButton text={fullText(doc)} label="Copy all" />
        </div>
      </div>
      <p className="hidden print:block text-[18px] font-semibold">{doc.medium_label}: {doc.label}</p>

      {doc.warnings.length > 0 && (
        <div className="no-print rounded-md bg-paper px-3 py-2 text-[13px]">
          <p className="text-ink-2">{doc.live ? `Drafted by ${doc.generator.split(":")[1] ?? doc.generator}, then checked line by line.` : "Built directly from the sources (no AI provider)."}</p>
          {doc.warnings.map((w, i) => <p key={i} className="text-amber">{w}</p>)}
        </div>
      )}

      <section>
        <SectionHead n={1} title="Threat summary and core claim" />
        <p className="text-[14.5px] mt-1.5 text-ink-2">{doc.threat_summary}</p>
        <p className="text-[14.5px] mt-1.5"><span className="text-ink-3">Core claim: </span>{doc.core_claim}</p>
        <p className="mt-2 text-[14.5px] rounded-md border border-line px-3 py-2">
          <span className="font-semibold">{doc.assessment.verdict_label}.</span>{" "}
          <CitedText text={doc.assessment.explanation} onCite={cite} /><Refs ids={doc.assessment.sources} inline={doc.assessment.explanation} onCite={cite} />
        </p>
      </section>

      <section>
        <SectionHead n={2} title="Verified counter-facts" copy={doc.counter_facts.map((f) => `• ${strip(f.fact)}`).join("\n")} />
        {doc.counter_facts.length ? (
          <ul className="mt-1.5 space-y-1.5">
            {doc.counter_facts.map((f, i) => (
              <li key={i} className="flex gap-2 text-[14.5px] leading-snug">
                <span aria-hidden className="mt-2 w-1.5 h-1.5 rounded-full bg-gotv shrink-0" />
                <span><CitedText text={f.fact} onCite={cite} /><Refs ids={f.sources} inline={f.fact} onCite={cite} /></span>
              </li>
            ))}
          </ul>
        ) : <p className="mt-1.5 text-[14px] text-amber">No verified facts found. Use the holding line below and add sources in JanVani Copilot.</p>}
      </section>

      <section>
        <SectionHead n={3} title={doc.medium_label} copy={copyText(doc)} />
        <div className="mt-2">
          {doc.medium === "tv_brief" && (
            <div className="space-y-3 text-[14.5px]">
              {c.headline_message && <p className="text-[16px] font-medium">{c.headline_message}</p>}
              {!!c.talking_points?.length && <ol className="list-decimal pl-5 space-y-1">{c.talking_points.map((x, i) =>
                <li key={i}><CitedText text={x} onCite={cite} /></li>)}</ol>}
              {!!c.bridging_lines?.length && (
                <div><p className="text-[12.5px] text-ink-3 uppercase tracking-wide">Bridging lines</p>
                  <ul className="mt-1 space-y-1">{c.bridging_lines.map((x, i) => <li key={i}>“{x}”</li>)}</ul></div>
              )}
              {!!c.likely_questions?.length && (
                <div><p className="text-[12.5px] text-ink-3 uppercase tracking-wide">Likely questions</p>
                  <dl className="mt-1 space-y-2">{c.likely_questions.map((x, i) => (
                    <div key={i}><dt className="font-medium">Q. {x.q}</dt><dd className="text-ink-2">A. <CitedText text={x.a} onCite={cite} /></dd></div>
                  ))}</dl></div>
              )}
            </div>
          )}
          {doc.medium === "whatsapp" && (
            <div className="rounded-lg bg-[#E7EFE9] p-3">
              <p className="max-w-[420px] rounded-lg rounded-tl-none bg-white px-3 py-2 text-[14.5px] whitespace-pre-wrap shadow-sm">
                {(c.text ?? "").split(/(\*[^*\n]+\*)/g).map((p, i) => p.startsWith("*") && p.endsWith("*") ? <b key={i}>{p.slice(1, -1)}</b> : p)}
              </p>
              <p className="mt-1.5 text-[12px] text-ink-3">{c.characters ?? c.text?.length ?? 0} / 700 characters</p>
            </div>
          )}
          {doc.medium === "press_release" && (
            <div className="space-y-2 text-[14.5px]">
              <p className="text-[12.5px] text-ink-3 uppercase tracking-wide">For immediate release</p>
              <p className="text-[18px] font-semibold leading-snug">{c.headline}</p>
              {(c.body ?? []).map((x, i) => <p key={i}><CitedText text={x} onCite={cite} /></p>)}
              {c.quote?.text && <blockquote className="border-l-2 border-ink pl-3">“{c.quote.text}” <span className="text-ink-3">— {c.quote.attribution}</span></blockquote>}
            </div>
          )}
          {doc.medium === "digital_ad" && (
            <div className="grid sm:grid-cols-2 gap-2">
              {(c.variants ?? []).map((v, i) => (
                <div key={i} className="rounded-md border border-line p-3 flex flex-col">
                  <p className="text-[11.5px] text-ink-3">Variant {i + 1} · <span className="narrow">{v.headline.length}/40 · {v.body.length}/125 · {v.cta.length}/20</span></p>
                  <p className="text-[16px] font-semibold leading-snug mt-1">{v.headline}</p>
                  <p className="text-[14px] text-ink-2 mt-1 flex-1">{v.body}</p>
                  <div className="mt-2 flex items-center justify-between">
                    <span className="h-7 px-2.5 grid place-items-center rounded bg-ink text-white text-[12.5px]">{v.cta}</span>
                    <CopyButton text={`${v.headline}\n${v.body}\n${v.cta}`} />
                  </div>
                </div>
              ))}
              {!c.variants?.length && <p className="text-[14px] text-ink-3">No ad copy: there are no verified facts to advertise.</p>}
            </div>
          )}
        </div>
        {doc.avoid.length > 0 && (
          <p className="mt-3 text-[13px] text-ink-3"><span className="font-medium text-ink-2">Don’t say: </span>{doc.avoid.join(" · ")}</p>
        )}
      </section>

      <section>
        <SectionHead n={4} title="ECI / MCC safety check" />
        <p className={`mt-1.5 text-[14px] font-medium ${blocked ? "text-amber" : "text-gotv"}`}>
          {blocked ? "Blocked: not ready for release" : doc.safety_check.status === "clear_with_fixes" ? "Clear after automatic fixes" : "Clear"}
        </p>
        <ul className="mt-1.5 divide-y divide-line border-y border-line">
          {doc.safety_check.checks.map((ch, i) => {
            const t = CHECK_TONE[ch.status];
            return (
              <li key={i} className="py-1.5 flex gap-2.5 text-[13.5px]">
                <span aria-hidden className={`w-4 text-center font-semibold ${t.cls}`}>{t.mark}</span>
                <span className="flex-1"><span className="font-medium">{ch.rule}</span> <span className="sr-only">({t.label})</span>
                  <span className="block text-ink-3">{ch.detail}</span></span>
                <span className={`text-[12px] ${t.cls}`}>{t.label}</span>
              </li>
            );
          })}
        </ul>
        {isAdmin ? (
          <div className="no-print mt-3 flex flex-wrap gap-2">
            {doc.status !== "approved" && (
              <button onClick={() => onReview("approved")} disabled={blocked}
                      className="h-9 px-3.5 rounded-md bg-gotv text-white text-[14px] font-medium disabled:opacity-40"
                      title={blocked ? "A draft with no verified facts can't be approved" : undefined}>Approve for release</button>
            )}
            {doc.status !== "rejected" && <button onClick={() => onReview("rejected")} className="h-9 px-3 rounded-md border border-line text-[14px]">Reject</button>}
            {doc.status !== "draft" && <button onClick={() => onReview("draft")} className="h-9 px-3 text-[13px] text-ink-3 underline">Back to draft</button>}
          </div>
        ) : (
          <p className="no-print mt-2 text-[13px] text-ink-3">A strategist approves drafts before release.</p>
        )}
      </section>

      <div className="pt-3 border-t border-line"><SourcesList sources={doc.sources} active={active} /></div>
      <p className="text-[12px] text-ink-3">{doc.medium_label} · generated {ago(doc.generated_at)}{doc.party_name ? ` · ${doc.party_name}` : ""}</p>
    </article>
  );
}
