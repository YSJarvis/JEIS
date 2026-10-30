"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BoothRow, CadreRow } from "@/lib/types";

/** Constituency managers (and strategists) assign booths to field cadres. */
export default function CadrePanel({ state, booths, canEdit }: { state?: string; booths: BoothRow[]; canEdit: boolean }) {
  const [cadres, setCadres] = useState<CadreRow[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => { api.cadres(state).then(setCadres).catch((e) => setError(e.message)); }, [state]);

  async function save(c: CadreRow) {
    setSaving(true); setError("");
    try {
      await api.assignCadre(c.cadre_id, draft);
      setCadres((all) => all.map((x) => (x.cadre_id === c.cadre_id ? { ...x, booth_ids: draft } : x)));
      setEditing(null);
    } catch (e) { setError((e as Error).message); }
    finally { setSaving(false); }
  }

  const boothName = (id: string) => booths.find((b) => b.booth_id === id)?.name ?? id;

  return (
    <div>
      {error && <p role="alert" className="mx-4 mb-2 text-[14px] text-amber">{error}</p>}
      <table className="w-full text-[14.5px]">
        <thead>
          <tr className="text-left text-ink-3 text-[13px] border-y border-line">
            <th className="font-normal px-4 py-2">Cadre</th>
            <th className="font-normal px-2 py-2">Assigned booths</th>
            <th className="font-normal px-2 py-2 text-right">Today</th>
            <th className="font-normal px-4 py-2 w-24" />
          </tr>
        </thead>
        <tbody>
          {cadres.map((c) => {
            const open = editing === c.cadre_id;
            return (
              <tr key={c.cadre_id} className="border-b border-line align-top">
                <td className="px-4 py-2">
                  <span className="block font-medium">{c.name}</span>
                  <span className="block text-[12.5px] text-ink-3">{c.username ?? "No login"}</span>
                </td>
                <td className="px-2 py-2">
                  {open ? (
                    <fieldset>
                      <legend className="sr-only">Booths for {c.name}</legend>
                      {booths.map((b) => (
                        <label key={b.booth_id} className="flex items-center gap-2 py-0.5 text-[14px]">
                          <input type="checkbox" checked={draft.includes(b.booth_id)}
                                 onChange={(e) => setDraft(e.target.checked ? [...draft, b.booth_id] : draft.filter((x) => x !== b.booth_id))} />
                          {b.booth_id} <span className="text-ink-3 truncate">{b.name}</span>
                        </label>
                      ))}
                    </fieldset>
                  ) : (
                    <span className="text-ink-2">{c.booth_ids.map((b) => <span key={b} className="block" title={boothName(b)}>{b}</span>)}</span>
                  )}
                </td>
                <td className="px-2 py-2 text-right whitespace-nowrap">
                  {c.route_id ? `${c.done} of ${c.planned} doors` : <span className="text-ink-3">No route yet</span>}
                </td>
                <td className="px-4 py-2 text-right whitespace-nowrap">
                  {canEdit && (open ? (
                    <span className="flex flex-col items-end gap-1">
                      <button onClick={() => save(c)} disabled={saving || !draft.length}
                              className="h-8 px-3 rounded-md bg-violet text-white text-[13.5px] disabled:opacity-40">
                        {saving ? "Saving…" : "Save"}
                      </button>
                      <button onClick={() => setEditing(null)} className="text-[13px] text-ink-3">Cancel</button>
                    </span>
                  ) : (
                    <button onClick={() => { setEditing(c.cadre_id); setDraft(c.booth_ids); }}
                            className="text-[14px] text-violet underline underline-offset-2">Change</button>
                  ))}
                </td>
              </tr>
            );
          })}
          {!cadres.length && <tr><td colSpan={4} className="px-4 py-6 text-center text-ink-3">No cadres in this area yet.</td></tr>}
        </tbody>
      </table>
      <p className="px-4 py-3 text-[12.5px] text-ink-3">A new assignment applies from the cadre’s next route. Routes already built today stay as they are.</p>
    </div>
  );
}
