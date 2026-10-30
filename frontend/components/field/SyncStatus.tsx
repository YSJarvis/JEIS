"use client";

import { useEffect, useState } from "react";
import type { Connection } from "./useFieldRoute";

function ago(ts: number | null): string {
  if (!ts) return "";
  const m = Math.round((Date.now() - ts) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** One-line connection + queue indicator for the cadre app. */
export default function SyncStatus({ connection, pending, syncing, lastSynced, onSync }: {
  connection: Connection; pending: number; syncing: boolean; lastSynced: number | null; onSync: () => void;
}) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 30_000); return () => clearInterval(t); }, []);

  const updates = `${pending} ${pending === 1 ? "update" : "updates"}`;
  let dot = "bg-gotv", text = "Online", sub = lastSynced ? `All changes saved · synced ${ago(lastSynced)}` : "All changes saved";
  if (connection === "offline") {
    dot = pending ? "bg-amber" : "bg-ink-3";
    text = "Offline";
    sub = pending ? `${updates} saved on this phone` : "Showing your saved route";
  } else if (syncing && pending) {
    dot = "bg-violet animate-pulse"; text = "Syncing"; sub = `Sending ${updates}…`;
  } else if (pending) {
    dot = "bg-amber"; text = `${updates} pending`; sub = "Waiting to send";
  }

  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2.5 min-h-9">
      <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${dot}`} aria-hidden />
      <span className="leading-tight">
        <span className="block text-[14.5px] font-semibold">{text}</span>
        <span className="block text-[12.5px] text-ink-3">{sub}</span>
      </span>
      {pending > 0 && connection === "online" && !syncing && (
        <button onClick={onSync} className="ml-auto h-8 px-3 rounded-md border border-line text-[13.5px]">Sync now</button>
      )}
    </div>
  );
}
