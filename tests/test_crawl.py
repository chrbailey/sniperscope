"""Tests for crawl.py — seed discovery and contributor extraction orchestration.

Uses mocked GitHub client to avoid any real API calls.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


from sniperscope import crawl
from sniperscope.db import Database  # noqa: E402
from sniperscope.models import Candidate  # noqa: E402


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "crawl.db"))
    yield database
    database.close()


@pytest.fixture
def seeds_file(tmp_path):
    seeds = {
        "repos": ["owner1/repo1", "owner2/repo2"],
        "search_queries": ["netsuite mcp", "sap claude"],
    }
    path = tmp_path / "seeds.json"
    path.write_text(json.dumps(seeds))
    return path


# ============================================================================
# Helper functions
# ============================================================================

class TestCandidateRecentlyExtracted:
    def test_returns_false_for_new_candidate(self, db):
        c = Candidate(github_id=1, github_login="new", discovered_via="manual")
        cid = db.upsert_candidate(c)
        assert crawl._candidate_recently_extracted(db, cid) is False

    def test_returns_true_after_recent_extraction(self, db):
        from sniperscope.models import ExtractionRun
        from datetime import datetime

        c = Candidate(github_id=1, github_login="user", discovered_via="manual")
        cid = db.upsert_candidate(c)
        run = ExtractionRun(
            candidate_id=cid, trigger_type="initial",
            completed_at=datetime.utcnow().isoformat(),
            status="completed",
        )
        db.create_extraction_run(run)
        # Mark it completed
        db.complete_extraction_run(run.id, 1, 5, 100)

        assert crawl._candidate_recently_extracted(db, cid) is True


class TestRepoMatchesErpAi:
    def test_matches_on_erp_name(self):
        assert crawl._repo_matches_erp_ai("netsuite-mcp-tools", None, None) is True

    def test_matches_on_ai_keyword_description(self):
        assert crawl._repo_matches_erp_ai(
            "some-repo",
            "An AI tool for enterprise systems",
            None,
        ) is True

    def test_matches_on_topics(self):
        assert crawl._repo_matches_erp_ai(
            "generic",
            None,
            ["netsuite", "automation"],
        ) is True

    def test_no_match_on_unrelated(self):
        assert crawl._repo_matches_erp_ai(
            "pytorch-tutorial",
            "Deep learning tutorial for computer vision",
            ["ml", "tutorial"],
        ) is False


# ============================================================================
# crawl_seeds orchestration
# ============================================================================

class TestCrawlSeeds:
    def test_crawl_registers_seed_repos(self, db, seeds_file):
        mock_client = MagicMock()
        mock_client.get_repo_contributors.return_value = []

        crawl.crawl_seeds(db, mock_client, str(seeds_file))

        seeds = db.list_seed_repos()
        assert len(seeds) == 2
        assert {s["full_name"] for s in seeds} == {"owner1/repo1", "owner2/repo2"}

    def test_crawl_creates_new_candidates(self, db, seeds_file):
        mock_client = MagicMock()
        mock_client.get_repo_contributors.return_value = [
            {"id": 1, "login": "alice", "contributions": 10},
        ]
        mock_client.get_user.return_value = {
            "id": 1, "login": "alice", "name": "Alice",
            "bio": None, "company": None, "location": None,
            "avatar_url": None, "public_repos": 5, "followers": 3,
            "following": 0, "created_at": "2020-01-01",
        }

        with patch("sniperscope.crawl._try_extract", return_value=False):
            result = crawl.crawl_seeds(db, mock_client, str(seeds_file))

        candidates = db.list_candidates()
        assert len(candidates) > 0
        assert any(c["github_login"] == "alice" for c in candidates)
        assert result["new_candidates"] >= 1

    def test_crawl_skips_duplicates(self, db, seeds_file):
        mock_client = MagicMock()
        mock_client.get_repo_contributors.return_value = [
            {"id": 1, "login": "alice", "contributions": 10},
        ]
        mock_client.get_user.return_value = {
            "id": 1, "login": "alice", "name": "Alice",
            "bio": None, "company": None, "location": None,
            "avatar_url": None, "public_repos": 5, "followers": 3,
            "following": 0, "created_at": "2020-01-01",
        }

        with patch("sniperscope.crawl._try_extract", return_value=False):
            # Run twice — second should not duplicate the candidate
            crawl.crawl_seeds(db, mock_client, str(seeds_file))
            crawl.crawl_seeds(db, mock_client, str(seeds_file))

        alice_count = db.conn.execute(
            "SELECT COUNT(*) as c FROM candidates WHERE github_login = 'alice'"
        ).fetchone()["c"]
        assert alice_count == 1

    def test_crawl_handles_404_gracefully(self, db, seeds_file):
        """A 404 on contributors shouldn't crash the whole crawl."""
        mock_client = MagicMock()
        mock_client.get_repo_contributors.return_value = []  # simulates 404

        result = crawl.crawl_seeds(db, mock_client, str(seeds_file))
        assert result["repos_crawled"] == 2
        assert result["new_candidates"] == 0
