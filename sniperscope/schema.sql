-- Sniperscope Schema
-- Evidence-based talent radar for Claude + ERP intersection
-- Design principle: extraction and analysis are physically separated
-- All evidence is append-only — no UPDATE, no DELETE

-- ============================================================================
-- Candidates
-- ============================================================================
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    github_id INTEGER UNIQUE NOT NULL,
    github_login TEXT NOT NULL,
    display_name TEXT,
    email TEXT,
    bio TEXT,
    company TEXT,
    location TEXT,
    avatar_url TEXT,
    public_repos INTEGER,
    followers INTEGER,
    following INTEGER,
    github_created_at TEXT,
    discovered_via TEXT NOT NULL,  -- "seed:opensuitemcp/opensuitemcp" or "search:netsuite mcp"
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Repos (snapshot per candidate, updated on re-extraction)
-- ============================================================================
CREATE TABLE IF NOT EXISTS repos (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    github_repo_id INTEGER NOT NULL,
    full_name TEXT NOT NULL,
    description TEXT,
    primary_language TEXT,
    languages_json TEXT,          -- JSON: {"TypeScript": 62000, "Python": 3400}
    stars INTEGER DEFAULT 0,
    forks INTEGER DEFAULT 0,
    is_fork INTEGER DEFAULT 0,   -- SQLite boolean
    is_archived INTEGER DEFAULT 0,
    created_at TEXT,
    pushed_at TEXT,
    topics_json TEXT,             -- JSON array: ["mcp", "netsuite"]
    has_ci INTEGER DEFAULT 0,    -- .github/workflows/ exists
    has_tests INTEGER DEFAULT 0, -- test files detected
    test_file_count INTEGER DEFAULT 0,
    source_file_count INTEGER DEFAULT 0,
    license TEXT,
    default_branch TEXT,
    extraction_run_id TEXT NOT NULL,
    extracted_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(candidate_id, github_repo_id)
);

-- ============================================================================
-- Evidence Facts (APPEND-ONLY — the core anti-manipulation table)
-- ============================================================================
CREATE TABLE IF NOT EXISTS evidence_facts (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    category TEXT NOT NULL,       -- "commit_pattern", "testing", "domain_keyword", "language", "ci", "temporal", "collaboration"
    fact_key TEXT NOT NULL,       -- "test_file_ratio", "commit_frequency_weekly_avg", "domain:netsuite:commit_mentions"
    fact_value TEXT NOT NULL,     -- "0.34", "12.5", "true"
    fact_type TEXT NOT NULL,      -- "number", "string", "boolean", "json"
    source TEXT NOT NULL,         -- "github:repo:chrbailey/promptspeak-mcp-server", "github:user:chrbailey"
    extraction_run_id TEXT NOT NULL,
    extracted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- NO UPDATE TRIGGER — enforce append-only
CREATE TRIGGER IF NOT EXISTS prevent_evidence_update
BEFORE UPDATE ON evidence_facts
BEGIN
    SELECT RAISE(ABORT, 'evidence_facts is append-only: updates are not permitted');
END;

-- NO DELETE TRIGGER — enforce append-only
CREATE TRIGGER IF NOT EXISTS prevent_evidence_delete
BEFORE DELETE ON evidence_facts
BEGIN
    SELECT RAISE(ABORT, 'evidence_facts is append-only: deletes are not permitted');
END;

-- ============================================================================
-- Extraction Runs (audit trail)
-- ============================================================================
CREATE TABLE IF NOT EXISTS extraction_runs (
    id TEXT PRIMARY KEY,
    candidate_id TEXT REFERENCES candidates(id), -- NULL for seed crawl runs
    trigger_type TEXT NOT NULL,   -- "initial", "incremental", "manual_reverify", "seed_crawl", "search_discovery"
    repos_scanned INTEGER DEFAULT 0,
    facts_extracted INTEGER DEFAULT 0,
    duration_ms INTEGER,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',  -- "running", "completed", "failed", "partial"
    error_message TEXT
);

-- ============================================================================
-- Analysis Runs (physically separate from extraction)
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis_runs (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    evidence_snapshot_at TEXT NOT NULL,  -- point-in-time of evidence used
    evidence_fact_count INTEGER NOT NULL,
    analysis_output_json TEXT NOT NULL,  -- structured assessment
    model_used TEXT NOT NULL,            -- "claude-sonnet-4-6"
    prompt_hash TEXT NOT NULL,           -- SHA-256 of analysis prompt
    critic_passed INTEGER NOT NULL,      -- 0 or 1: did critic loop approve?
    critic_findings_json TEXT,           -- what the critic found
    critic_attempts INTEGER DEFAULT 1,   -- how many worker/critic rounds
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Outcomes (training sidecar — you fill this manually)
-- ============================================================================
CREATE TABLE IF NOT EXISTS outcomes (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    decision TEXT,                -- "contacted", "responded", "met", "hired", "rejected", "no_response"
    role TEXT,                    -- what role you considered them for
    decision_date TEXT,
    quality_rating TEXT,          -- "strong", "possible", "weak" — your assessment after interaction
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Seed Repos (track which repos we're monitoring)
-- ============================================================================
CREATE TABLE IF NOT EXISTS seed_repos (
    id TEXT PRIMARY KEY,
    full_name TEXT UNIQUE NOT NULL,    -- "opensuitemcp/opensuitemcp"
    discovered_via TEXT NOT NULL,       -- "manual", "search:netsuite mcp", "starred_by:chrbailey"
    contributor_count INTEGER DEFAULT 0,
    last_crawled_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Indexes
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_evidence_candidate ON evidence_facts(candidate_id);
CREATE INDEX IF NOT EXISTS idx_evidence_category ON evidence_facts(category);
CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence_facts(extraction_run_id);
CREATE INDEX IF NOT EXISTS idx_repos_candidate ON repos(candidate_id);
CREATE INDEX IF NOT EXISTS idx_analysis_candidate ON analysis_runs(candidate_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_candidate ON outcomes(candidate_id);
CREATE INDEX IF NOT EXISTS idx_extraction_candidate ON extraction_runs(candidate_id);
