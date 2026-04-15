"""Tests for the extraction engine — deterministic evidence extraction."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from extract import (
    _fact,
    _is_test_file,
    _is_source_file,
    extract_language_facts,
    extract_testing_facts,
    extract_commit_facts,
    extract_ci_facts,
    extract_domain_keyword_facts,
    extract_temporal_facts,
    extract_collaboration_facts,
    extract_repo_metadata_facts,
    fetch_and_upsert_candidate,
    fetch_active_repos,
    fetch_commits_for_repos,
    extract_user,
    CONVENTIONAL_COMMIT_RE,
)
from models import EvidenceFact


# ============================================================================
# Helpers
# ============================================================================

def _make_repo(
    full_name: str = "testuser/testrepo",
    language: str = "Python",
    fork: bool = False,
    archived: bool = False,
    pushed_at: str = "2026-04-01T12:00:00Z",
    stars: int = 5,
    forks: int = 1,
    description: str = "A test repo",
    topics: Optional[List[str]] = None,
    created_at: str = "2024-01-15T00:00:00Z",
) -> Dict[str, Any]:
    """Build a mock GitHub repo API response."""
    return {
        "id": hash(full_name) % 10**8,
        "name": full_name.split("/")[-1],
        "full_name": full_name,
        "owner": {"login": full_name.split("/")[0], "id": 12345},
        "description": description,
        "fork": fork,
        "language": language,
        "stargazers_count": stars,
        "forks_count": forks,
        "archived": archived,
        "created_at": created_at,
        "pushed_at": pushed_at,
        "topics": topics or [],
        "license": {"spdx_id": "MIT"},
        "default_branch": "main",
    }


def _make_commit(
    message: str = "fix: resolve issue",
    date: str = "2026-03-15T10:00:00Z",
    author_login: str = "testuser",
    files: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Build a mock GitHub commit API response."""
    commit = {
        "sha": "abc123",
        "commit": {
            "author": {
                "name": "Test User",
                "email": "test@example.com",
                "date": date,
            },
            "message": message,
        },
        "author": {"login": author_login, "id": 12345},
    }
    if files is not None:
        commit["files"] = files
    return commit


# ============================================================================
# _is_test_file
# ============================================================================

class TestIsTestFile:

    def test_pytest_pattern(self):
        assert _is_test_file("test_something.py") is True

    def test_jest_pattern(self):
        assert _is_test_file("component.test.js") is True
        assert _is_test_file("component.test.tsx") is True

    def test_spec_pattern(self):
        assert _is_test_file("service.spec.ts") is True
        assert _is_test_file("helper.spec.js") is True

    def test_go_test_pattern(self):
        assert _is_test_file("handler_test.go") is True

    def test_source_file_not_test(self):
        assert _is_test_file("main.py") is False
        assert _is_test_file("index.js") is False
        assert _is_test_file("utils.ts") is False

    def test_case_insensitive(self):
        assert _is_test_file("Test_Something.py") is True
        assert _is_test_file("Component.Test.JS") is True


# ============================================================================
# _is_source_file
# ============================================================================

class TestIsSourceFile:

    def test_python(self):
        assert _is_source_file("main.py") is True

    def test_javascript(self):
        assert _is_source_file("index.js") is True

    def test_typescript(self):
        assert _is_source_file("handler.ts") is True
        assert _is_source_file("component.tsx") is True

    def test_non_source(self):
        assert _is_source_file("README.md") is False
        assert _is_source_file("package.json") is False
        assert _is_source_file("image.png") is False


# ============================================================================
# Conventional commit regex
# ============================================================================

class TestConventionalCommitRegex:

    def test_feat(self):
        assert CONVENTIONAL_COMMIT_RE.match("feat: add new feature") is not None

    def test_fix_with_scope(self):
        assert CONVENTIONAL_COMMIT_RE.match("fix(auth): resolve login bug") is not None

    def test_refactor(self):
        assert CONVENTIONAL_COMMIT_RE.match("refactor: extract helper") is not None

    def test_non_conventional(self):
        assert CONVENTIONAL_COMMIT_RE.match("Add new feature") is None
        assert CONVENTIONAL_COMMIT_RE.match("Fixed the bug") is None
        assert CONVENTIONAL_COMMIT_RE.match("WIP stuff") is None


# ============================================================================
# _fact helper
# ============================================================================

class TestFactBuilder:

    def test_creates_valid_evidence_fact(self):
        fact = _fact("cid-1", "run-1", "testing", "test_file_ratio", 0.34, "number", "github:repo:x/y")
        assert isinstance(fact, EvidenceFact)
        assert fact.candidate_id == "cid-1"
        assert fact.extraction_run_id == "run-1"
        assert fact.category == "testing"
        assert fact.fact_key == "test_file_ratio"
        assert fact.fact_value == "0.34"
        assert fact.fact_type == "number"
        assert fact.source == "github:repo:x/y"

    def test_value_converted_to_string(self):
        fact = _fact("cid-1", "run-1", "language", "diversity", 7, "number", "src")
        assert fact.fact_value == "7"
        assert isinstance(fact.fact_value, str)


# ============================================================================
# extract_language_facts
# ============================================================================

class TestExtractLanguageFacts:

    def test_extracts_primary_language(self):
        repos = [_make_repo("testuser/repo1", language="Python")]
        client = MagicMock()
        client.get_repo_languages.return_value = {"Python": 50000, "Shell": 1200}

        facts, per_repo = extract_language_facts("cid", "run", repos, client)

        primary_facts = [f for f in facts if f.fact_key == "primary_language"]
        assert len(primary_facts) == 1
        assert primary_facts[0].fact_value == "Python"
        assert primary_facts[0].source == "github:repo:testuser/repo1"

    def test_calculates_total_bytes(self):
        repos = [
            _make_repo("testuser/repo1", language="Python"),
            _make_repo("testuser/repo2", language="TypeScript"),
        ]
        client = MagicMock()
        client.get_repo_languages.side_effect = [
            {"Python": 50000, "Shell": 1200},
            {"TypeScript": 30000, "Python": 5000},
        ]

        facts, _ = extract_language_facts("cid", "run", repos, client)

        python_bytes = [f for f in facts if f.fact_key == "total_bytes:Python"]
        assert len(python_bytes) == 1
        assert python_bytes[0].fact_value == "55000"

    def test_language_diversity(self):
        repos = [_make_repo("testuser/repo1")]
        client = MagicMock()
        client.get_repo_languages.return_value = {"Python": 50000, "Shell": 1200, "SQL": 800}

        facts, _ = extract_language_facts("cid", "run", repos, client)

        diversity = [f for f in facts if f.fact_key == "language_diversity"]
        assert len(diversity) == 1
        assert diversity[0].fact_value == "3"

    def test_handles_api_error(self):
        repos = [_make_repo("testuser/repo1")]
        client = MagicMock()
        client.get_repo_languages.side_effect = Exception("API error")

        facts, per_repo = extract_language_facts("cid", "run", repos, client)
        # Should still produce primary_language from repo data
        primary = [f for f in facts if f.fact_key == "primary_language"]
        assert len(primary) == 1


# ============================================================================
# extract_commit_facts
# ============================================================================

class TestExtractCommitFacts:

    def test_total_commits(self):
        commits_by_repo = {
            "testuser/repo1": [_make_commit() for _ in range(10)],
        }
        facts = extract_commit_facts("cid", "run", commits_by_repo)

        total = [f for f in facts if f.fact_key == "total_commits_in_lookback"]
        assert len(total) == 1
        assert total[0].fact_value == "10"

    def test_conventional_commit_detection(self):
        commits_by_repo = {
            "testuser/repo1": [
                _make_commit("feat: add feature"),
                _make_commit("fix: resolve bug"),
                _make_commit("Updated readme"),
                _make_commit("chore: cleanup"),
            ],
        }
        facts = extract_commit_facts("cid", "run", commits_by_repo)

        conv_count = [f for f in facts if f.fact_key == "conventional_commit_count"]
        assert conv_count[0].fact_value == "3"

        conv_ratio = [f for f in facts if f.fact_key == "conventional_commit_ratio"]
        assert conv_ratio[0].fact_value == "0.75"

    def test_coauthor_detection(self):
        commits_by_repo = {
            "testuser/repo1": [
                _make_commit("feat: add feature\n\nCo-authored-by: Claude <noreply@anthropic.com>"),
                _make_commit("fix: resolve bug"),
                _make_commit("docs: update readme\n\nCo-Authored-By: Copilot <noreply@github.com>"),
            ],
        }
        facts = extract_commit_facts("cid", "run", commits_by_repo)

        coauthor_count = [f for f in facts if f.fact_key == "coauthor_commit_count"]
        assert coauthor_count[0].fact_value == "2"

        coauthor_ratio = [f for f in facts if f.fact_key == "coauthor_commit_ratio"]
        assert float(coauthor_ratio[0].fact_value) == pytest.approx(0.6667, abs=0.001)

    def test_message_length_stats(self):
        commits_by_repo = {
            "testuser/repo1": [
                _make_commit("fix: short"),         # 10 chars
                _make_commit("feat: a longer message here"),  # 27 chars
            ],
        }
        facts = extract_commit_facts("cid", "run", commits_by_repo)

        avg = [f for f in facts if f.fact_key == "commit_message_length_avg"]
        assert len(avg) == 1
        # (10 + 27) / 2 = 18.5
        assert float(avg[0].fact_value) == pytest.approx(18.5, abs=0.1)

    def test_no_commits_returns_total_only(self):
        commits_by_repo = {"testuser/repo1": []}
        facts = extract_commit_facts("cid", "run", commits_by_repo)

        total = [f for f in facts if f.fact_key == "total_commits_in_lookback"]
        assert total[0].fact_value == "0"
        # No other facts should be generated when there are 0 commits
        assert len(facts) == 1


# ============================================================================
# extract_ci_facts
# ============================================================================

class TestExtractCiFacts:

    def test_detects_ci_workflows(self):
        repos = [_make_repo("testuser/repo1")]
        client = MagicMock()
        client.check_path_exists.return_value = True
        client.list_directory.return_value = [
            {"type": "file", "name": "ci.yml"},
            {"type": "file", "name": "deploy.yaml"},
            {"type": "file", "name": "README.md"},
        ]

        facts, ci_by_repo = extract_ci_facts("cid", "run", repos, client)

        has_ci = [f for f in facts if f.fact_key == "has_ci" and f.source.endswith("repo1")]
        assert has_ci[0].fact_value == "true"

        workflow_count = [f for f in facts if f.fact_key == "workflow_file_count" and f.source.endswith("repo1")]
        assert workflow_count[0].fact_value == "2"  # ci.yml + deploy.yaml, not README.md

        assert ci_by_repo["testuser/repo1"] is True

    def test_no_ci_detected(self):
        repos = [_make_repo("testuser/repo1")]
        client = MagicMock()
        client.check_path_exists.return_value = False

        facts, ci_by_repo = extract_ci_facts("cid", "run", repos, client)

        has_ci = [f for f in facts if f.fact_key == "has_ci" and f.source.endswith("repo1")]
        assert has_ci[0].fact_value == "false"
        assert ci_by_repo["testuser/repo1"] is False

    def test_aggregate_ci_facts(self):
        repos = [
            _make_repo("testuser/repo1"),
            _make_repo("testuser/repo2"),
        ]
        client = MagicMock()
        client.check_path_exists.side_effect = [True, False]
        client.list_directory.return_value = [{"type": "file", "name": "ci.yml"}]

        facts, _ = extract_ci_facts("cid", "run", repos, client)

        repos_with_ci = [f for f in facts if f.fact_key == "repos_with_ci"]
        assert repos_with_ci[0].fact_value == "1"

        has_any = [f for f in facts if f.fact_key == "has_any_ci"]
        assert has_any[0].fact_value == "true"


# ============================================================================
# extract_domain_keyword_facts
# ============================================================================

class TestExtractDomainKeywordFacts:

    def test_detects_keywords_in_description(self):
        repos = [_make_repo("testuser/netsuite-mcp", description="A NetSuite MCP server integration")]
        commits_by_repo = {"testuser/netsuite-mcp": []}

        facts = extract_domain_keyword_facts("cid", "run", repos, commits_by_repo)

        netsuite_repos = [f for f in facts if f.fact_key == "domain:netsuite:repo_mentions"]
        assert int(netsuite_repos[0].fact_value) >= 1

        mcp_repos = [f for f in facts if f.fact_key == "domain:mcp:repo_mentions"]
        assert int(mcp_repos[0].fact_value) >= 1

    def test_detects_keywords_in_commits(self):
        repos = [_make_repo("testuser/repo1", description="")]
        commits_by_repo = {
            "testuser/repo1": [
                _make_commit("feat: add Claude API integration"),
                _make_commit("fix: update SuiteScript handler"),
                _make_commit("docs: unrelated change"),
            ],
        }

        facts = extract_domain_keyword_facts("cid", "run", repos, commits_by_repo)

        anthropic_commits = [f for f in facts if f.fact_key == "domain:anthropic:commit_mentions"]
        assert int(anthropic_commits[0].fact_value) >= 1

        netsuite_commits = [f for f in facts if f.fact_key == "domain:netsuite:commit_mentions"]
        assert int(netsuite_commits[0].fact_value) >= 1

    def test_zero_mentions_still_recorded(self):
        repos = [_make_repo("testuser/repo1", description="A web app")]
        commits_by_repo = {"testuser/repo1": [_make_commit("fix: css layout")]}

        facts = extract_domain_keyword_facts("cid", "run", repos, commits_by_repo)

        # Every domain should still have repo_mentions and commit_mentions entries
        oracle_repos = [f for f in facts if f.fact_key == "domain:oracle:repo_mentions"]
        assert len(oracle_repos) == 1
        assert oracle_repos[0].fact_value == "0"


# ============================================================================
# extract_temporal_facts
# ============================================================================

class TestExtractTemporalFacts:

    def test_first_and_most_recent(self):
        repos = [
            _make_repo("testuser/old-repo", created_at="2020-01-01T00:00:00Z", pushed_at="2026-03-01T00:00:00Z"),
            _make_repo("testuser/new-repo", created_at="2025-06-01T00:00:00Z", pushed_at="2026-04-01T00:00:00Z"),
        ]
        commits_by_repo = {}

        facts = extract_temporal_facts("cid", "run", repos, commits_by_repo, "2015-03-14T00:00:00Z")

        first = [f for f in facts if f.fact_key == "first_repo_created_at"]
        assert first[0].fact_value == "2020-01-01T00:00:00Z"

        most_recent = [f for f in facts if f.fact_key == "most_recent_push_at"]
        assert most_recent[0].fact_value == "2026-04-01T00:00:00Z"

    def test_active_months(self):
        repos = [_make_repo("testuser/repo1")]
        commits_by_repo = {
            "testuser/repo1": [
                _make_commit(date="2026-01-15T10:00:00Z"),
                _make_commit(date="2026-01-20T10:00:00Z"),
                _make_commit(date="2026-03-01T10:00:00Z"),
            ],
        }

        facts = extract_temporal_facts("cid", "run", repos, commits_by_repo, None)

        active = [f for f in facts if f.fact_key == "active_months_count"]
        assert active[0].fact_value == "2"  # Jan and Mar

    def test_longest_gap(self):
        repos = [_make_repo("testuser/repo1")]
        commits_by_repo = {
            "testuser/repo1": [
                _make_commit(date="2026-01-01T10:00:00Z"),
                _make_commit(date="2026-01-05T10:00:00Z"),  # 4 day gap
                _make_commit(date="2026-03-01T10:00:00Z"),  # 55 day gap
            ],
        }

        facts = extract_temporal_facts("cid", "run", repos, commits_by_repo, None)

        gap = [f for f in facts if f.fact_key == "longest_commit_gap_days"]
        assert int(gap[0].fact_value) == 55

    def test_account_age(self):
        repos = [_make_repo("testuser/repo1")]
        facts = extract_temporal_facts("cid", "run", repos, {}, "2015-03-14T00:00:00Z")

        age = [f for f in facts if f.fact_key == "account_age_days"]
        assert len(age) == 1
        assert int(age[0].fact_value) > 3000  # account created in 2015


# ============================================================================
# extract_repo_metadata_facts
# ============================================================================

class TestExtractRepoMetadataFacts:

    def test_totals(self):
        repos = [
            _make_repo("testuser/repo1", stars=10, forks=2),
            _make_repo("testuser/repo2", stars=5, forks=1),
        ]
        facts = extract_repo_metadata_facts("cid", "run", repos)

        total_repos = [f for f in facts if f.fact_key == "total_public_repos"]
        assert total_repos[0].fact_value == "2"

        total_stars = [f for f in facts if f.fact_key == "total_stars_received"]
        assert total_stars[0].fact_value == "15"

        total_forks = [f for f in facts if f.fact_key == "total_forks_received"]
        assert total_forks[0].fact_value == "3"

    def test_topics_aggregated(self):
        repos = [
            _make_repo("testuser/repo1", topics=["netsuite", "mcp"]),
            _make_repo("testuser/repo2", topics=["mcp", "typescript"]),
        ]
        facts = extract_repo_metadata_facts("cid", "run", repos)

        topics = [f for f in facts if f.fact_key == "topics_used"]
        assert len(topics) == 1
        topics_list = json.loads(topics[0].fact_value)
        assert set(topics_list) == {"netsuite", "mcp", "typescript"}


# ============================================================================
# extract_collaboration_facts
# ============================================================================

class TestExtractCollaborationFacts:

    def test_pr_counts(self):
        repos = [_make_repo("testuser/repo1")]
        client = MagicMock()
        client.get_user_prs.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
        client.get_user_pr_reviews.return_value = [{"id": 4}]
        client.get_repo_contributors.return_value = [
            {"login": "testuser"}, {"login": "other"}
        ]

        facts = extract_collaboration_facts("cid", "run", "testuser", repos, client)

        prs = [f for f in facts if f.fact_key == "total_prs_opened"]
        assert prs[0].fact_value == "3"

        reviews = [f for f in facts if f.fact_key == "total_pr_reviews_given"]
        assert reviews[0].fact_value == "1"

    def test_solo_repo_detection(self):
        repos = [
            _make_repo("testuser/repo1"),
            _make_repo("testuser/repo2"),
        ]
        client = MagicMock()
        client.get_user_prs.return_value = []
        client.get_user_pr_reviews.return_value = []
        # repo1 has 1 contributor (solo), repo2 has 3
        client.get_repo_contributors.side_effect = [
            [{"login": "testuser"}],
            [{"login": "testuser"}, {"login": "a"}, {"login": "b"}],
        ]

        facts = extract_collaboration_facts("cid", "run", "testuser", repos, client)

        solo_count = [f for f in facts if f.fact_key == "solo_repo_count"]
        assert solo_count[0].fact_value == "1"

        solo_ratio = [f for f in facts if f.fact_key == "solo_repo_ratio"]
        assert solo_ratio[0].fact_value == "0.5"


# ============================================================================
# fetch_active_repos
# ============================================================================

class TestFetchActiveRepos:

    def test_filters_forks(self):
        client = MagicMock()
        client.get_user_repos.return_value = [
            _make_repo("testuser/original", fork=False),
            _make_repo("testuser/forked", fork=True),
        ]

        repos = fetch_active_repos("testuser", client)
        assert len(repos) == 1
        assert repos[0]["full_name"] == "testuser/original"

    def test_filters_archived(self):
        client = MagicMock()
        client.get_user_repos.return_value = [
            _make_repo("testuser/active", archived=False),
            _make_repo("testuser/archived", archived=True),
        ]

        repos = fetch_active_repos("testuser", client)
        assert len(repos) == 1
        assert repos[0]["full_name"] == "testuser/active"

    def test_filters_old_repos(self):
        client = MagicMock()
        client.get_user_repos.return_value = [
            _make_repo("testuser/recent", pushed_at="2026-04-01T00:00:00Z"),
            _make_repo("testuser/stale", pushed_at="2020-01-01T00:00:00Z"),
        ]

        repos = fetch_active_repos("testuser", client)
        assert len(repos) == 1
        assert repos[0]["full_name"] == "testuser/recent"


# ============================================================================
# extract_user (integration — mocked GitHub)
# ============================================================================

class TestExtractUser:

    def test_user_not_found(self, db):
        """Extraction for a non-existent user should return a failed run."""
        client = MagicMock()
        client.get_user.return_value = None

        run = extract_user("ghost-user", "manual", db, client)

        assert run.status == "failed"
        assert "not found" in run.error_message.lower()

    def test_no_active_repos(self, db):
        """User with no active repos should complete with 0 facts."""
        client = MagicMock()
        client.get_user.return_value = {
            "id": 99999, "login": "emptyuser", "name": "Empty User",
            "email": None, "bio": None, "company": None,
            "location": None, "avatar_url": None,
            "public_repos": 0, "followers": 0, "following": 0,
            "created_at": "2020-01-01T00:00:00Z",
        }
        client.get_user_repos.return_value = []

        run = extract_user("emptyuser", "manual", db, client)

        assert run.status == "completed"
        assert run.repos_scanned == 0
        assert run.facts_extracted == 0

    def test_full_extraction(self, db, mock_github_user, mock_github_repo, mock_github_commit):
        """Full extraction with one repo should produce facts across all categories."""
        client = MagicMock()
        client.get_user.return_value = mock_github_user
        client.get_user_repos.return_value = [mock_github_repo]
        client.get_repo_languages.return_value = {"Python": 50000, "Shell": 1200}
        client.get_repo_commits.return_value = [mock_github_commit]
        client.check_path_exists.return_value = True
        client.list_directory.side_effect = [
            # CI workflows
            [{"type": "file", "name": "ci.yml"}],
            # tests/ directory
            [{"type": "file", "name": "test_main.py"}],
            # root directory listing
            [
                {"type": "file", "name": "main.py"},
                {"type": "file", "name": "utils.py"},
                {"type": "dir", "name": "src"},
            ],
            # src/ subdirectory
            [{"type": "file", "name": "handler.py"}],
        ]
        client.get_user_prs.return_value = [{"id": 1}]
        client.get_user_pr_reviews.return_value = []
        client.get_repo_contributors.return_value = [
            {"login": "testuser"}, {"login": "other"}
        ]
        client.requests_made = 20

        run = extract_user("testuser", "manual", db, client)

        assert run.status == "completed"
        assert run.repos_scanned == 1
        assert run.facts_extracted > 0
        assert run.duration_ms > 0

        # Verify facts were written to DB
        candidate = db.get_candidate_by_login("testuser")
        assert candidate is not None

        facts = db.get_facts_for_candidate(candidate["id"])
        categories = {f["category"] for f in facts}

        # All expected categories should be present
        assert "language" in categories
        assert "testing" in categories
        assert "commit_pattern" in categories
        assert "ci_cd" in categories
        assert "domain_keyword" in categories
        assert "temporal" in categories
        assert "collaboration" in categories
        assert "repo_metadata" in categories
