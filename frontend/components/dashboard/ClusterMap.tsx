"use client";

import { useEffect, useRef, useState } from "react";
import { Q } from "@/lib/quadrants";
import type { BoothRow, Cluster } from "@/lib/types";

interface Props { clusters: Cluster[]; booths: BoothRow[] }

/** Leaflet map of priority households, cluster ranks and booths.
 *  Leaflet loads client-side only; if it (or the tile server) is unavailable,
 *  an offline schematic projection is drawn instead. */
export default function ClusterMap({ clusters, booths }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let map: import("leaflet").Map | undefined;
    let cancelled = false;
    (async () => {
      try {
        const L = (await import("leaflet")).default;
        if (cancelled || !el.current) return;
        map = L.map(el.current, { zoomControl: true, attributionControl: true });
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19, attribution: "© OpenStreetMap contributors",
        }).addTo(map);
        const bounds = L.latLngBounds([]);
        for (const c of clusters) {
          for (const p of c.points) {
            L.circleMarker([p.lat, p.lng], { radius: 3.5, weight: 0, fillOpacity: 0.75, fillColor: Q[p.q].color })
              .bindTooltip(`${p.household_id} · priority ${p.priority}`).addTo(map);
            bounds.extend([p.lat, p.lng]);
          }
          const icon = L.divIcon({
            className: "",
            html: `<div style="width:26px;height:26px;border-radius:50%;background:#1D1A2E;color:#fff;display:grid;place-items:center;font:600 13px var(--font-sans);border:2px solid #fff">${c.rank}</div>`,
            iconSize: [26, 26], iconAnchor: [13, 13],
          });
          L.marker([c.lat, c.lng], { icon })
            .bindPopup(`<b>Cluster ${c.rank}</b><br>${c.households} households · ${c.voters} voters<br>${c.persuade} to persuade · ${c.gotv} to turn out`)
            .addTo(map);
        }
        for (const b of booths) {
          L.rectangle([[b.lat - 0.0006, b.lng - 0.0006], [b.lat + 0.0006, b.lng + 0.0006]],
            { color: "#5B2E91", weight: 2, fillOpacity: 0.9, fillColor: "#fff" })
            .bindTooltip(`${b.booth_id} · ${b.name}`).addTo(map);
          bounds.extend([b.lat, b.lng]);
        }
        if (bounds.isValid()) map.fitBounds(bounds, { padding: [24, 24] });
        else map.setView([22.5, 79], 5);
      } catch {
        if (!cancelled) setOffline(true);
      }
    })();
    return () => { cancelled = true; map?.remove(); };
  }, [clusters, booths]);

  if (offline) return <Schematic clusters={clusters} booths={booths} />;
  return <div ref={el} className="w-full h-full min-h-[420px]" aria-label="Map of priority clusters" />;
}

function Schematic({ clusters, booths }: Props) {
  const pts = clusters.flatMap((c) => c.points);
  const all = [...pts, ...booths];
  if (!all.length) return <p className="p-6 text-ink-3">No priority households in this selection.</p>;
  const lats = all.map((p) => p.lat), lngs = all.map((p) => p.lng);
  const [a, b, c, d] = [Math.min(...lats), Math.max(...lats), Math.min(...lngs), Math.max(...lngs)];
  const W = 600, H = 420, pad = 24;
  const x = (lng: number) => pad + ((lng - c) / (d - c || 1)) * (W - 2 * pad);
  const y = (lat: number) => H - pad - ((lat - a) / (b - a || 1)) * (H - 2 * pad);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full bg-[#E7E9E4]" role="img" aria-label="Offline schematic of priority clusters">
      {pts.map((p, i) => <circle key={i} cx={x(p.lng)} cy={y(p.lat)} r={2.6} fill={Q[p.q].color} fillOpacity={0.75} />)}
      {booths.map((bt) => <rect key={bt.booth_id} x={x(bt.lng) - 5} y={y(bt.lat) - 5} width={10} height={10} fill="#fff" stroke="#5B2E91" strokeWidth={2} />)}
      {clusters.map((cl) => (
        <g key={cl.cluster_id}>
          <circle cx={x(cl.lng)} cy={y(cl.lat)} r={12} fill="#1D1A2E" stroke="#fff" strokeWidth={2} />
          <text x={x(cl.lng)} y={y(cl.lat) + 4.5} textAnchor="middle" fontSize="12" fontWeight="600" fill="#fff">{cl.rank}</text>
        </g>
      ))}
      <text x={W - 10} y={H - 8} textAnchor="end" fontSize="11" fill="#7C7889">Offline view · map tiles unavailable</text>
    </svg>
  );
}
