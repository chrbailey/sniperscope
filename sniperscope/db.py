"""SQLite evidence store.

Append-only on ``evidence_facts`` is enforced by database triggers (see
schema.sql), not by convention. All INSERT statements are generated from a
single per-table column list so the schema is declared in exactly one place
per table.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sniperscope import config
from sniperscope.models import (
    AnalysisRun,
    Candidate,
    EvidenceFact,
    ExtractionRun,
    Outcome,
    Repo,
    utc_now_iso,
)

# Column lists mirror the Pydantic model fields — one source of truth per
# table for INSERTs. Update the model, the schema, and this tuple together.
_CANDIDATE_COLS = (
    "id", "github_id", "github_login", "display_name", "email", "bio",
    "company", "location", "avatar_url", "public_repos", "followers",
    "following", "github_created_at", "discovered_via", "created_at",
)
# Profile fields refreshed when a known candidate is seen again. Identity and
# provenance (github_login, github_created_at, discovered_via) are not.
_CANDIDATE_REFRESH_COLS = (
    "display_name", "email", "bio", "company", "location", "avatar_url",
    "public_repos", "followers", "following",
)
_REPO_COLS = (
    "id", "candidate_id", "github_repo_id", "full_name", "description",
    "primary_language", "languages_json", "stars", "forks", "is_fork",
    "is_archived", "created_at", "pushed_at", "topics_json", "has_ci",
    "has_tests", "test_file_count", "source_file_count", "license",
    "default_branch", "extraction_run_id", "extracted_at",
)
_REPO_REFRESH_COLS = (
    "full_name", "description", "primary_language", "languages_json",
    "stars", "forks", "is_fork", "is_archived", "pushed_at", "topics_json",
    "has_ci", "has_tests", "test_file_count", "source_file_count",
    "license", "default_branch", "extraction_run_id", "extracted_at",
)
_FACT_COLS = (
    "id", "candidate_id", "category", "fact_key", "fact_value", "fact_type",
    "source", "extraction_run_id", "extracted_at",
)
_EXTRACTION_RUN_COLS = (
    "id", "candidate_id", "trigger_type", "repos_scanned", "facts_extracted",
    "duration_ms", "started_at", "completed_at", "status", "error_message",
)
_ANALYSIS_RUN_COLS = (
    "id", "candidate_id", "evidence_snapshot_at", "evidence_fact_count",
    "analysis_output_json", "model_used", "prompt_hash", "critic_passed",
    "critic_findings_json", "critic_attempts", "created_at",
)
_OUTCOME_COLS = (
    "id", "candidate_id", "decision", "role", "decision_date",
    "quality_rating", "notes", "created_at",
)


def _values(model: Any, cols: Sequence[str]) -> List[Any]:
    """Pull column values off a model, coercing bools to SQLite integers."""
    out = []
    for col in cols:
        value = getattr(model, col)
        out.append(int(value) if isinstance(value, bool) else value)
    return out


def _insert_sql(table: str, cols: Sequence[str]) -> str:
    return "INSERT INTO {} ({}) VALUES ({})".format(
        table, ", ".join(cols), ", ".join("?" for _ in cols)
    )


def _upsert_sql(table: str, cols: Sequence[str], conflict_cols: Sequence[str],
                refresh_cols: Sequence[str]) -> str:
    return "{} ON CONFLICT({}) DO UPDATE SET {}".format(
        _insert_sql(table, cols),
        ", ".join(conflict_cols),
        ", ".join("{0} = excluded.{0}".format(c) for c in refresh_cols),
    )


class Database:
    """SQLite-backed evidence store with append-only enforcement."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.SQLITE_PATH
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        schema_path = Path(__file__).parent / "schema.sql"
        with open(schema_path) as f:
            self.conn.executescript(f.read())

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _row(self, query: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(query, params).fetchone()
        return dict(row) if row else None

    def _rows(self, query: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    def upsert_candidate(self, candidate: Candidate) -> str:
        """Insert or refresh a candidate. Returns the stored candidate id."""
        self.conn.execute(
            _upsert_sql("candidates", _CANDIDATE_COLS, ("github_id",),
                        _CANDIDATE_REFRESH_COLS),
            _values(candidate, _CANDIDATE_COLS),
        )
        self.conn.commit()
        stored = self.get_candidate_by_github_id(candidate.github_id)
        assert stored is not None
        return stored["id"]

    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM candidates WHERE id = ?", (candidate_id,))

    def get_candidate_by_github_id(self, github_id: int) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM candidates WHERE github_id = ?", (github_id,))

    def get_candidate_by_login(self, login: str) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM candidates WHERE github_login = ?", (login,))

    def list_candidates(self) -> List[Dict[str, Any]]:
        return self._rows("SELECT * FROM candidates ORDER BY created_at DESC")

    # ------------------------------------------------------------------
    # Repos
    # ------------------------------------------------------------------

    def upsert_repo(self, repo: Repo) -> str:
        """Insert or refresh a repo snapshot. Returns the stored repo id."""
        self.conn.execute(
            _upsert_sql("repos", _REPO_COLS, ("candidate_id", "github_repo_id"),
                        _REPO_REFRESH_COLS),
            _values(repo, _REPO_COLS),
        )
        self.conn.commit()
        stored = self._row(
            "SELECT id FROM repos WHERE candidate_id = ? AND github_repo_id = ?",
            (repo.candidate_id, repo.github_repo_id),
        )
        assert stored is not None
        return stored["id"]

    # ------------------------------------------------------------------
    # Evidence facts (APPEND-ONLY)
    # ------------------------------------------------------------------

    def insert_fact(self, fact: EvidenceFact) -> str:
        """Insert a single evidence fact. Append-only — no updates allowed."""
        self.conn.execute(_insert_sql("evidence_facts", _FACT_COLS),
                          _values(fact, _FACT_COLS))
        self.conn.commit()
        return fact.id

    def insert_facts_batch(self, facts: List[EvidenceFact]) -> int:
        """Insert multiple evidence facts in a single transaction."""
        self.conn.executemany(
            _insert_sql("evidence_facts", _FACT_COLS),
            [_values(f, _FACT_COLS) for f in facts],
        )
        self.conn.commit()
        return len(facts)

    def get_facts_for_candidate(self, candidate_id: str) -> List[Dict[str, Any]]:
        return self._rows(
            "SELECT * FROM evidence_facts WHERE candidate_id = ? "
            "ORDER BY category, fact_key",
            (candidate_id,),
        )

    def get_evidence_json(self, candidate_id: str) -> Dict[str, Any]:
        """Build the evidence blob for analysis — all facts for one candidate.

        This is the ONLY input the analysis LLM receives.
        """
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            return {}

        facts = self.get_facts_for_candidate(candidate_id)
        repos = self._rows("SELECT * FROM repos WHERE candidate_id = ?",
                           (candidate_id,))

        facts_by_category: Dict[str, List[Dict[str, Any]]] = {}
        for f in facts:
            facts_by_category.setdefault(f["category"], []).append({
                "key": f["fact_key"],
                "value": f["fact_value"],
                "type": f["fact_type"],
                "source": f["source"],
            })

        return {
            "candidate": {
                "github_login": candidate["github_login"],
                "display_name": candidate["display_name"],
                "bio": candidate["bio"],
                "company": candidate["company"],
                "location": candidate["location"],
                "public_repos": candidate["public_repos"],
                "followers": candidate["followers"],
                "github_created_at": candidate["github_created_at"],
                "discovered_via": candidate["discovered_via"],
            },
            "repos": [
                {
                    "full_name": r["full_name"],
                    "primary_language": r["primary_language"],
                    "stars": r["stars"],
                    "forks": r["forks"],
                    "is_fork": bool(r["is_fork"]),
                    "has_ci": bool(r["has_ci"]),
                    "has_tests": bool(r["has_tests"]),
                    "test_file_count": r["test_file_count"],
                    "source_file_count": r["source_file_count"],
                    "pushed_at": r["pushed_at"],
                    "topics": json.loads(r["topics_json"]) if r["topics_json"] else [],
                }
                for r in repos
            ],
            "evidence": facts_by_category,
            "metadata": {
                "total_facts": len(facts),
                "total_repos": len(repos),
                "extracted_at": max((f["extracted_at"] for f in facts), default=None),
            },
        }

    # ------------------------------------------------------------------
    # Extraction runs
    # ------------------------------------------------------------------

    def create_extraction_run(self, run: ExtractionRun) -> str:
        self.conn.execute(_insert_sql("extraction_runs", _EXTRACTION_RUN_COLS),
                          _values(run, _EXTRACTION_RUN_COLS))
        self.conn.commit()
        return run.id

    def set_extraction_run_candidate(self, run_id: str, candidate_id: str) -> None:
        self.conn.execute(
            "UPDATE extraction_runs SET candidate_id = ? WHERE id = ?",
            (candidate_id, run_id),
        )
        self.conn.commit()

    def complete_extraction_run(self, run_id: str, repos_scanned: int,
                                facts_extracted: int, duration_ms: int,
                                status: str = "completed",
                                error_message: Optional[str] = None) -> None:
        self.conn.execute(
            """UPDATE extraction_runs SET
                repos_scanned = ?, facts_extracted = ?, duration_ms = ?,
                completed_at = ?, status = ?, error_message = ?
            WHERE id = ?""",
            (repos_scanned, facts_extracted, duration_ms,
             utc_now_iso(), status, error_message, run_id),
        )
        self.conn.commit()

    def has_recent_extraction(self, candidate_id: str, cutoff_iso: str) -> bool:
        """True if a completed extraction exists after the cutoff timestamp."""
        row = self._row(
            """SELECT id FROM extraction_runs
               WHERE candidate_id = ? AND status = 'completed'
                 AND completed_at > ?
               LIMIT 1""",
            (candidate_id, cutoff_iso),
        )
        return row is not None

    # ------------------------------------------------------------------
    # Analysis runs
    # ------------------------------------------------------------------

    def insert_analysis_run(self, run: AnalysisRun) -> str:
        self.conn.execute(_insert_sql("analysis_runs", _ANALYSIS_RUN_COLS),
                          _values(run, _ANALYSIS_RUN_COLS))
        self.conn.commit()
        return run.id

    def get_unanalyzed_candidates(self) -> List[Dict[str, Any]]:
        """Candidates with evidence but no analysis."""
        return self._rows(
            """SELECT c.* FROM candidates c
            WHERE c.id NOT IN (SELECT candidate_id FROM analysis_runs)
            AND c.id IN (SELECT DISTINCT candidate_id FROM evidence_facts)
            ORDER BY c.created_at"""
        )

    # ------------------------------------------------------------------
    # Outcomes (training sidecar)
    # ------------------------------------------------------------------

    def insert_outcome(self, outcome: Outcome) -> str:
        self.conn.execute(_insert_sql("outcomes", _OUTCOME_COLS),
                          _values(outcome, _OUTCOME_COLS))
        self.conn.commit()
        return outcome.id

    # ------------------------------------------------------------------
    # Seed repos
    # ------------------------------------------------------------------

    def upsert_seed_repo(self, full_name: str, discovered_via: str) -> str:
        existing = self._row("SELECT id FROM seed_repos WHERE full_name = ?",
                             (full_name,))
        if existing:
            return existing["id"]
        seed_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO seed_repos (id, full_name, discovered_via) VALUES (?, ?, ?)",
            (seed_id, full_name, discovered_via),
        )
        self.conn.commit()
        return seed_id

    def list_seed_repos(self) -> List[Dict[str, Any]]:
        return self._rows("SELECT * FROM seed_repos ORDER BY created_at")

    def update_seed_crawl_stats(self, full_name: str, contributor_count: int) -> None:
        self.conn.execute(
            """UPDATE seed_repos
               SET last_crawled_at = ?, contributor_count = ?
               WHERE full_name = ?""",
            (utc_now_iso(), contributor_count, full_name),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Evidence diffing (interview verification)
    # ------------------------------------------------------------------

    def diff_extractions(self, candidate_id: str,
                         run_id_old: str, run_id_new: str) -> Dict[str, Any]:
        """Compare two extraction runs for the same candidate.

        Returns facts that were added, removed (not in new run), or changed
        (same key, different value). This is the interview verification
        mechanism — if the candidate edited their profile between application
        and interview, the diff reveals it.
        """
        query = """SELECT category, fact_key, fact_value, source
                   FROM evidence_facts
                   WHERE candidate_id = ? AND extraction_run_id = ?"""
        old_facts = self._rows(query, (candidate_id, run_id_old))
        new_facts = self._rows(query, (candidate_id, run_id_new))

        old_map = {(r["category"], r["fact_key"], r["source"]): r["fact_value"]
                   for r in old_facts}
        new_map = {(r["category"], r["fact_key"], r["source"]): r["fact_value"]
                   for r in new_facts}

        added = [
            {"category": k[0], "key": k[1], "source": k[2], "value": new_map[k]}
            for k in (new_map.keys() - old_map.keys())
        ]
        removed = [
            {"category": k[0], "key": k[1], "source": k[2], "value": old_map[k]}
            for k in (old_map.keys() - new_map.keys())
        ]
        changed = [
            {"category": k[0], "key": k[1], "source": k[2],
             "old_value": old_map[k], "new_value": new_map[k]}
            for k in (old_map.keys() & new_map.keys())
            if old_map[k] != new_map[k]
        ]

        return {
            "candidate_id": candidate_id,
            "old_run_id": run_id_old,
            "new_run_id": run_id_new,
            "added": sorted(added, key=lambda x: (x["category"], x["key"])),
            "removed": sorted(removed, key=lambda x: (x["category"], x["key"])),
            "changed": sorted(changed, key=lambda x: (x["category"], x["key"])),
            "summary": {
                "facts_added": len(added),
                "facts_removed": len(removed),
                "facts_changed": len(changed),
                "old_total": len(old_facts),
                "new_total": len(new_facts),
            },
        }
