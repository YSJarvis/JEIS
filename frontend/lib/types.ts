export type StateCode = "UP" | "PB" | "UK" | "GA";
export type Quadrant = "PERSUADE" | "GOTV" | "OPPOSITION" | "LOW";
export type StopStatus = "PENDING" | "VISITED" | "NOT_HOME" | "REFUSED" | "REVISIT";

export interface StateMeta { code: StateCode; name: string; center: [number, number]; voters: number }
export interface Meta {
  states: StateMeta[];
  role: "ADMIN" | "MANAGER" | "CADRE"; constituency: string | null; pii_masking: boolean;
  quadrants: Record<Quadrant, { label: string; action: string }>;
  thresholds: { swing_high: number; turnout_high: number; loyal_affinity: number; opp_affinity: number };
}

export interface BoothRow {
  booth_id: string; name: string; constituency: string; total_voters: number;
  historical_turnout: number; last_margin_pct: number | null; priority_score: number;
  lat: number; lng: number; persuade: number; gotv: number;
}

export interface Segment { segment: string; voters: number; avg_swing: number; avg_turnout: number; persuade: number; gotv: number }

export interface Dashboard {
  totals: { voters: number; households: number; booths: number; avg_swing: number; avg_turnout: number; migrants: number; beneficiaries: number };
  quadrants: Partial<Record<Quadrant, number>>;
  scatter: { x: number; y: number; q: Quadrant }[];
  segments: Segment[];
  schemes: Segment[];
  booths: BoothRow[];
  field: { planned: number; done: number; visited: number };
  consent: Record<"GRANTED" | "PENDING" | "OPT_OUT", number>;
}

export interface Cluster {
  cluster_id: number; rank: number; lat: number; lng: number; households: number; voters: number;
  persuade: number; gotv: number; total_priority: number;
  points: { household_id: string; lat: number; lng: number; priority: number; q: Quadrant }[];
}

export interface VoterRow {
  voter_id: string; name: string; age: number; gender: string; booth_id: string; household_id: string;
  community_group: string; scheme_types: string; is_migrant: number; phone: string | null;
  consent_status: "GRANTED" | "PENDING" | "OPT_OUT"; address?: string;
  swing_score: number; turnout_score: number; quadrant: Quadrant; priority: number;
}

export interface Member extends Omit<VoterRow, "booth_id" | "household_id"> {
  scheme_beneficiary: number; state: StateCode; attributes: Record<string, unknown>;
  drivers: { swing: Record<string, number>; turnout: Record<string, number> };
}

export interface TalkingPoint { topic: string; point: string; reason: string }

export interface Stop {
  stop_id: number; seq: number; household_id: string; address: string; lat: number; lng: number;
  leg_km: number; household_priority: number; status: StopStatus; notes: string | null;
  members: Member[]; talking_points: TalkingPoint[];
  sources?: { id: string; title: string; kind: string }[];
}

export interface Route {
  route_id: number | null; cadre_id: string; cadre_name?: string; route_date: string; total_km?: number;
  stops: Stop[]; progress?: { done: number; total: number }; message?: string;
}

export interface Cadre { cadre_id: string; name: string; state: StateCode; phone: string; booth_ids: string }

export interface IngestReport {
  accepted: number; rejected: number; booths_created: number; households: number;
  errors: { row: number | null; voter_id?: string; error: string }[];
}

// ---------- v2: auth, privacy, sync, scoring config ----------
export type Role = "ADMIN" | "MANAGER" | "CADRE";
export type ConsentStatus = "GRANTED" | "PENDING" | "OPT_OUT";

export interface User {
  user_id: string; username: string; display_name: string; role: Role; role_label: string;
  state: StateCode | null; constituency: string | null; cadre_id: string | null;
}
export interface Scope { states: StateCode[]; booth_ids: string[] | null; constituency: string | null; cadre_id: string | null }
export interface Session { user: User; scope: Scope; today?: string; workspace?: "main" | "demo" }

export interface CadreRow extends Omit<Cadre, "booth_ids"> {
  booth_ids: string[]; username: string | null; route_id: number | null; total_km: number | null;
  planned: number; done: number;
}

export interface FieldRoute extends Route {
  cadre?: { cadre_id: string; name: string; booth_ids: string; state: StateCode };
  directives?: { id: number; text: string; author: string | null }[];
}

export type SyncOp =
  | { op_id: string; type: "stop_status"; stop_id: number; household_id: string; status: StopStatus; notes?: string; client_ts: string }
  | { op_id: string; type: "consent"; voter_id: string; status: ConsentStatus; client_ts: string };

export interface SyncResult {
  results: { op_id: string; result: "applied" | "stale" | "rejected"; reason?: string; duplicate?: boolean }[];
  summary: { applied: number; stale: number; rejected: number };
  server_time: string;
  route?: FieldRoute;
}

export interface PrivacyState {
  pii_masking: boolean;
  updated: { updated_by: string; updated_at: string } | null;
  consent: Record<ConsentStatus, number>;
  policy: Record<ConsentStatus, string>;
  recent_consent_changes: { voter_id: string; old_status: string | null; new_status: ConsentStatus; source: string; created_at: string; actor: string | null }[];
}

export interface StateWeights { swing_weights: Record<string, number>; turnout_weights: Record<string, number>; migrant_penalty: number }
export interface ScoringConfig {
  labels: { swing: Record<string, string>; turnout: Record<string, string> };
  states: Record<StateCode, { name: string; current: StateWeights; default: StateWeights; customised: boolean;
    updated: { updated_by: string; updated_at: string; version: number } | null }>;
  thresholds: { current: Meta["thresholds"]; default: Meta["thresholds"] };
  version: number;
}
export interface ScoringJob {
  job_id: string; status: "QUEUED" | "RUNNING" | "DONE" | "FAILED"; scope: string; error: string | null;
  result: { scored?: number; model_version?: string; before?: Record<string, Partial<Record<Quadrant, number>>>;
    after?: Record<string, Partial<Record<Quadrant, number>>> };
}

// ---------- v3: roll import, talking points, directives, operations ----------
export type RollTarget = "epic" | "name" | "relative_name" | "relation_type" | "age" | "gender" | "house_no" | "booth"
  | "serial_no" | "section" | "constituency" | "state";
export interface RollIssue { row: number | null; kind: "error" | "warning" | "duplicate"; message: string; epic: string | null; name: string | null }
export interface RollStats {
  rows: number; valid: number; invalid: number; duplicates_in_file: number; created: number; updated: number; unchanged: number;
  skipped_existing: number; possible_duplicates: number; linked_epic: number; nonstandard_epic: number;
  booths_created: number; households_created: number; needs_geocoding: number;
}
export interface RollReport {
  dry_run: boolean; stats: RollStats; issues: RollIssue[]; states: StateCode[];
  new_booths: { booth_id: string; name: string; constituency: string; geo_fallback: string }[];
  scoring?: ScoringJob | { status: "DEFERRED"; error: string };
}
export interface RollPreview {
  format: "csv" | "json"; rows: number; columns: string[]; sample: Record<string, string>[];
  targets: Record<RollTarget, { label: string; required: boolean }>;
  suggested: Record<RollTarget, string | null>; confidence: Partial<Record<RollTarget, "high" | "medium" | "low">>;
  mapping: Record<RollTarget, string | null>; report: RollReport | null; blocking?: string;
}
export interface RollOptions { state: string; constituency?: string; on_existing: "update" | "skip" }

export interface TalkingPointResult {
  household_id: string; date: string; language: string; generator: string; live: boolean; note: string | null; cached: boolean;
  points: (TalkingPoint & { sources: string[] })[];
  directives: { id: number; text: string; author: string | null }[];
  sources: { id: string; title: string; kind: string }[];
  generated_at: string;
}
export interface Directive {
  directive_id: number; state: StateCode | null; constituency: string | null; for_date: string; text: string;
  author?: string | null; created_at: string;
}

export interface OpsCounts {
  targeted: number; completed: number; visited: number; not_home: number; revisit: number; refused: number; remaining: number;
  stale_pending: number; households_visited: number; households_consented: number; persuadables_reached: number;
  supporters_reached: number; consent_rate: number | null;
}
export interface OpsSummary {
  date: string; scope: string; state: StateCode | null; generated_at: string; stale_minutes: number;
  totals: OpsCounts & { reported_unsynced: number; cadres_active: number; cadres_out_of_contact: number; cadres_total: number };
  consent: { households_visited: number; households_consented: number; rate: number | null; captured_today: number };
  cadres: (OpsCounts & { cadre_id: string; name: string; booth_ids: string[]; last_sync_at: string | null;
    minutes_since_sync: number | null; reported_queue: number; status: "On route" | "Done" | "Not started" | "Out of contact" | "No route" })[];
  booths: (OpsCounts & { booth_id: string; name: string; constituency: string; cadres: string[]; consents_captured_today: number })[];
  hourly: { hour: number; completed: number }[];
  directives: Directive[];
}

// ---------- v4: JanVani Copilot ----------
export interface CpConstituency { id: string; name: string; state: StateCode }
export interface CpMeta {
  constituencies: CpConstituency[]; topics: string[]; doc_types: Record<string, string>;
  event_types: Record<string, string>; audiences: Record<string, string>; languages: Record<string, string>;
  llm: { live: boolean; provider: string }; search: string;
  stats: { documents: number; chunks: number; embedded_chunks: number };
}
export interface CpSource {
  id: string; chunk_id: string; doc_id: string; title: string; doc_type: string; citation: string; page: number | null;
  published_on: string | null; url: string | null; snippet: string; quote: string; used: boolean;
}
export interface CpCited { text: string; sources: string[] }
export interface CpBrief {
  output_id: string; constituency: { id: string; name: string; state: StateCode }; event_type: string; event_label: string;
  audience: string; audience_label: string; language: string; generated_language: string; party_name: string | null;
  opponent: string | null; generator: string; live: boolean; warnings: string[]; generated_at: string;
  executive_summary: CpCited;
  grievances: { title: string; detail: string; stats: { figure: string; context: string; source: string }[]; sources: string[] }[];
  opponent_critique: { point: string; sources: string[] }[];
  commitments: { commitment: string; delivery: string; type: "party" | "local"; sources: string[] }[];
  event_section: { title: string; items: CpCited[] };
  sources: CpSource[];
}
export interface CpManifesto {
  output_id: string; constituency: { id: string; name: string; state: StateCode }; language: string; generated_language: string;
  party_name: string | null; topics: string[]; booth_ids: string[] | null; generator: string; live: boolean;
  warnings: string[]; generated_at: string; title: string; preamble: CpCited; closing: string;
  sections: { topic: string; local_context: string; context_sources: string[];
    commitments: { text: string; type: "party" | "local"; delivery: string; sources: string[] }[] }[];
  sources: CpSource[];
}
export interface CpDocument {
  doc_id: string; title: string; state: StateCode | null; constituency: string | null; constituency_id: string | null;
  doc_type: string; topics: string[]; source_citation: string; source_url: string | null; published_on: string | null;
  file_name: string | null; pages: number; pages_without_text: number; chars: number; chunks: number;
  uploaded_at: string; uploader: string | null; editable: boolean;
}
export interface CpIngestResult {
  file: string; status: "indexed" | "duplicate" | "error"; error?: string; doc_id?: string; title?: string;
  state?: StateCode | null; constituency?: string | null; doc_type?: string; topics?: string[]; published_on?: string | null;
  source_citation?: string; pages?: number; pages_without_text?: number; chunks?: number;
}
export interface CpQueryResult {
  query: string; translated: string | null; search: string;
  results: { id: string; chunk_id: string; doc_id: string; title: string; doc_type: string; topics: string; state: string | null;
    constituency: string | null; page: number | null; snippet: string; score: number; citation: string }[];
  answer?: { text: string; sources: string[]; generator: string; warnings: string[] };
}
export interface CpOutputRow { output_id: string; kind: "brief" | "manifesto"; constituency_id: string; language: string;
  params: Record<string, unknown>; created_at: string; generator: string; author: string | null }

// ---------- CounterPulse AI (war room) ----------
export type Severity = "Critical" | "High" | "Medium" | "Low";
export type WrMedium = "tv_brief" | "whatsapp" | "press_release" | "digital_ad";
export interface WrMeta {
  states: Record<string, unknown>; source_types: Record<string, string>; mediums: Record<WrMedium, string>;
  languages: Record<string, string>; severities: Severity[]; statuses: string[]; verdicts: Record<string, string>;
  state_significance: Record<string, number>; llm: { live: boolean; provider: string };
  active_by_severity: Record<Severity, number>;
  media: { items: number; sample_items: number; last_ingested_at: string | null };
  can_simulate: boolean; scope: { role: string; state: string | null; constituency: string | null };
}
export interface WrCluster {
  cluster_id: string; label: string; label_source: "auto" | "manual"; issue: string | null; state: StateCode | null;
  status: string; claim: string | null; threat_score: number; severity: Severity; item_count: number;
  items_24h: number; items_72h: number; trend: number[];
  breakdown: { volume?: number; velocity?: number; negativity?: number; reach?: number; spread?: number; state_significance?: number };
  first_seen: string; last_seen: string; constituencies: string[]; channels: string[]; source_mix: Record<string, number>;
  avg_negativity: number; negative_items: number;
  latest: { item_id: string; channel: string; source_type: string; published_at: string; text: string } | null;
  responses: number; approved_responses: number;
}
export interface WrMediaItem {
  item_id: string; published_at: string; state: StateCode | null; constituency: string | null; source_type: string;
  channel: string; url: string | null; text: string; language: string; negativity: number;
  sentiment_label: "negative" | "neutral" | "positive"; reach: number | null;
  cluster_id?: string | null; cluster_label?: string | null; threat_score?: number | null; severity?: Severity | null; sample?: number;
}
export interface WrClusterDetail extends WrCluster { items: WrMediaItem[] }
export interface WrIngestResult {
  ref: string; status: "ingested" | "duplicate" | "error"; error?: string; item_id?: string; cluster_id?: string;
  cluster_label?: string; new_cluster?: boolean; threat_score?: number; severity?: Severity; sentiment?: string;
  state?: string | null; constituency?: string | null;
}
export interface WrCheck { rule: string; status: "pass" | "fixed" | "info" | "action" | "fail"; detail: string }
export interface WrCopy {
  headline_message?: string; talking_points?: string[]; bridging_lines?: string[]; likely_questions?: { q: string; a: string }[];
  text?: string; characters?: number;
  headline?: string; body?: string[]; quote?: { text: string; attribution: string };
  variants?: { headline: string; body: string; cta: string }[];
  sources?: string[];
}
export interface WrCounter {
  response_id: string; status: "draft" | "approved" | "rejected"; reviewed_by?: string | null; reviewed_at?: string | null;
  cluster_id: string; label: string; state: StateCode | null; place: { id: string | null; name: string; state: StateCode | null };
  medium: WrMedium; medium_label: string; language: string; generated_language: string; party_name: string | null;
  threat: { score: number; severity: Severity; items_72h: number };
  narrative: { id: string; channel: string; source_type: string; published_at: string; text: string; negativity: number }[];
  threat_summary: string; core_claim: string;
  assessment: { verdict: string; verdict_label: string; explanation: string; sources: string[] };
  counter_facts: { fact: string; sources: string[] }[]; copy: WrCopy; avoid: string[];
  safety_check: { status: "clear" | "clear_with_fixes" | "blocked"; checks: WrCheck[] };
  warnings: string[]; sources: CpSource[]; generator: string; live: boolean; generated_at: string;
}
export interface WrResponseRow {
  response_id: string; cluster_id: string; medium: WrMedium; medium_label: string; language: string; status: string;
  created_at: string; label: string; generator: string; safety: string; author: string | null; reviewer: string | null;
}

// ---------- Command Center + pitch demo ----------
export interface DemoStatus {
  allowed: boolean; active: boolean; built_at: string | null; stale: boolean;
  stats: Record<string, number> | null; can_enable: boolean; can_rebuild: boolean;
  restored_session?: boolean;
}
export interface CmdKpis {
  active_booths: number; booths_covered_today: number; electors: number; scored_voters: number; persuade: number; gotv: number;
  manifestos: number; briefs: number; critical_threats: number; high_threats: number; active_narratives: number;
}
export interface CmdNarrative {
  cluster_id: string; label: string; state: StateCode | null; issue: string | null; threat_score: number; severity: Severity;
  item_count: number; items_24h: number; trend: number[]; claim: string | null; last_seen: string;
  response: { response_id: string; medium: string; medium_label: string; status: string; verdict: string; lead_fact: string; safety: string } | null;
}
export interface CmdOverview {
  area: { label: string; states: StateCode[]; constituency: string | null };
  kpis: CmdKpis; by_state: ({ state: StateCode; name: string } & CmdKpis)[] | null;
  voterengine: {
    quadrants: { quadrant: string; label: string; action: string; voters: number; share: number }[];
    top_booths: { booth_id: string; name: string; state: StateCode; constituency: string; total_voters: number; historical_turnout: number;
                  last_margin_pct: number | null; priority_score: number; persuade: number; gotv: number; focus: string }[];
    field: { date: string; planned: number; done: number; visited: number; progress: number; cadres: number; cadres_dispatched: number;
             consents_today: number; consents_granted_today: number };
  };
  copilot: {
    grievances: { constituency: string; constituency_id: string; title: string; figure: string; context: string; source: string }[];
    promises: { text: string; scope: string; citation: string }[];
    recent: { output_id: string; kind: string; constituency_id: string; place: string; event: string | null; created_at: string; author: string | null }[];
  };
  warroom: { narratives: CmdNarrative[]; sentiment: { days: number; items: number; negative: number; neutral: number; positive: number;
             negative_share: number; by_source: { source: string; negative: number; neutral: number; positive: number }[] };
             pending_approvals: number };
  actions: {
    dispatch: { cadres: number; dispatched: number; pending: number };
    speech: { constituency_id: string; name: string; state: StateCode; persuadable: number; audience: string; audience_label: string; has_documents: boolean } | null;
    threat: { cluster_id: string; label: string; state: StateCode; threat_score: number; severity: Severity; has_response: boolean; response_status: string | null } | null;
  };
  generated_at: string; workspace: "main" | "demo"; llm: { live: boolean; provider: string };
  states: { code: StateCode; name: string }[];
}
export interface DispatchResult { date: string; dispatched: number; already_dispatched: number; no_priority_doors: number; stops: number;
  field: CmdOverview["voterengine"]["field"] }
