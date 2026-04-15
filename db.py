"""Database abstraction — SQLite for local dev, Supabase for production."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from models import Candidate, EvidenceFact, ExtractionRun, Repo, AnalysisRun, Outcome


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

    # ========================================================================
    # Candidates
    # ========================================================================

    def upsert_candidate(self, candidate: Candidate) -> str:
        """Insert or update candidate. Returns candidate ID."""
        existing = self.get_candidate_by_github_id(candidate.github_id)
        if existing:
            self.conn.execute(
                """UPDATE candidates SET
                    display_name = ?, email = ?, bio = ?, company = ?,
                    location = ?, avatar_url = ?, public_repos = ?,
                    followers = ?, following = ?
                WHERE github_id = ?""",
                (candidate.display_name, candidate.email, candidate.bio,
                 candidate.company, candidate.location, candidate.avatar_url,
                 candidate.public_repos, candidate.followers, candidate.following,
                 candidate.github_id)
            )
            self.conn.commit()
            return existing["id"]
        else:
            self.conn.execute(
                """INSERT INTO candidates
                    (id, github_id, github_login, display_name, email, bio,
                     company, location, avatar_url, public_repos, followers,
                     following, github_created_at, discovered_via, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.id, candidate.github_id, candidate.github_login,
                 candidate.display_name, candidate.email, candidate.bio,
                 candidate.company, candidate.location, candidate.avatar_url,
                 candidate.public_repos, candidate.followers, candidate.following,
                 candidate.github_created_at, candidate.discovered_via,
                 candidate.created_at)
            )
            self.conn.commit()
            return candidate.id

    def get_candidate_by_github_id(self, github_id: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM candidates WHERE github_id = ?", (github_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_candidate_by_login(self, login: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM candidates WHERE github_login = ?", (login,)
        ).fetchone()
        return dict(row) if row else None

    def list_candidates(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM candidates ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ========================================================================
    # Repos
    # ========================================================================

    def upsert_repo(self, repo: Repo) -> str:
        """Insert or update repo snapshot."""
        existing = self.conn.execute(
            "SELECT id FROM repos WHERE candidate_id = ? AND github_repo_id = ?",
            (repo.candidate_id, repo.github_repo_id)
        ).fetchone()
        if existing:
            self.conn.execute(
                """UPDATE repos SET
                    full_name = ?, description = ?, primary_language = ?,
                    languages_json = ?, stars = ?, forks = ?, is_fork = ?,
                    is_archived = ?, pushed_at = ?, topics_json = ?,
                    has_ci = ?, has_tests = ?, test_file_count = ?,
                    source_file_count = ?, license = ?, default_branch = ?,
                    extraction_run_id = ?, extracted_at = ?
                WHERE candidate_id = ? AND github_repo_id = ?""",
                (repo.full_name, repo.description, repo.primary_language,
                 repo.languages_json, repo.stars, repo.forks, int(repo.is_fork),
                 int(repo.is_archived), repo.pushed_at, repo.topics_json,
                 int(repo.has_ci), int(repo.has_tests), repo.test_file_count,
                 repo.source_file_count, repo.license, repo.default_branch,
                 repo.extraction_run_id, repo.extracted_at,
                 repo.candidate_id, repo.github_repo_id)
            )
            self.conn.commit()
            return existing["id"]
        else:
            self.conn.execute(
                """INSERT INTO repos
                    (id, candidate_id, github_repo_id, full_name, description,
                     primary_language, languages_json, stars, forks, is_fork,
                     is_archived, created_at, pushed_at, topics_json,
                     has_ci, has_tests, test_file_count, source_file_count,
                     license, default_branch, extraction_run_id, extracted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (repo.id, repo.candidate_id, repo.github_repo_id, repo.full_name,
                 repo.description, repo.primary_language, repo.languages_json,
                 repo.stars, repo.forks, int(repo.is_fork), int(repo.is_archived),
                 repo.created_at, repo.pushed_at, repo.topics_json,
                 int(repo.has_ci), int(repo.has_tests), repo.test_file_count,
                 repo.source_file_count, repo.license, repo.default_branch,
                 repo.extraction_run_id, repo.extracted_at)
            )
            self.conn.commit()
            return repo.id

    # ========================================================================
    # Evidence Facts (APPEND-ONLY)
    # ========================================================================

    def insert_fact(self, fact: EvidenceFact) -> str:
        """Insert a single evidence fact. Append-only — no updates allowed."""
        self.conn.execute(
            """INSERT INTO evidence_facts
                (id, candidate_id, category, fact_key, fact_value, fact_type,
                 source, extraction_run_id, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fact.id, fact.candidate_id, fact.category, fact.fact_key,
             fact.fact_value, fact.fact_type, fact.source,
             fact.extraction_run_id, fact.extracted_at)
        )
        self.conn.commit()
        return fact.id

    def insert_facts_batch(self, facts: List[EvidenceFact]) -> int:
        """Insert multiple evidence facts in a single transaction."""
        self.conn.executemany(
            """INSERT INTO evidence_facts
                (id, candidate_id, category, fact_key, fact_value, fact_type,
                 source, extraction_run_id, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(f.id, f.candidate_id, f.category, f.fact_key, f.fact_value,
              f.fact_type, f.source, f.extraction_run_id, f.extracted_at)
             for f in facts]
        )
        self.conn.commit()
        return len(facts)

    def get_facts_for_candidate(self, candidate_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_facts WHERE candidate_id = ? ORDER BY category, fact_key",
            (candidate_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_evidence_json(self, candidate_id: str) -> Dict[str, Any]:
        """Build the evidence JSON blob for analysis — all facts for one candidate."""
        candidate = self.conn.execute(
            "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if not candidate:
            return {}

        facts = self.get_facts_for_candidate(candidate_id)
        repos = self.conn.execute(
            "SELECT * FROM repos WHERE candidate_id = ?", (candidate_id,)
        ).fetchall()

        # Group facts by category
        facts_by_category: Dict[str, List[Dict[str, Any]]] = {}
        for f in facts:
            cat = f["category"]
            if cat not in facts_by_category:
                facts_by_category[cat] = []
            facts_by_category[cat].append({
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

    # ========================================================================
    # Extraction Runs
    # ========================================================================

    def create_extraction_run(self, run: ExtractionRun) -> str:
        self.conn.execute(
            """INSERT INTO extraction_runs
                (id, candidate_id, trigger_type, repos_scanned, facts_extracted,
                 duration_ms, started_at, completed_at, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run.id, run.candidate_id, run.trigger_type, run.repos_scanned,
             run.facts_extracted, run.duration_ms, run.started_at,
             run.completed_at, run.status, run.error_message)
        )
        self.conn.commit()
        return run.id

    def complete_extraction_run(self, run_id: str, repos_scanned: int,
                                 facts_extracted: int, duration_ms: int,
                                 status: str = "completed",
                                 error_message: Optional[str] = None) -> None:
        from datetime import datetime
        self.conn.execute(
            """UPDATE extraction_runs SET
                repos_scanned = ?, facts_extracted = ?, duration_ms = ?,
                completed_at = ?, status = ?, error_message = ?
            WHERE id = ?""",
            (repos_scanned, facts_extracted, duration_ms,
             datetime.utcnow().isoformat(), status, error_message, run_id)
        )
        self.conn.commit()

    # ========================================================================
    # Analysis Runs
    # ========================================================================

    def insert_analysis_run(self, run: AnalysisRun) -> str:
        self.conn.execute(
            """INSERT INTO analysis_runs
                (id, candidate_id, evidence_snapshot_at, evidence_fact_count,
                 analysis_output_json, model_used, prompt_hash,
                 critic_passed, critic_findings_json, critic_attempts, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run.id, run.candidate_id, run.evidence_snapshot_at,
             run.evidence_fact_count, run.analysis_output_json,
             run.model_used, run.prompt_hash, int(run.critic_passed),
             run.critic_findings_json, run.critic_attempts, run.created_at)
        )
        self.conn.commit()
        return run.id

    def get_unanalyzed_candidates(self) -> List[Dict[str, Any]]:
        """Find candidates with evidence but no analysis."""
        rows = self.conn.execute(
            """SELECT c.* FROM candidates c
            WHERE c.id NOT IN (SELECT candidate_id FROM analysis_runs)
            AND c.id IN (SELECT DISTINCT candidate_id FROM evidence_facts)
            ORDER BY c.created_at"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ========================================================================
    # Outcomes (training sidecar)
    # ========================================================================

    def insert_outcome(self, outcome: Outcome) -> str:
        self.conn.execute(
            """INSERT INTO outcomes
                (id, candidate_id, decision, role, decision_date,
                 quality_rating, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (outcome.id, outcome.candidate_id, outcome.decision,
             outcome.role, outcome.decision_date, outcome.quality_rating,
             outcome.notes, outcome.created_at)
        )
        self.conn.commit()
        return outcome.id

    # ========================================================================
    # Seed Repos
    # ========================================================================

    def upsert_seed_repo(self, full_name: str, discovered_via: str) -> str:
        existing = self.conn.execute(
            "SELECT id FROM seed_repos WHERE full_name = ?", (full_name,)
        ).fetchone()
        if existing:
            return existing["id"]
        seed_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO seed_repos (id, full_name, discovered_via)
            VALUES (?, ?, ?)""",
            (seed_id, full_name, discovered_via)
        )
        self.conn.commit()
        return seed_id

    def list_seed_repos(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM seed_repos ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # ========================================================================
    # Evidence Diffing (for interview verification)
    # ========================================================================

    def diff_extractions(self, candidate_id: str,
                          run_id_old: str, run_id_new: str) -> Dict[str, Any]:
        """Compare two extraction runs for the same candidate.

        Returns facts that were added, removed (not in new run),
        or changed (same key, different value). This is the interview
        verification mechanism — if the candidate edited their profile
        between application and interview, the diff reveals it.
        """
        old_facts = self.conn.execute(
            """SELECT category, fact_key, fact_value, source
            FROM evidence_facts
            WHERE candidate_id = ? AND extraction_run_id = ?""",
            (candidate_id, run_id_old)
        ).fetchall()

        new_facts = self.conn.execute(
            """SELECT category, fact_key, fact_value, source
            FROM evidence_facts
            WHERE candidate_id = ? AND extraction_run_id = ?""",
            (candidate_id, run_id_new)
        ).fetchall()

        old_map = {(r["category"], r["fact_key"], r["source"]): r["fact_value"]
                   for r in old_facts}
        new_map = {(r["category"], r["fact_key"], r["source"]): r["fact_value"]
                   for r in new_facts}

        old_keys = set(old_map.keys())
        new_keys = set(new_map.keys())

        added = [
            {"category": k[0], "key": k[1], "source": k[2], "value": new_map[k]}
            for k in (new_keys - old_keys)
        ]
        removed = [
            {"category": k[0], "key": k[1], "source": k[2], "value": old_map[k]}
            for k in (old_keys - new_keys)
        ]
        changed = [
            {"category": k[0], "key": k[1], "source": k[2],
             "old_value": old_map[k], "new_value": new_map[k]}
            for k in (old_keys & new_keys)
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
