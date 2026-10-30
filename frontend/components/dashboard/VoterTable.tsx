"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmt, Q, Q_ORDER } from "@/lib/quadrants";
import type { Quadrant, VoterRow } from "@/lib/types";
import { CONSENT_LABEL } from "@/components/admin/PrivacyPanel";

const PAGE = 25;

export default function VoterTable({ state, booth, masked }: { state: string; booth: string; masked: boolean }) {
  const [quadrant, setQuadrant] = useState<Quadrant | "">("PERSUADE");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ total: number; items: VoterRow[] }>({ total: 0, items: [] });

  useEffect(() => {
    const t = setTimeout(() => {
      api.voters({ state, booth_id: booth || undefined, quadrant: quadrant || undefined, q: q || undefined, limit: PAGE, offset: page * PAGE })
        .then(setData).catch(() => setData({ total: 0, items: [] }));
    }, 200);
    return () => clearTimeout(t);
  }, [state, booth, quadrant, q, page]);

  const pages = Math.max(1, Math.ceil(data.total / PAGE));
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3">
        {[["", "Everyone"] as const, ...Q_ORDER.map((k) => [k, Q[k].action] as const)].map(([k, label]) => (
          <button key={k || "all"} onClick={() => { setQuadrant(k as Quadrant | ""); setPage(0); }} aria-pressed={quadrant === k}
                  className={`px-2.5 py-1 rounded-full text-[14px] border ${quadrant === k ? "border-ink bg-ink text-white" : "border-line text-ink-2 hover:border-ink-3"}`}>
            {label}
          </button>
        ))}
        <input value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} placeholder={masked ? "Search voter ID" : "Search name or voter ID"}
               aria-label="Search voters" className="ml-auto h-9 px-3 rounded-md border border-line bg-panel text-[14.5px] w-60" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[14px]">
          <thead>
            <tr className="text-left text-ink-3 text-[13px] border-y border-line">
              {["Voter", "Age", "Booth", "Community", "Schemes", "Consent", "Swing", "Turnout", "Segment", "Priority"].map((h, i) => (
                <th key={h} className={`font-normal py-2 ${i ? "px-2" : "px-4"} ${["Age", "Swing", "Turnout", "Priority"].includes(h) ? "text-right" : ""}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.items.map((v) => (
              <tr key={v.voter_id} className="border-b border-line">
                <td className="px-4 py-2">
                  <span className="block font-medium">{v.name}</span>
                  <span className="block text-[12px] text-ink-3">{[v.voter_id, v.phone, v.is_migrant ? "absentee" : ""].filter(Boolean).join(" · ")}</span>
                </td>
                <td className="px-2 py-2 text-right">{v.age}{v.gender}</td>
                <td className="px-2 py-2 text-ink-2">{v.booth_id}</td>
                <td className="px-2 py-2 text-ink-2">{v.community_group}</td>
                <td className="px-2 py-2 text-ink-2 max-w-48 truncate" title={v.scheme_types}>{v.scheme_types.replaceAll(",", ", ") || "–"}</td>
                <td className="px-2 py-2 text-ink-2 whitespace-nowrap">{CONSENT_LABEL[v.consent_status]}</td>
                <td className="px-2 py-2 text-right">{v.swing_score}</td>
                <td className="px-2 py-2 text-right">{v.turnout_score}</td>
                <td className="px-2 py-2">
                  <span className="inline-flex items-center gap-1.5 text-[13px]">
                    <span className="w-2 h-2 rounded-full" style={{ background: Q[v.quadrant].color }} />{Q[v.quadrant].action}
                  </span>
                </td>
                <td className="px-2 py-2 text-right narrow text-[16px] font-semibold">{v.priority}</td>
              </tr>
            ))}
            {!data.items.length && <tr><td colSpan={10} className="px-4 py-8 text-center text-ink-3">No voters match these filters. Clear the search or pick another segment.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between px-4 py-3 text-[14px] text-ink-2">
        <span>{fmt(data.total)} voters</span>
        <div className="flex items-center gap-2">
          <button disabled={page === 0} onClick={() => setPage(page - 1)} className="px-2.5 py-1 rounded-md border border-line disabled:opacity-40">Previous</button>
          <span>Page {page + 1} of {pages}</span>
          <button disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} className="px-2.5 py-1 rounded-md border border-line disabled:opacity-40">Next</button>
        </div>
      </div>
    </div>
  );
}
