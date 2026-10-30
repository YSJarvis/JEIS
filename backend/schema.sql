-- VoterEngine AI — SQLite schema
-- Raw facts (booths, households, voters) are kept separate from derived
-- outputs (voter_scores, routes) so real data can replace mock data and the
-- scoring pipeline can be re-run without touching source records.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS booths (
    booth_id            TEXT PRIMARY KEY,          -- e.g. UP-MRT-001
    state               TEXT NOT NULL,             -- UP | PB | UK | GA
    constituency        TEXT NOT NULL,
    name                TEXT NOT NULL,
    total_voters        INTEGER NOT NULL DEFAULT 0,
    historical_turnout  REAL NOT NULL,             -- 0..100 (%)
    last_margin_pct     REAL,                      -- winning margin at booth, % of votes
    priority_score      REAL DEFAULT 0,            -- 0..100, recomputed by pipeline
    lat                 REAL NOT NULL,
    lng                 REAL NOT NULL,
    attributes          TEXT DEFAULT '{}'          -- JSON: terrain, etc.
);

CREATE TABLE IF NOT EXISTS households (
    household_id  TEXT PRIMARY KEY,
    booth_id      TEXT NOT NULL REFERENCES booths(booth_id) ON DELETE CASCADE,
    state         TEXT NOT NULL,
    address       TEXT,
    lat           REAL NOT NULL,
    lng           REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS voters (
    voter_id              TEXT PRIMARY KEY,        -- EPIC number in real data
    household_id          TEXT NOT NULL REFERENCES households(household_id) ON DELETE CASCADE,
    booth_id              TEXT NOT NULL REFERENCES booths(booth_id) ON DELETE CASCADE,
    state                 TEXT NOT NULL,
    name                  TEXT NOT NULL,
    age                   INTEGER NOT NULL,
    gender                TEXT NOT NULL,           -- M | F | O
    community_group       TEXT,                    -- caste / community group
    scheme_beneficiary    INTEGER NOT NULL DEFAULT 0,
    scheme_types          TEXT DEFAULT '',         -- comma-separated
    voting_history_index  REAL NOT NULL DEFAULT 0.5, -- share of last elections voted, 0..1
    party_affinity        REAL NOT NULL DEFAULT 0,   -- -1 opposition .. +1 our party
    is_migrant            INTEGER NOT NULL DEFAULT 0, -- migrant / NRI / absentee
    phone                 TEXT,
    lat                   REAL NOT NULL,
    lng                   REAL NOT NULL,
    attributes            TEXT DEFAULT '{}',       -- JSON: state-specific extras
    source                TEXT DEFAULT 'mock',     -- mock | csv | json
    consent_status        TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK (consent_status IN ('GRANTED','PENDING','OPT_OUT')),
    consent_updated_at    TEXT,                    -- when the current status was recorded
    consent_source        TEXT                     -- field | import | admin | mock
);

CREATE INDEX IF NOT EXISTS idx_voters_booth ON voters(booth_id);
CREATE INDEX IF NOT EXISTS idx_voters_state ON voters(state);
CREATE INDEX IF NOT EXISTS idx_voters_household ON voters(household_id);

CREATE TABLE IF NOT EXISTS voter_scores (
    voter_id        TEXT PRIMARY KEY REFERENCES voters(voter_id) ON DELETE CASCADE,
    swing_score     REAL NOT NULL,     -- 0..100
    turnout_score   REAL NOT NULL,     -- 0..100
    quadrant        TEXT NOT NULL,     -- PERSUADE | GOTV | OPPOSITION | LOW
    priority        REAL NOT NULL,     -- 0..100 contact priority
    drivers         TEXT DEFAULT '{}', -- JSON: per-feature contributions (explainability)
    model_version   TEXT NOT NULL,
    scored_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_quadrant ON voter_scores(quadrant);

CREATE TABLE IF NOT EXISTS cadres (
    cadre_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    state      TEXT NOT NULL,
    phone      TEXT,
    booth_ids  TEXT NOT NULL            -- comma-separated booths this cadre covers
);

CREATE TABLE IF NOT EXISTS routes (
    route_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    cadre_id     TEXT NOT NULL REFERENCES cadres(cadre_id) ON DELETE CASCADE,
    route_date   TEXT NOT NULL,          -- YYYY-MM-DD
    total_km     REAL NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_stops (
    stop_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id         INTEGER NOT NULL REFERENCES routes(route_id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    household_id     TEXT NOT NULL REFERENCES households(household_id) ON DELETE CASCADE,
    leg_km           REAL NOT NULL,
    household_priority REAL NOT NULL,
    status           TEXT NOT NULL DEFAULT 'PENDING', -- PENDING | VISITED | NOT_HOME | REFUSED | REVISIT
    notes            TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_stops_household ON route_stops(household_id);

CREATE TABLE IF NOT EXISTS ingestion_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    mode        TEXT NOT NULL,
    accepted    INTEGER NOT NULL,
    rejected    INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Auth, privacy and configuration (v2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    display_name   TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('ADMIN','MANAGER','CADRE')),
    state          TEXT,                 -- MANAGER / CADRE
    constituency   TEXT,                 -- MANAGER
    cadre_id       TEXT REFERENCES cadres(cadre_id) ON DELETE SET NULL,  -- CADRE
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    last_login_at  TEXT
);

-- Append-only consent audit trail (no FK: survives voter deletion).
CREATE TABLE IF NOT EXISTS consent_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    voter_id    TEXT NOT NULL,
    old_status  TEXT,
    new_status  TEXT NOT NULL,
    source      TEXT NOT NULL,           -- field | import | admin
    actor       TEXT NOT NULL,           -- user_id
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_consent_log_voter ON consent_log(voter_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT,
    role        TEXT,
    action      TEXT NOT NULL,
    detail      TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_by  TEXT,
    updated_at  TEXT
);

-- Admin overrides of config.STATES weights; key is a state code or '*' for thresholds.
CREATE TABLE IF NOT EXISTS scoring_config (
    scope       TEXT PRIMARY KEY,
    config      TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    updated_by  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS scoring_jobs (
    job_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL,          -- QUEUED | RUNNING | DONE | FAILED
    scope        TEXT NOT NULL,          -- state code or ALL
    requested_by TEXT,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    result       TEXT DEFAULT '{}',
    error        TEXT
);

-- Idempotency ledger for offline sync: each client op is applied at most once.
CREATE TABLE IF NOT EXISTS sync_ops (
    op_id        TEXT PRIMARY KEY,
    cadre_id     TEXT NOT NULL,
    op_type      TEXT NOT NULL,
    payload      TEXT NOT NULL,
    result       TEXT NOT NULL,
    received_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- v3: talking points (retrieval + LLM), daily directives, field operations
-- ---------------------------------------------------------------------------
-- Knowledge base the talking-point generator retrieves from. state/constituency
-- NULL = applies everywhere / state-wide. Content is campaign-owned and editable.
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id        TEXT PRIMARY KEY,
    state         TEXT,
    constituency  TEXT,
    kind          TEXT NOT NULL,          -- scheme | issue | turnout | context | brief
    title         TEXT NOT NULL,
    body          TEXT NOT NULL,          -- facts the LLM may use
    ask           TEXT,                   -- one listening-led line (used by the offline generator)
    tags          TEXT DEFAULT '',        -- comma-separated retrieval tags (scheme names, crops, ...)
    source        TEXT,                   -- where the facts come from
    active        INTEGER NOT NULL DEFAULT 1,
    updated_by    TEXT,
    updated_at    TEXT NOT NULL
);

-- Daily campaign focus set by managers (constituency) or strategists (state / all).
CREATE TABLE IF NOT EXISTS directives (
    directive_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    state         TEXT,                   -- NULL = all states
    constituency  TEXT,                   -- NULL = whole state
    for_date      TEXT NOT NULL,          -- YYYY-MM-DD (local)
    text          TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_directives_date ON directives(for_date);

CREATE TABLE IF NOT EXISTS talking_point_cache (
    cache_key     TEXT PRIMARY KEY,
    household_id  TEXT NOT NULL,
    for_date      TEXT NOT NULL,
    payload       TEXT NOT NULL,
    generator     TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- Last contact from each cadre's phone: drives "not yet synced" on the ops tab.
CREATE TABLE IF NOT EXISTS cadre_devices (
    cadre_id        TEXT PRIMARY KEY,
    last_seen_at    TEXT,
    last_sync_at    TEXT,
    reported_queue  INTEGER NOT NULL DEFAULT 0,
    app_version     TEXT
);

-- ---------------------------------------------------------------------------
-- v4: JanVani Copilot — constituency knowledge base and generated drafts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS copilot_documents (
    doc_id             TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    state              TEXT,                 -- NULL = central / national
    constituency       TEXT,                 -- NULL = state-wide
    constituency_id    TEXT,                 -- e.g. UP-SAR
    booth_ids          TEXT DEFAULT '',      -- optional: booth-cluster level material
    doc_type           TEXT NOT NULL,        -- budget | legislative | news | survey | party_promise | other
    topics             TEXT DEFAULT '',      -- Agriculture,Infrastructure,Youth,Welfare
    primary_topic      TEXT,
    source_citation    TEXT NOT NULL,
    source_url         TEXT,
    published_on       TEXT,
    file_name          TEXT,
    pages              INTEGER DEFAULT 1,
    pages_without_text INTEGER DEFAULT 0,
    chars              INTEGER DEFAULT 0,
    sha256             TEXT UNIQUE,
    detected           TEXT DEFAULT '{}',    -- what auto-detection found, for review
    active             INTEGER NOT NULL DEFAULT 1,
    uploaded_by        TEXT,
    uploaded_at        TEXT NOT NULL,
    updated_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_copilot_docs_place ON copilot_documents(state, constituency_id);

CREATE TABLE IF NOT EXISTS copilot_chunks (
    chunk_id   TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES copilot_documents(doc_id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    page       INTEGER,
    text       TEXT NOT NULL,
    tf         TEXT NOT NULL,                -- JSON term frequencies (BM25)
    length     INTEGER NOT NULL,
    embedding  TEXT                          -- JSON vector when an embedding provider is configured
);
CREATE INDEX IF NOT EXISTS idx_copilot_chunks_doc ON copilot_chunks(doc_id);

CREATE TABLE IF NOT EXISTS copilot_outputs (
    output_id        TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,          -- brief | manifesto | answer
    constituency_id  TEXT,
    language         TEXT,
    params           TEXT NOT NULL,
    payload          TEXT NOT NULL,
    created_by       TEXT,
    created_at       TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- v5: CounterPulse AI — media monitoring, narrative clusters, counter-responses
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media_items (
    item_id          TEXT PRIMARY KEY,
    state            TEXT,
    constituency     TEXT,
    constituency_id  TEXT,
    source_type      TEXT NOT NULL,       -- social | print | tv | youtube | survey | other
    channel          TEXT NOT NULL,       -- outlet, page or programme name (public channels only)
    url              TEXT,
    published_at     TEXT NOT NULL,       -- UTC ISO
    ingested_at      TEXT NOT NULL,
    text             TEXT NOT NULL,       -- phone numbers / emails scrubbed on ingest
    language         TEXT,
    negativity       REAL NOT NULL,       -- 0..1
    sentiment_label  TEXT NOT NULL,       -- negative | neutral | positive
    reach            REAL,                -- optional views / shares / circulation
    claim            TEXT,                -- the item's central claim sentence
    issue            TEXT,                -- matched issue label, if any
    tf               TEXT NOT NULL,       -- JSON term weights for clustering
    cluster_id       TEXT,
    sha256           TEXT UNIQUE,
    ingested_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_cluster ON media_items(cluster_id);
CREATE INDEX IF NOT EXISTS idx_media_time ON media_items(published_at);
CREATE INDEX IF NOT EXISTS idx_media_place ON media_items(state, constituency_id);

CREATE TABLE IF NOT EXISTS narrative_clusters (
    cluster_id     TEXT PRIMARY KEY,
    state          TEXT,
    label          TEXT NOT NULL,
    label_source   TEXT NOT NULL DEFAULT 'auto',   -- auto | manual
    issue          TEXT,
    status         TEXT NOT NULL DEFAULT 'active', -- active | responded | dismissed
    centroid       TEXT NOT NULL,                  -- JSON term weights
    claim          TEXT,
    first_seen     TEXT,
    last_seen      TEXT,
    item_count     INTEGER NOT NULL DEFAULT 0,
    threat_score   INTEGER NOT NULL DEFAULT 1,
    severity       TEXT NOT NULL DEFAULT 'Low',
    breakdown      TEXT DEFAULT '{}',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS warroom_responses (
    response_id  TEXT PRIMARY KEY,
    cluster_id   TEXT NOT NULL REFERENCES narrative_clusters(cluster_id) ON DELETE CASCADE,
    medium       TEXT NOT NULL,          -- tv_brief | whatsapp | press_release | digital_ad
    language     TEXT NOT NULL,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | rejected
    created_by   TEXT,
    created_at   TEXT NOT NULL,
    reviewed_by  TEXT,
    reviewed_at  TEXT
);

-- ---------------------------------------------------------------------------
-- v6: workspace metadata (pitch-demo build stamp, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
