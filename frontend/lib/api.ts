import type {
  CmdOverview, DemoStatus, DispatchResult,
  WrClusterDetail, WrCluster, WrCounter, WrIngestResult, WrMediaItem, WrMeta, WrResponseRow,
  CpBrief, CpDocument, CpIngestResult, CpManifesto, CpMeta, CpOutputRow, CpQueryResult, Directive, OpsSummary, RollOptions, RollPreview, RollReport, TalkingPointResult, CadreRow, Cluster, ConsentStatus, Dashboard, FieldRoute, IngestReport, Meta, PrivacyState, Route, ScoringConfig,
  ScoringJob, Session, StateWeights, SyncOp, SyncResult, VoterRow,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE || process.env.NEXT_PUBLIC_API_BASE_URL || "https://jeis.onrender.com";

/** Thrown when the request never reached the server (offline, DNS, timeout). */
export class NetworkError extends Error {}
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

function toLogin() {
  if (typeof window === "undefined" || window.location.pathname === "/login") return;
  const next = window.location.pathname + window.location.search;
  window.location.assign(`/login${next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`);
}

async function req<T>(path: string, init: RequestInit = {}, opts: { redirectOn401?: boolean; timeoutMs?: number } = {}): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 15000);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",                       // send the HTTP-only session cookie
      signal: ctrl.signal,
      headers: { "X-Requested-With": "VoterEngine", ...(init.headers ?? {}) },
    });
  } catch {
    throw new NetworkError("No connection");
  } finally {
    clearTimeout(timer);
  }
  const body = await res.json().catch(() => ({}));
  if (res.status === 401 && opts.redirectOn401 !== false) toLogin();
  if (!res.ok) throw new ApiError(body.error ?? `Request failed (${res.status})`, res.status);
  return body as T;
}

const qs = (p: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined && v !== "") as [string, string][]).toString();

const json = (method: string, data: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
});

export const api = {
  // auth
  login: (username: string, password: string) =>
    req<Session>("/api/auth/login", json("POST", { username, password }), { redirectOn401: false }),
  logout: () => req<{ ok: boolean }>("/api/auth/logout", { method: "POST" }, { redirectOn401: false }),
  me: () => req<Session>("/api/auth/me", {}, { redirectOn401: false }),

  // dashboard (admin, manager)
  meta: () => req<Meta>("/api/meta"),
  dashboard: (state?: string, booth?: string) => req<Dashboard>(`/api/dashboard?${qs({ state, booth_id: booth })}`),
  clusters: (state?: string, booth?: string) => req<Cluster[]>(`/api/clusters?${qs({ state, booth_id: booth })}`),
  voters: (p: { state?: string; booth_id?: string; quadrant?: string; consent?: string; q?: string; limit?: number; offset?: number }) =>
    req<{ total: number; items: VoterRow[]; pii_masked: boolean }>(`/api/voters?${qs(p)}`),
  setConsent: (voterId: string, consent_status: ConsentStatus) =>
    req(`/api/voters/${voterId}/consent`, json("PATCH", { consent_status })),
  cadres: (state?: string) => req<CadreRow[]>(`/api/cadres?${qs({ state })}`),
  assignCadre: (cadreId: string, booth_ids: string[]) => req<CadreRow>(`/api/cadres/${cadreId}`, json("PATCH", { booth_ids })),
  cadreRoute: (cadreId: string) => req<Route>(`/api/cadres/${cadreId}/route`),

  // field (cadre)
  myRoute: () => req<FieldRoute>("/api/cadre/route", {}, { timeoutMs: 10000 }),
  buildMyRoute: (max_stops: number, regenerate = false) =>
    req<FieldRoute>("/api/cadre/route", json("POST", { max_stops, regenerate })),
  sync: (ops: SyncOp[], client?: { remaining: number; app_version: string }) =>
    req<SyncResult>("/api/cadre/sync", json("POST", { ops, include_route: true, client }), { timeoutMs: 20000 }),

  // admin
  scoringConfig: () => req<ScoringConfig>("/api/scoring/config"),
  recalibrate: (body: { state?: string; reset?: boolean; thresholds?: Partial<Meta["thresholds"]> } & Partial<StateWeights>) =>
    req<{ job: ScoringJob; config: ScoringConfig }>("/api/scoring/recalibrate", json("POST", body)),
  scoringJob: (id: string) => req<ScoringJob>(`/api/scoring/jobs/${id}`),
  privacy: () => req<PrivacyState>("/api/privacy"),
  setMasking: (pii_masking: boolean) => req<PrivacyState>("/api/privacy", json("PATCH", { pii_masking })),
  runScoring: () => req<{ scored: number }>("/api/scoring/run", { method: "POST" }),
  ingestCsv: (file: File, mode: "append" | "replace", state?: string) => {
    const fd = new FormData();
    fd.append("file", file); fd.append("mode", mode);
    if (state) fd.append("state", state);
    return req<IngestReport>("/api/ingest/csv", { method: "POST", body: fd });
  },
  reseed: () => req<{ seeded: Record<string, number> }>("/api/admin/seed", json("POST", {})),

  // electoral roll import (admin)
  rollPreview: (file: File, options: RollOptions, mapping?: Record<string, string | null>) =>
    req<RollPreview>("/api/import/electoral-roll/preview", { method: "POST", body: rollForm(file, options, mapping) }, { timeoutMs: 120000 }),
  rollImport: (file: File, options: RollOptions, mapping: Record<string, string | null>) =>
    req<RollReport>("/api/import/electoral-roll", { method: "POST", body: rollForm(file, options, mapping) }, { timeoutMs: 300000 }),
  booths: (state: string) => req<{ booth_id: string; constituency: string }[]>(`/api/booths?${qs({ state })}`),

  // talking points
  llmStatus: () => req<{ live: boolean; provider: string }>("/api/llm/status"),
  talkingPoints: (household_id: string, language: string, refresh = false) =>
    req<TalkingPointResult>("/api/cadre/generate-talking-points", json("POST", { household_id, language, refresh }), { timeoutMs: 25000 }),

  // directives + operations (manager / admin)
  directives: () => req<Directive[]>("/api/directives"),
  addDirective: (text: string, state?: string) => req<Directive>("/api/directives", json("POST", { text, state })),
  removeDirective: (id: number) => req<Directive>(`/api/directives/${id}`, json("PATCH", { active: false })),
  opsSummary: (state?: string) => req<OpsSummary>(`/api/ops/summary?${qs({ state })}`),
  opsExportUrl: (format: "csv" | "pdf", state?: string) => `${BASE}/api/ops/summary.${format}?${qs({ state })}`,

  // JanVani Copilot
  cpMeta: () => req<CpMeta>("/api/copilot/meta"),
  cpIngest: (files: File[], meta: Record<string, unknown>) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("file", f));
    fd.append("meta", JSON.stringify(meta));
    return req<{ results: CpIngestResult[]; summary: Record<string, number> }>("/api/copilot/ingest", { method: "POST", body: fd }, { timeoutMs: 180000 });
  },
  cpDocuments: (p: { constituency_id?: string; doc_type?: string; topic?: string; q?: string }) =>
    req<CpDocument[]>(`/api/copilot/documents?${qs(p)}`),
  cpDocument: (id: string) => req<CpDocument & { chunks: { chunk_id: string; seq: number; page: number | null; text: string }[] }>(`/api/copilot/documents/${id}`),
  cpUpdateDocument: (id: string, body: Record<string, unknown>) => req<CpDocument>(`/api/copilot/documents/${id}`, json("PATCH", body)),
  cpDeleteDocument: (id: string) => req<{ deleted: string }>(`/api/copilot/documents/${id}`, { method: "DELETE" }),
  cpQuery: (body: { query: string; constituency_id?: string; state?: string; topic?: string; doc_type?: string; k?: number; language?: string }) =>
    req<CpQueryResult>("/api/copilot/query", json("POST", body), { timeoutMs: 45000 }),
  cpBrief: (body: Record<string, unknown>) => req<CpBrief>("/api/copilot/generate-speech-brief", json("POST", body), { timeoutMs: 90000 }),
  cpManifesto: (body: Record<string, unknown>) => req<CpManifesto>("/api/copilot/generate-manifesto", json("POST", body), { timeoutMs: 90000 }),
  cpOutputs: (kind?: string) => req<CpOutputRow[]>(`/api/copilot/outputs?${qs({ kind })}`),
  cpOutput: <T,>(id: string) => req<T>(`/api/copilot/outputs/${id}`),
  cpExportUrl: (id: string, format: "md" | "pdf") => `${BASE}/api/copilot/outputs/${id}.${format}`,

  // CounterPulse AI
  wrMeta: () => req<WrMeta>("/api/warroom/meta"),
  wrThreats: (p: { state?: string; severity?: string; status?: string; q?: string } = {}) =>
    req<{ generated_at: string; clusters: WrCluster[] }>(`/api/warroom/threats?${qs(p)}`),
  wrThreat: (id: string) => req<WrClusterDetail>(`/api/warroom/threats/${id}`),
  wrUpdateThreat: (id: string, body: { label?: string; status?: string }) => req<WrClusterDetail>(`/api/warroom/threats/${id}`, json("PATCH", body)),
  wrIngest: (items: Record<string, unknown>[]) =>
    req<{ results: WrIngestResult[]; summary: Record<string, number> }>("/api/warroom/ingest-media", json("POST", { items })),
  wrIngestFiles: (files: File[], meta: Record<string, unknown>) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("file", f));
    fd.append("meta", JSON.stringify(meta));
    return req<{ results: WrIngestResult[]; summary: Record<string, number> }>("/api/warroom/ingest-media", { method: "POST", body: fd }, { timeoutMs: 120000 });
  },
  wrMedia: (p: Record<string, string | number | undefined>) =>
    req<{ total: number; limit: number; offset: number; items: WrMediaItem[] }>(`/api/warroom/media?${qs(p)}`),
  wrMediaCsvUrl: (p: Record<string, string | number | undefined>) => `${BASE}/api/warroom/media?${qs({ ...p, format: "csv" })}`,
  wrCounter: (body: { narrative_cluster_id: string; target_medium: string; language?: string; party_name?: string; spokesperson?: string }) =>
    req<WrCounter>("/api/warroom/generate-counter", json("POST", body), { timeoutMs: 90000 }),
  wrResponses: (cluster_id?: string) => req<WrResponseRow[]>(`/api/warroom/responses?${qs({ cluster_id })}`),
  wrResponse: (id: string) => req<WrCounter>(`/api/warroom/responses/${id}`),
  wrReview: (id: string, status: "approved" | "rejected" | "draft") => req<WrCounter>(`/api/warroom/responses/${id}`, json("PATCH", { status })),
  wrExportUrl: (id: string, format: "md" | "pdf") => `${BASE}/api/warroom/responses/${id}.${format}`,
  // Command Center, executive briefing, pitch demo
  commandOverview: (state?: string) => req<CmdOverview>(`/api/command/overview?${qs({ state })}`),
  dispatchRoutes: (state?: string) => req<DispatchResult>("/api/command/dispatch-routes", json("POST", { state }), { timeoutMs: 60000 }),
  briefingUrl: (state?: string) => `${BASE}/api/reports/executive-briefing?${qs({ state })}`,
  demoStatus: () => req<DemoStatus>("/api/demo/status", {}, { redirectOn401: false }),
  demoStart: (rebuild = false) => req<DemoStatus>("/api/demo/pitch", json("POST", { enabled: true, rebuild }), { timeoutMs: 120000 }),
  demoExit: () => req<DemoStatus>("/api/demo/exit", { method: "POST" }, { redirectOn401: false }),

  wrSample: (body: { simulate?: number; reset?: boolean; remove?: boolean }) =>
    req<{ simulated?: number; removed?: boolean; seeded?: number }>("/api/warroom/sample-feed", json("POST", body)),
};

function rollForm(file: File, options: RollOptions, mapping?: Record<string, string | null>) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("options", JSON.stringify(options));
  if (mapping) fd.append("mapping", JSON.stringify(mapping));
  return fd;
}
