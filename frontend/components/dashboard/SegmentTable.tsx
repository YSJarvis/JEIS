"use client";

import { useState } from "react";
import { fmt } from "@/lib/quadrants";
import type { Segment } from "@/lib/types";

export default function SegmentTable({ community, schemes }: { community: Segment[]; schemes: Segment[] }) {
  const [tab, setTab] = useState<"community" | "schemes">("community");
  const rows = tab === "community" ? community : schemes;
  const maxP = Math.max(1, ...rows.map((r) => r.persuade + r.gotv));
  return (
    <div>
      <div className="flex gap-1 px-4 pt-2 pb-3" role="tablist">
        {(["community", "schemes"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                  className={`px-2.5 py-1 rounded-md text-[14px] ${tab === t ? "bg-ink text-white" : "text-ink-2 hover:bg-paper"}`}>
            {t === "community" ? "By community" : "By scheme status"}
          </button>
        ))}
      </div>
      <table className="w-full text-[14px]">
        <thead>
          <tr className="text-left text-ink-3 text-[13px] border-y border-line">
            <th className="font-normal px-4 py-2">Segment</th>
            <th className="font-normal px-2 py-2 text-right">Voters</th>
            <th className="font-normal px-2 py-2 text-right">Swing</th>
            <th className="font-normal px-2 py-2 text-right">Turnout</th>
            <th className="font-normal px-4 py-2">Actionable</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.segment} className="border-b border-line">
              <td className="px-4 py-2">{r.segment}</td>
              <td className="px-2 py-2 text-right">{fmt(r.voters)}</td>
              <td className="px-2 py-2 text-right">{r.avg_swing}</td>
              <td className="px-2 py-2 text-right">{r.avg_turnout}</td>
              <td className="px-4 py-2">
                <div className="flex h-2 rounded-sm overflow-hidden bg-paper" title={`${r.persuade} to persuade, ${r.gotv} to turn out`}>
                  <div className="bg-violet" style={{ width: `${(r.persuade / maxP) * 100}%` }} />
                  <div className="bg-gotv" style={{ width: `${(r.gotv / maxP) * 100}%` }} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="px-4 py-3 text-[12.5px] text-ink-3">Violet: persuadable voters. Teal: supporters to turn out. Segment figures are aggregates for planning; every score is computed per voter.</p>
    </div>
  );
}
