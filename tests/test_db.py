"""Tests for the Database class — evidence store with append-only enforcement."""
from __future__ import annotations

import sqlite3

import pytest

from db import Database
from models import Candidate, EvidenceFact, ExtractionRun, Repo


# ============================================================================
# Candidate CRUD
# ============================================================================

class TestUpsertCandidate:

    def test_upsert_candidate_creates_new(self, db, sample_candidate):
        """Inserting a candidate for the first time returns its generated ID."""
        cid = db.upsert_candidate(sample_candidate)
        assert cid == sample_candidate.id

        row = db.get_candidate_by_github_id(sample_candidate.github_id)
        assert row is not None
        assert row["github_login"] == "testuser"
        assert row["display_name"] == "Test User"
        assert row["discovered_via"] == "seed:opensuitemcp/opensuitemcp"

    def test_upsert_candidate_updates_existing(self, db, sample_candidate):
        """Re-upserting the same github_id updates mutable fields and returns
        the original ID (not a new one)."""
        original_id = db.upsert_candidate(sample_candidate)

        updated = Candidate(
            github_id=sample_candidate.github_id,
            github_login=sample_candidate.github_login,
            display_name="Updated Name",
            email="new@example.com",
            bio="Updated bio",
            company="New Corp",
            location="New York, NY",
            avatar_url=sample_candidate.avatar_url,
            public_repos=99,
            followers=200,
            following=75,
            discovered_via=sample_candidate.discovered_via,
        )
        returned_id = db.upsert_candidate(updated)

        assert returned_id == original_id

        row = db.get_candidate_by_github_id(sample_candidate.github_id)
        assert row["display_name"] == "Updated Name"
        assert row["email"] == "new@example.com"
        assert row["company"] == "New Corp"
        assert row["public_repos"] == 99


class TestGetCandidate:

    def test_get_candidate_by_github_id(self, db, sample_candidate):
        """Look up a candidate by numeric GitHub ID."""
        db.upsert_candidate(sample_candidate)
        row = db.get_candidate_by_github_id(12345)
        assert row is not None
        assert row["github_login"] == "testuser"

    def test_get_candidate_by_github_id_missing(self, db):
        """Non-existent github_id returns None."""
        assert db.get_candidate_by_github_id(999999) is None

    def test_get_candidate_by_login(self, db, sample_candidate):
        """Look up a candidate by GitHub login string."""
        db.upsert_candidate(sample_candidate)
        row = db.get_candidate_by_login("testuser")
        assert row is not None
        assert row["github_id"] == 12345

    def test_get_candidate_by_login_missing(self, db):
        """Non-existent login returns None."""
        assert db.get_candidate_by_login("ghost") is None


# ============================================================================
# Evidence Facts — APPEND-ONLY (critical anti-manipulation tests)
# ============================================================================

class TestEvidenceFacts:

    def test_insert_facts_batch(self, db, sample_candidate, make_fact):
        """Batch-inserting multiple facts persists them all."""
        cid = db.upsert_candidate(sample_candidate)
        run_id = "run-batch-001"

        facts = [
            make_fact(cid, run_id, category="testing", key="test_file_ratio", value="0.34"),
            make_fact(cid, run_id, category="language", key="primary_language", value="Python",
                      fact_type="string"),
            make_fact(cid, run_id, category="commit_pattern", key="commit_frequency_weekly_avg",
                      value="12.5"),
        ]
        count = db.insert_facts_batch(facts)
        assert count == 3

        stored = db.get_facts_for_candidate(cid)
        assert len(stored) == 3
        categories = {f["category"] for f in stored}
        assert categories == {"testing", "language", "commit_pattern"}

    def test_evidence_facts_append_only(self, db, sample_candidate, make_fact):
        """UPDATE on evidence_facts must raise an error — this is the
        anti-manipulation wall.  If this test fails, the integrity guarantee
        is broken."""
        cid = db.upsert_candidate(sample_candidate)
        run_id = "run-append-001"
        fact = make_fact(cid, run_id)
        db.insert_fact(fact)

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.conn.execute(
                "UPDATE evidence_facts SET fact_value = ? WHERE id = ?",
                ("0.99", fact.id)
            )

    def test_evidence_facts_no_delete(self, db, sample_candidate, make_fact):
        """DELETE on evidence_facts must raise an error — evidence is
        permanent.  If this test fails, facts can be silently erased."""
        cid = db.upsert_candidate(sample_candidate)
        run_id = "run-nodelete-001"
        fact = make_fact(cid, run_id)
        db.insert_fact(fact)

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.conn.execute("DELETE FROM evidence_facts WHERE id = ?", (fact.id,))


# ============================================================================
# Evidence JSON structure
# ============================================================================

class TestEvidenceJson:

    def test_get_evidence_json_structure(self, db, sample_candidate, make_fact, make_repo):
        """get_evidence_json returns a well-formed dict with candidate, repos,
        evidence, and metadata sections."""
        cid = db.upsert_candidate(sample_candidate)
        run_id = "run-json-001"

        # Add a repo
        repo = make_repo(cid, run_id)
        db.upsert_repo(repo)

        # Add some facts
        facts = [
            make_fact(cid, run_id, category="testing", key="test_file_ratio", value="0.34"),
            make_fact(cid, run_id, category="language", key="primary_language", value="Python",
                      fact_type="string"),
        ]
        db.insert_facts_batch(facts)

        result = db.get_evidence_json(cid)

        # Top-level keys
        assert "candidate" in result
        assert "repos" in result
        assert "evidence" in result
        assert "metadata" in result

        # Candidate section
        assert result["candidate"]["github_login"] == "testuser"
        assert result["candidate"]["discovered_via"] == "seed:opensuitemcp/opensuitemcp"

        # Repos section
        assert len(result["repos"]) == 1
        assert result["repos"][0]["full_name"] == "testuser/testrepo"
        assert result["repos"][0]["topics"] == ["netsuite", "mcp"]
        assert result["repos"][0]["has_tests"] is True

        # Evidence section — grouped by category
        assert "testing" in result["evidence"]
        assert "language" in result["evidence"]
        assert len(result["evidence"]["testing"]) == 1
        assert result["evidence"]["testing"][0]["key"] == "test_file_ratio"

        # Metadata section
        assert result["metadata"]["total_facts"] == 2
        assert result["metadata"]["total_repos"] == 1
        assert result["metadata"]["extracted_at"] is not None

    def test_get_evidence_json_nonexistent_candidate(self, db):
        """Evidence JSON for a non-existent candidate returns empty dict."""
        result = db.get_evidence_json("nonexistent-id")
        assert result == {}


# ============================================================================
# Unanalyzed candidates
# ============================================================================

class TestUnanalyzedCandidates:

    def test_get_unanalyzed_candidates(self, db, sample_candidate, make_fact):
        """Candidates with evidence but no analysis_runs should appear."""
        cid = db.upsert_candidate(sample_candidate)
        run_id = "run-unanalyzed-001"
        fact = make_fact(cid, run_id)
        db.insert_fact(fact)

        unanalyzed = db.get_unanalyzed_candidates()
        assert len(unanalyzed) == 1
        assert unanalyzed[0]["id"] == cid

    def test_get_unanalyzed_excludes_analyzed(self, db, sample_candidate, make_fact):
        """Candidates that already have an analysis_run should NOT appear."""
        from models import AnalysisRun

        cid = db.upsert_candidate(sample_candidate)
        run_id = "run-analyzed-001"
        fact = make_fact(cid, run_id)
        db.insert_fact(fact)

        analysis = AnalysisRun(
            candidate_id=cid,
            evidence_snapshot_at="2026-04-01T00:00:00Z",
            evidence_fact_count=1,
            analysis_output_json="{}",
            model_used="claude-sonnet-4-6",
            prompt_hash="abc123",
            critic_passed=True,
        )
        db.insert_analysis_run(analysis)

        unanalyzed = db.get_unanalyzed_candidates()
        assert len(unanalyzed) == 0

    def test_get_unanalyzed_excludes_no_evidence(self, db, sample_candidate):
        """Candidates with zero evidence facts should NOT appear."""
        db.upsert_candidate(sample_candidate)

        unanalyzed = db.get_unanalyzed_candidates()
        assert len(unanalyzed) == 0


# ============================================================================
# Extraction Runs
# ============================================================================

class TestExtractionRun:

    def test_extraction_run_lifecycle(self, db, sample_candidate):
        """Create a run in 'running' state, then complete it."""
        cid = db.upsert_candidate(sample_candidate)

        run = ExtractionRun(candidate_id=cid, trigger_type="initial")
        run_id = db.create_extraction_run(run)
        assert run_id == run.id

        # Verify it's in running state
        row = db.conn.execute(
            "SELECT * FROM extraction_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["status"] == "running"
        assert row["completed_at"] is None

        # Complete it
        db.complete_extraction_run(
            run_id,
            repos_scanned=5,
            facts_extracted=42,
            duration_ms=3500,
        )

        row = db.conn.execute(
            "SELECT * FROM extraction_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["status"] == "completed"
        assert row["repos_scanned"] == 5
        assert row["facts_extracted"] == 42
        assert row["duration_ms"] == 3500
        assert row["completed_at"] is not None

    def test_extraction_run_failed(self, db, sample_candidate):
        """A failed extraction run records the error message."""
        cid = db.upsert_candidate(sample_candidate)
        run = ExtractionRun(candidate_id=cid, trigger_type="initial")
        run_id = db.create_extraction_run(run)

        db.complete_extraction_run(
            run_id,
            repos_scanned=0,
            facts_extracted=0,
            duration_ms=100,
            status="failed",
            error_message="GitHub API rate limit exceeded",
        )

        row = db.conn.execute(
            "SELECT * FROM extraction_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["status"] == "failed"
        assert row["error_message"] == "GitHub API rate limit exceeded"


# ============================================================================
# Repos
# ============================================================================

class TestUpsertRepo:

    def test_upsert_repo(self, db, sample_candidate, make_repo):
        """Inserting a new repo returns its generated ID."""
        cid = db.upsert_candidate(sample_candidate)
        repo = make_repo(cid, "run-repo-001")
        repo_id = db.upsert_repo(repo)
        assert repo_id == repo.id

        rows = db.conn.execute(
            "SELECT * FROM repos WHERE candidate_id = ?", (cid,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["full_name"] == "testuser/testrepo"
        assert rows[0]["primary_language"] == "Python"
        assert rows[0]["stars"] == 10

    def test_upsert_repo_updates_existing(self, db, sample_candidate, make_repo):
        """Re-upserting the same (candidate_id, github_repo_id) pair updates
        mutable fields and returns the original ID."""
        cid = db.upsert_candidate(sample_candidate)

        repo1 = make_repo(cid, "run-repo-001")
        original_id = db.upsert_repo(repo1)

        repo2 = make_repo(cid, "run-repo-002")
        repo2.stars = 50
        repo2.forks = 10
        repo2.description = "Updated description"
        returned_id = db.upsert_repo(repo2)

        assert returned_id == original_id

        row = db.conn.execute(
            "SELECT * FROM repos WHERE id = ?", (original_id,)
        ).fetchone()
        assert row["stars"] == 50
        assert row["forks"] == 10
        assert row["description"] == "Updated description"


# ============================================================================
# Seed Repos
# ============================================================================

class TestSeedRepos:

    def test_seed_repo_upsert_idempotent(self, db):
        """Inserting the same seed repo twice returns the same ID both times."""
        id1 = db.upsert_seed_repo("opensuitemcp/opensuitemcp", "manual")
        id2 = db.upsert_seed_repo("opensuitemcp/opensuitemcp", "manual")
        assert id1 == id2

        seeds = db.list_seed_repos()
        assert len(seeds) == 1
        assert seeds[0]["full_name"] == "opensuitemcp/opensuitemcp"

    def test_seed_repo_different_repos(self, db):
        """Distinct repos get distinct IDs."""
        id1 = db.upsert_seed_repo("owner/repo-a", "manual")
        id2 = db.upsert_seed_repo("owner/repo-b", "search:netsuite mcp")
        assert id1 != id2

        seeds = db.list_seed_repos()
        assert len(seeds) == 2
