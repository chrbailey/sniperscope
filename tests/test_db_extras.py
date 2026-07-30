"""Tests for db.py features not covered by test_db.py:
diff_extractions (interview verification) and outcomes (training sidecar).
"""
from __future__ import annotations


import pytest


from sniperscope.db import Database  # noqa: E402
from sniperscope.models import Candidate, EvidenceFact, ExtractionRun, Outcome  # noqa: E402


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    yield database
    database.close()


@pytest.fixture
def candidate_id(db):
    c = Candidate(github_id=100, github_login="diffuser", discovered_via="manual")
    return db.upsert_candidate(c)


# ============================================================================
# diff_extractions — the interview verification feature
# ============================================================================

class TestDiffExtractions:
    def _make_run_with_facts(self, db, candidate_id, facts_data):
        """Helper: create an extraction run and insert a list of (category, key, value, source) tuples."""
        run = ExtractionRun(candidate_id=candidate_id, trigger_type="initial")
        run_id = db.create_extraction_run(run)
        facts = [
            EvidenceFact(
                candidate_id=candidate_id,
                category=cat,
                fact_key=key,
                fact_value=val,
                fact_type="string",
                source=src,
                extraction_run_id=run_id,
            )
            for cat, key, val, src in facts_data
        ]
        db.insert_facts_batch(facts)
        db.complete_extraction_run(run_id, 1, len(facts), 100)
        return run_id

    def test_diff_empty_when_no_changes(self, db, candidate_id):
        """Same facts in both runs should produce empty added/removed/changed."""
        old = self._make_run_with_facts(db, candidate_id, [
            ("language", "primary", "Python", "github:user:diffuser"),
            ("testing", "test_ratio", "0.5", "github:repo:diffuser/x"),
        ])
        new = self._make_run_with_facts(db, candidate_id, [
            ("language", "primary", "Python", "github:user:diffuser"),
            ("testing", "test_ratio", "0.5", "github:repo:diffuser/x"),
        ])

        result = db.diff_extractions(candidate_id, old, new)
        assert result["added"] == []
        assert result["removed"] == []
        assert result["changed"] == []

    def test_diff_detects_added_facts(self, db, candidate_id):
        """Facts only in the new run show up under 'added'."""
        old = self._make_run_with_facts(db, candidate_id, [
            ("language", "primary", "Python", "github:user:diffuser"),
        ])
        new = self._make_run_with_facts(db, candidate_id, [
            ("language", "primary", "Python", "github:user:diffuser"),
            ("repo_metadata", "total_stars", "42", "github:user:diffuser"),
        ])

        result = db.diff_extractions(candidate_id, old, new)
        assert len(result["added"]) == 1
        assert result["added"][0]["key"] == "total_stars"
        assert result["added"][0]["value"] == "42"

    def test_diff_detects_removed_facts(self, db, candidate_id):
        """Facts only in the old run show up under 'removed'."""
        old = self._make_run_with_facts(db, candidate_id, [
            ("language", "primary", "Python", "github:user:diffuser"),
            ("domain_keyword", "domain:netsuite:commit_mentions", "5",
             "github:repo:diffuser/x"),
        ])
        new = self._make_run_with_facts(db, candidate_id, [
            ("language", "primary", "Python", "github:user:diffuser"),
        ])

        result = db.diff_extractions(candidate_id, old, new)
        assert len(result["removed"]) == 1
        assert result["removed"][0]["key"] == "domain:netsuite:commit_mentions"

    def test_diff_detects_changed_values(self, db, candidate_id):
        """Same (category, key, source) but different value shows up under 'changed'."""
        old = self._make_run_with_facts(db, candidate_id, [
            ("repo_metadata", "total_stars", "10", "github:user:diffuser"),
        ])
        new = self._make_run_with_facts(db, candidate_id, [
            ("repo_metadata", "total_stars", "42", "github:user:diffuser"),
        ])

        result = db.diff_extractions(candidate_id, old, new)
        assert len(result["changed"]) == 1
        assert result["changed"][0]["old_value"] == "10"
        assert result["changed"][0]["new_value"] == "42"

    def test_diff_summary_counts(self, db, candidate_id):
        old = self._make_run_with_facts(db, candidate_id, [
            ("a", "k1", "v1", "src1"),
            ("a", "k2", "v2", "src2"),
            ("a", "k3", "v3", "src3"),
        ])
        new = self._make_run_with_facts(db, candidate_id, [
            ("a", "k1", "v1", "src1"),       # unchanged
            ("a", "k2", "different", "src2"), # changed
            ("a", "k4", "v4", "src4"),       # added; k3 removed
        ])

        result = db.diff_extractions(candidate_id, old, new)
        assert result["summary"]["facts_added"] == 1
        assert result["summary"]["facts_removed"] == 1
        assert result["summary"]["facts_changed"] == 1
        assert result["summary"]["old_total"] == 3
        assert result["summary"]["new_total"] == 3


# ============================================================================
# Outcomes (training sidecar)
# ============================================================================

class TestOutcomes:
    def test_insert_outcome(self, db, candidate_id):
        outcome = Outcome(
            candidate_id=candidate_id,
            decision="contacted",
            role="partner",
            quality_rating="strong",
            notes="responded within 24h",
        )
        outcome_id = db.insert_outcome(outcome)
        assert outcome_id

        row = db.conn.execute(
            "SELECT * FROM outcomes WHERE id = ?", (outcome_id,)
        ).fetchone()
        assert row["decision"] == "contacted"
        assert row["quality_rating"] == "strong"

    def test_multiple_outcomes_per_candidate(self, db, candidate_id):
        """A candidate can accumulate multiple outcomes over time."""
        db.insert_outcome(Outcome(candidate_id=candidate_id, decision="contacted"))
        db.insert_outcome(Outcome(candidate_id=candidate_id, decision="responded"))
        db.insert_outcome(Outcome(candidate_id=candidate_id, decision="met"))

        count = db.conn.execute(
            "SELECT COUNT(*) as c FROM outcomes WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()["c"]
        assert count == 3
