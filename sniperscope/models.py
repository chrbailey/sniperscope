"""Pydantic models for evidence and analysis data structures."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    """Naive-UTC ISO-8601 timestamp.

    Kept naive (no offset suffix) so new rows sort and compare correctly
    against timestamps already stored by earlier versions.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class Candidate(BaseModel):
    id: str = Field(default_factory=new_id)
    github_id: int
    github_login: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    avatar_url: Optional[str] = None
    public_repos: Optional[int] = None
    followers: Optional[int] = None
    following: Optional[int] = None
    github_created_at: Optional[str] = None
    discovered_via: str
    created_at: str = Field(default_factory=utc_now_iso)


class Repo(BaseModel):
    """Per-candidate repo snapshot. Mutable — refreshed on re-extraction."""

    id: str = Field(default_factory=new_id)
    candidate_id: str
    github_repo_id: int
    full_name: str
    description: Optional[str] = None
    primary_language: Optional[str] = None
    languages_json: Optional[str] = None
    stars: int = 0
    forks: int = 0
    is_fork: bool = False
    is_archived: bool = False
    created_at: Optional[str] = None
    pushed_at: Optional[str] = None
    topics_json: Optional[str] = None
    has_ci: bool = False
    has_tests: bool = False
    test_file_count: int = 0
    source_file_count: int = 0
    license: Optional[str] = None
    default_branch: Optional[str] = None
    extraction_run_id: str = ""
    extracted_at: str = Field(default_factory=utc_now_iso)


class EvidenceFact(BaseModel):
    """The core unit of evidence. Append-only at the database layer."""

    id: str = Field(default_factory=new_id)
    candidate_id: str
    category: str       # "commit_pattern", "testing", "domain_keyword", ...
    fact_key: str       # "test_file_ratio", "commits_per_week_avg", ...
    fact_value: str     # "0.34", "12.5", "true"
    fact_type: str      # "number", "string", "boolean", "json"
    source: str         # "github:repo:owner/name", "github:user:login", "arxiv:id"
    extraction_run_id: str
    extracted_at: str = Field(default_factory=utc_now_iso)


class ExtractionRun(BaseModel):
    id: str = Field(default_factory=new_id)
    candidate_id: Optional[str] = None
    trigger_type: str   # "initial", "incremental", "manual_reverify", "seed_crawl", "arxiv_search"
    repos_scanned: int = 0
    facts_extracted: int = 0
    duration_ms: Optional[int] = None
    started_at: str = Field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None
    status: str = "running"
    error_message: Optional[str] = None


class AnalysisRun(BaseModel):
    id: str = Field(default_factory=new_id)
    candidate_id: str
    evidence_snapshot_at: str
    evidence_fact_count: int
    analysis_output_json: str
    model_used: str
    prompt_hash: str
    critic_passed: bool
    critic_findings_json: Optional[str] = None
    critic_attempts: int = 1
    created_at: str = Field(default_factory=utc_now_iso)


class Outcome(BaseModel):
    """Training sidecar — the human decision, recorded after the fact."""

    id: str = Field(default_factory=new_id)
    candidate_id: str
    decision: Optional[str] = None
    role: Optional[str] = None
    decision_date: Optional[str] = None
    quality_rating: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)


EVIDENCE_CATEGORIES = [
    "language",          # programming languages used
    "testing",           # test discipline signals
    "commit_pattern",    # commit frequency, cadence, message style
    "ci_cd",             # CI/CD presence and configuration
    "domain_keyword",    # ERP, framework, protocol mentions
    "temporal",          # activity timeline, gaps, lifecycle
    "collaboration",     # PR behavior, co-authors, team signals
    "dependency",        # frameworks, libraries, tool choices
    "repo_metadata",     # stars, forks, topics, visibility
    "research_paper",    # arXiv papers, academic publications
]

# Domain keywords to detect (no weighting — just counts).
# IMPORTANT: These are for COUNTING occurrences in evidence only.
# They must NEVER be used to filter or exclude candidates.
# The rubric-free principle requires that what matters is learned
# from labeled outcomes, not prescribed by keyword lists.
DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "erp": ["erp", "enterprise resource planning"],
    "netsuite": ["netsuite", "suitescript", "suitetalk", "suiteql", "suitelet", "restlet", "saved search", "netsuite next"],
    "sap": ["sap", "abap", "bapi", "rfc", "idoc", "fiori", "s4hana", "netweaver", "tcode"],
    "oracle": ["oracle", "oracle cloud", "oracle erp", "jde", "peoplesoft"],
    "workday": ["workday", "workday hcm", "workday integration"],
    "salesforce": ["salesforce", "sfdc", "apex", "soql", "lightning"],
    "anthropic": ["anthropic", "claude", "claude code", "claude api"],
    "mcp": ["mcp", "model context protocol", "mcp server", "mcp tool"],
    "ai_agent": ["agent", "agentic", "ai agent", "llm agent", "tool use"],
    "governance": ["governance", "audit", "compliance", "sox", "soc", "segregation of duties"],
}
