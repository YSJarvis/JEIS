"use client";

import { fmt } from "@/lib/quadrants";
import type { BoothRow } from "@/lib/types";

export default function BoothTable({ booths, selected, onSelect }: {
  booths: BoothRow[]; selected: string; onSelect: (id: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[14.5px]">
        <thead>
          <tr className="text-left text-ink-3 text-[13px] border-y border-line">
            <th className="font-normal px-4 py-2 w-10">Rank</th>
            <th className="font-normal px-2 py-2">Booth</th>
            <th className="font-normal px-2 py-2 text-right">Voters</th>
            <th className="font-normal px-2 py-2 text-right">Last turnout</th>
            <th className="font-normal px-2 py-2 text-right">Last margin</th>
            <th className="font-normal px-2 py-2 text-right">Persuade</th>
            <th className="font-normal px-2 py-2 text-right">Turn out</th>
            <th className="font-normal px-4 py-2 w-40">Priority</th>
          </tr>
        </thead>
        <tbody>
          {booths.map((b, i) => {
            const on = b.booth_id === selected;
            const tight = (b.last_margin_pct ?? 99) < 5;
            return (
              <tr key={b.booth_id} onClick={() => onSelect(on ? "" : b.booth_id)}
                  className={`border-b border-line cursor-pointer ${on ? "bg-violet-soft" : "hover:bg-paper"}`}>
                <td className="px-4 py-2 narrow text-[17px] font-semibold text-ink-3">{i + 1}</td>
                <td className="px-2 py-2">
                  <button className="text-left" aria-pressed={on} onClick={(e) => { e.stopPropagation(); onSelect(on ? "" : b.booth_id); }}>
                    <span className="block font-medium">{b.name}</span>
                    <span className="block text-[12.5px] text-ink-3">{b.booth_id} · {b.constituency}</span>
                  </button>
                </td>
                <td className="px-2 py-2 text-right">{fmt(b.total_voters)}</td>
                <td className="px-2 py-2 text-right">{b.historical_turnout}%</td>
                <td className={`px-2 py-2 text-right ${tight ? "text-violet font-semibold" : ""}`}>
                  {b.last_margin_pct != null ? `${b.last_margin_pct}%` : "–"}
                </td>
                <td className="px-2 py-2 text-right">{fmt(b.persuade)}</td>
                <td className="px-2 py-2 text-right">{fmt(b.gotv)}</td>
                <td className="px-4 py-2">
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-1.5 rounded-full bg-paper overflow-hidden">
                      <div className="h-full bg-violet" style={{ width: `${b.priority_score}%` }} />
                    </div>
                    <span className="narrow font-semibold w-8 text-right">{Math.round(b.priority_score)}</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
