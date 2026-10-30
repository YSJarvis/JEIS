"use client";

import { useState } from "react";
import { fmt, Q, Q_ORDER } from "@/lib/quadrants";
import type { Meta, Quadrant } from "@/lib/types";

interface Props {
  points: { x: number; y: number; q: Quadrant }[];
  counts: Partial<Record<Quadrant, number>>;
  thresholds: Meta["thresholds"];
}

const W = 520, H = 400, PAD = { l: 44, r: 12, t: 12, b: 40 };
const sx = (v: number) => PAD.l + (v / 100) * (W - PAD.l - PAD.r);
const sy = (v: number) => H - PAD.b - (v / 100) * (H - PAD.t - PAD.b);

export default function QuadrantField({ points, counts, thresholds }: Props) {
  const [focus, setFocus] = useState<Quadrant | null>(null);
  const total = Q_ORDER.reduce((a, q) => a + (counts[q] ?? 0), 0) || 1;
  const { swing_high: xs, turnout_high: ys } = thresholds;

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img"
           aria-label={`Scatter of ${points.length} sampled voters by swing and turnout`}>
        {/* persuasion zone */}
        <rect x={sx(xs)} y={sy(100)} width={sx(100) - sx(xs)} height={sy(ys) - sy(100)} fill={Q.PERSUADE.soft} />
        <rect x={sx(0)} y={sy(ys)} width={sx(100) - sx(0)} height={sy(0) - sy(ys)} fill="#F6F6F8" />
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v} className="text-[11px]" fill="#7C7889">
            <line x1={sx(v)} x2={sx(v)} y1={sy(0)} y2={sy(0) + 4} stroke="#B9B6C4" />
            <text x={sx(v)} y={sy(0) + 16} textAnchor="middle">{v}</text>
            <line x1={sx(0) - 4} x2={sx(0)} y1={sy(v)} y2={sy(v)} stroke="#B9B6C4" />
            <text x={sx(0) - 8} y={sy(v) + 4} textAnchor="end">{v}</text>
          </g>
        ))}
        <line x1={sx(xs)} x2={sx(xs)} y1={sy(0)} y2={sy(100)} stroke="#5B2E91" strokeDasharray="3 4" strokeOpacity=".5" />
        <line x1={sx(0)} x2={sx(100)} y1={sy(ys)} y2={sy(ys)} stroke="#5B2E91" strokeDasharray="3 4" strokeOpacity=".5" />
        <text x={sx(xs) + 8} y={sy(100) + 18} fill="#5B2E91" fontSize="13" fontWeight="600">Persuasion zone</text>
        <text x={sx(0) + 8} y={sy(0) - 8} fill="#7C7889" fontSize="12">Below {ys}% turnout: supporters here need a push to vote</text>
        <g>
          {points.map((p, i) => {
            const dim = focus && focus !== p.q;
            return <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={p.q === "PERSUADE" || p.q === "GOTV" ? 2.6 : 2}
                           fill={Q[p.q].color} fillOpacity={dim ? 0.08 : p.q === "LOW" ? 0.55 : 0.8} />;
          })}
        </g>
        <text x={(sx(0) + sx(100)) / 2} y={H - 6} textAnchor="middle" fontSize="12.5" fill="#4A4659">Swing propensity</text>
        <text transform={`translate(12 ${(sy(0) + sy(100)) / 2}) rotate(-90)`} textAnchor="middle" fontSize="12.5" fill="#4A4659">Turnout probability</text>
      </svg>

      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2" aria-label="Quadrant totals">
        {Q_ORDER.map((q) => (
          <li key={q}>
            <button onMouseEnter={() => setFocus(q)} onMouseLeave={() => setFocus(null)}
                    onFocus={() => setFocus(q)} onBlur={() => setFocus(null)}
                    className="w-full flex items-center gap-2 py-1.5 text-left">
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: Q[q].color }} />
              <span className="text-[14px] text-ink-2 flex-1 truncate">{Q[q].name}</span>
              <span className="narrow text-[18px] font-semibold">{fmt(counts[q])}</span>
              <span className="text-[12px] text-ink-3 w-9 text-right">{Math.round(((counts[q] ?? 0) / total) * 100)}%</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
