"use client";

import { useState } from "react";
import { Q, STATUS } from "@/lib/quadrants";
import type { ConsentStatus, Member, Stop, StopStatus } from "@/lib/types";
import { useTalkingPoints } from "./useTalkingPoints";

export interface PointsContext { cadreId?: string | null; date?: string; online: boolean; aiLive: boolean; lang: string; setLang: (l: string) => void }
const LANGS: [string, string][] = [["en", "English"], ["hi", "हिन्दी"]];

const ACTIONS: { status: StopStatus; label: string; cls: string }[] = [
  { status: "VISITED", label: "Visited", cls: "bg-gotv text-white" },
  { status: "NOT_HOME", label: "Not home", cls: "border border-line" },
  { status: "REVISIT", label: "Come back", cls: "border border-line" },
  { status: "REFUSED", label: "Refused", cls: "border border-line" },
];

const DRIVER_LABEL: Record<string, string> = {
  neutrality: "no fixed party lean", scheme: "scheme beneficiary", age: "age group", community: "contested community",
  dera: "dera influence", agrarian: "farm household", terrain: "hill terrain", family: "family influence",
  history: "voting history", booth: "booth turnout", local: "distance to booth", migrant_absentee: "absentee",
};

const CONSENT: { status: ConsentStatus; label: string }[] = [
  { status: "GRANTED", label: "Agreed" },
  { status: "PENDING", label: "Not asked" },
  { status: "OPT_OUT", label: "Opted out" },
];

export default function StopCard({ stop, open, unsynced, onToggle, onUpdate, onConsent, points: pctx }: {
  stop: Stop; open: boolean; unsynced: boolean; onToggle: () => void;
  onUpdate: (s: StopStatus, notes: string) => void; onConsent: (m: Member, s: ConsentStatus) => void;
  points: PointsContext;
}) {
  const tp = useTalkingPoints({ ...pctx, stop, open });
  const [notes, setNotes] = useState(stop.notes ?? "");
  const done = stop.status !== "PENDING";
  const persuade = stop.members.filter((m) => m.quadrant === "PERSUADE").length;
  const gotv = stop.members.filter((m) => m.quadrant === "GOTV").length;
  const head = stop.members[0];

  return (
    <li className={`bg-panel border rounded-lg ${open ? "border-ink-3" : "border-line"} ${done && !open ? "opacity-70" : ""}`}>
      <button onClick={onToggle} aria-expanded={open} className="w-full flex items-start gap-3 p-3 text-left">
        <span className={`narrow shrink-0 w-10 h-10 rounded-md grid place-items-center text-[22px] font-semibold ${
          done ? "bg-paper text-ink-3" : "bg-ink text-white"}`}>{stop.seq}</span>
        <span className="flex-1 min-w-0">
          <span className="block font-semibold text-[16px] truncate">{head ? `${head.name.split(" ").slice(-1)[0]} family` : "Household"}</span>
          <span className="block text-[13.5px] text-ink-2 truncate">{stop.address}</span>
          <span className="block text-[13.5px] text-ink-2">
            {stop.members.length} {stop.members.length === 1 ? "voter" : "voters"}
            {persuade ? <> · <span style={{ color: Q.PERSUADE.color }}>{persuade} to persuade</span></> : null}
            {gotv ? <> · <span style={{ color: Q.GOTV.color }}>{gotv} to turn out</span></> : null}
          </span>
          <span className="block text-[12.5px] text-ink-3">{Math.round(stop.leg_km * 1000)} m from last stop</span>
        </span>
        <span className="shrink-0 text-right">
          <span className={`block text-[13px] ${STATUS[stop.status].tone}`}>{STATUS[stop.status].label}</span>
          {unsynced && <span className="block text-[12px] text-amber">Saved on phone</span>}
        </span>
      </button>

      {open && (
        <div className="px-3 pb-3">
          <section className="rounded-md bg-violet-soft p-3" aria-busy={tp.loading}>
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-[14px] font-semibold text-violet">What to talk about</h3>
              {pctx.aiLive && (
                <div className="flex gap-1" role="group" aria-label="Language">
                  {LANGS.map(([code, label]) => (
                    <button key={code} onClick={() => pctx.setLang(code)} aria-pressed={pctx.lang === code}
                            className={`h-7 px-2 rounded text-[12.5px] ${pctx.lang === code ? "bg-violet text-white" : "text-violet"}`}>{label}</button>
                  ))}
                </div>
              )}
            </div>
            <p className="text-[12.5px] text-ink-3 mt-0.5">
              {tp.loading ? "Tailoring for this household…" : tp.tailored ? "Tailored for this household" : "Standard points for this household"}
              {tp.tailored && tp.canRefresh && !tp.loading && (
                <> · <button onClick={tp.refresh} className="underline underline-offset-2">Try again</button></>
              )}
            </p>
            <ul className={`mt-1.5 space-y-2 ${tp.loading ? "opacity-60" : ""}`}>
              {tp.points.map((t, i) => (
                <li key={i} className="text-[15px] leading-snug">
                  {t.point}
                  <span className="block text-[12.5px] text-ink-3">{t.topic} · {t.reason}</span>
                </li>
              ))}
            </ul>
            {tp.sources.length > 0 && (
              <p className="text-[12px] text-ink-3 mt-2">Based on: {tp.sources.map((s) => s.kind === "directive" ? "today’s focus" : s.title).join(" · ")}</p>
            )}
            {tp.error ? <p className="text-[12px] text-amber mt-1">{tp.error}</p>
              : tp.note && <p className="text-[12px] text-ink-3 mt-1">{tp.note}</p>}
            {pctx.lang !== "en" && !tp.tailored && !tp.loading && (
              <p className="text-[12px] text-ink-3 mt-1">Hindi points need a connection to the AI service; showing English.</p>
            )}
          </section>

          <ul className="mt-3 divide-y divide-line border-y border-line">
            {stop.members.map((m) => <MemberRow key={m.voter_id} m={m} onConsent={(st) => onConsent(m, st)} />)}
            {!stop.members.length && <li className="py-3 text-[14px] text-ink-3">No one here is on today’s contact list.</li>}
          </ul>

          <div className="flex gap-2 mt-3">
            <a href={`https://www.google.com/maps/dir/?api=1&destination=${stop.lat},${stop.lng}&travelmode=walking`}
               target="_blank" rel="noreferrer" className="h-9 px-3 rounded-md border border-line grid place-items-center text-[14px]">
              Directions
            </a>
            <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Note (optional)"
                   aria-label="Visit note" className="flex-1 min-w-0 h-9 px-3 rounded-md border border-line text-[14.5px]" />
          </div>
          <div className="grid grid-cols-4 gap-2 mt-2">
            {ACTIONS.map((a) => (
              <button key={a.status} onClick={() => onUpdate(a.status, notes)} aria-pressed={stop.status === a.status}
                      className={`h-11 rounded-md text-[14px] font-medium ${a.cls} ${stop.status === a.status ? "ring-2 ring-ink ring-offset-1" : ""}`}>
                {a.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </li>
  );
}

function MemberRow({ m, onConsent }: { m: Member; onConsent: (s: ConsentStatus) => void }) {
  const [more, setMore] = useState(false);
  const drivers = Object.entries(m.drivers?.swing ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 2)
    .map(([k]) => DRIVER_LABEL[k] ?? k);
  const absentee = (m.attributes?.absentee_type as string | undefined) ?? (m.is_migrant ? "Absentee" : "");
  return (
    <li className="py-2.5">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: Q[m.quadrant].color }} />
        <span className="font-medium text-[15px]">{m.name}</span>
        <span className="text-[13px] text-ink-3">{m.age}{m.gender}</span>
        {m.consent_status !== "GRANTED" && <span className="text-[12px] text-ink-3 border border-line rounded px-1">No consent yet</span>}
        <span className="ml-auto text-[13px]" style={{ color: Q[m.quadrant].color }}>{Q[m.quadrant].action}</span>
      </div>
      <div className="grid grid-cols-2 gap-x-4 mt-1.5 pl-4">
        <Bar label="Swing" value={m.swing_score} color={Q.PERSUADE.color} />
        <Bar label="Turnout" value={m.turnout_score} color={Q.GOTV.color} />
      </div>
      <button onClick={() => setMore(!more)} className="pl-4 mt-1 text-[13px] text-ink-3 underline underline-offset-2" aria-expanded={more}>
        {more ? "Less" : "Profile"}
      </button>
      {more && (
        <dl className="pl-4 mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[13.5px]">
          <dt className="text-ink-3">Community</dt><dd>{m.community_group}</dd>
          <dt className="text-ink-3">Schemes</dt><dd>{m.scheme_types ? m.scheme_types.replaceAll(",", ", ") : "None recorded"}</dd>
          {absentee && <><dt className="text-ink-3">Status</dt><dd>{absentee}</dd></>}
          <dt className="text-ink-3">Why flagged</dt><dd>{drivers.join(", ")}</dd>
          <dt className="text-ink-3">Phone</dt>
          <dd>{m.phone
            ? <a className="text-violet" href={`tel:${m.phone.replace(/\s/g, "")}`}>{m.phone}</a>
            : <span className="text-ink-3">Shown once they agree to be contacted</span>}</dd>
        </dl>
      )}
      {more && (
        <fieldset className="pl-4 mt-2">
          <legend className="text-[13px] text-ink-3">Consent to contact</legend>
          <div className="flex gap-1.5 mt-1">
            {CONSENT.map((c) => (
              <button key={c.status} aria-pressed={m.consent_status === c.status}
                      onClick={() => {
                        if (m.consent_status === c.status) return;
                        if (c.status === "OPT_OUT" && !confirm(`Record that ${m.name} opted out? They will be removed from all contact lists.`)) return;
                        onConsent(c.status);
                      }}
                      className={`h-8 px-2.5 rounded-md text-[13px] border ${m.consent_status === c.status
                        ? "border-ink bg-ink text-white" : "border-line text-ink-2"}`}>
                {c.label}
              </button>
            ))}
          </div>
        </fieldset>
      )}
    </li>
  );
}

function Bar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2 text-[12.5px] text-ink-3">
      <span className="w-12">{label}</span>
      <span className="flex-1 h-1.5 rounded-full bg-paper overflow-hidden">
        <span className="block h-full" style={{ width: `${value}%`, background: color }} />
      </span>
      <span className="narrow w-7 text-right text-ink font-semibold text-[14px]">{Math.round(value)}</span>
    </div>
  );
}
