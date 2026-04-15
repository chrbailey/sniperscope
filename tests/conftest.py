"""Shared fixtures for Sniperscope test suite."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import pytest

from db import Database
from models import Candidate, EvidenceFact, ExtractionRun, Repo


# ============================================================================
# Database fixtures
# ============================================================================

@pytest.fixture
def db(tmp_path):
    """Provide a Database instance backed by a temp SQLite file."""
    db_path = str(tmp_path / "test_sniperscope.db")
    database = Database(db_path=db_path)
    yield database
    database.close()


@pytest.fixture
def sample_candidate():
    """A minimal valid Candidate for testing."""
    return Candidate(
        github_id=12345,
        github_login="testuser",
        display_name="Test User",
        email="test@example.com",
        bio="I build ERP integrations",
        company="Acme Corp",
        location="San Francisco, CA",
        avatar_url="https://avatars.githubusercontent.com/u/12345",
        public_repos=42,
        followers=100,
        following=50,
        github_created_at="2015-03-14T00:00:00Z",
        discovered_via="seed:opensuitemcp/opensuitemcp",
    )


@pytest.fixture
def sample_extraction_run():
    """A minimal ExtractionRun for testing."""
    return ExtractionRun(
        candidate_id="placeholder",  # caller should override
        trigger_type="initial",
    )


@pytest.fixture
def make_fact():
    """Factory fixture — returns a function that creates EvidenceFact instances."""
    def _make(candidate_id: str, run_id: str, category: str = "testing",
              key: str = "test_file_ratio", value: str = "0.34",
              fact_type: str = "number",
              source: str = "github:repo:testuser/testrepo") -> EvidenceFact:
        return EvidenceFact(
            candidate_id=candidate_id,
            category=category,
            fact_key=key,
            fact_value=value,
            fact_type=fact_type,
            source=source,
            extraction_run_id=run_id,
        )
    return _make


@pytest.fixture
def make_repo():
    """Factory fixture — returns a function that creates Repo instances."""
    def _make(candidate_id: str, run_id: str, github_repo_id: int = 99999,
              full_name: str = "testuser/testrepo") -> Repo:
        return Repo(
            candidate_id=candidate_id,
            github_repo_id=github_repo_id,
            full_name=full_name,
            description="A test repository",
            primary_language="Python",
            languages_json=json.dumps({"Python": 50000, "Shell": 1200}),
            stars=10,
            forks=2,
            is_fork=False,
            is_archived=False,
            pushed_at="2026-04-01T12:00:00Z",
            topics_json=json.dumps(["netsuite", "mcp"]),
            has_ci=True,
            has_tests=True,
            test_file_count=8,
            source_file_count=25,
            license="MIT",
            default_branch="main",
            extraction_run_id=run_id,
        )
    return _make


# ============================================================================
# Mock GitHub API responses
# ============================================================================

@pytest.fixture
def mock_github_user():
    """A realistic GitHub user API response."""
    return {
        "login": "testuser",
        "id": 12345,
        "avatar_url": "https://avatars.githubusercontent.com/u/12345",
        "name": "Test User",
        "company": "Acme Corp",
        "blog": "https://testuser.dev",
        "location": "San Francisco, CA",
        "email": "test@example.com",
        "bio": "I build ERP integrations",
        "public_repos": 42,
        "followers": 100,
        "following": 50,
        "created_at": "2015-03-14T00:00:00Z",
    }


@pytest.fixture
def mock_github_repo():
    """A realistic GitHub repo API response."""
    return {
        "id": 99999,
        "name": "testrepo",
        "full_name": "testuser/testrepo",
        "owner": {"login": "testuser", "id": 12345},
        "description": "A test repository",
        "fork": False,
        "language": "Python",
        "stargazers_count": 10,
        "forks_count": 2,
        "archived": False,
        "created_at": "2024-01-15T00:00:00Z",
        "pushed_at": "2026-04-01T12:00:00Z",
        "topics": ["netsuite", "mcp"],
        "license": {"spdx_id": "MIT"},
        "default_branch": "main",
    }


@pytest.fixture
def mock_github_commit():
    """A realistic GitHub commit API response."""
    return {
        "sha": "abc123def456",
        "commit": {
            "author": {
                "name": "Test User",
                "email": "test@example.com",
                "date": "2026-04-01T10:30:00Z",
            },
            "message": "feat: add SuiteScript integration tests",
        },
        "author": {"login": "testuser", "id": 12345},
        "stats": {"additions": 120, "deletions": 30, "total": 150},
        "files": [
            {"filename": "tests/test_suitescript.py", "additions": 100, "deletions": 0},
            {"filename": "src/suitescript.py", "additions": 20, "deletions": 30},
        ],
    }


@pytest.fixture
def mock_github_contributors():
    """A realistic GitHub contributors API response."""
    return [
        {"login": "testuser", "id": 12345, "contributions": 87},
        {"login": "collaborator1", "id": 67890, "contributions": 34},
        {"login": "collaborator2", "id": 11111, "contributions": 12},
    ]
