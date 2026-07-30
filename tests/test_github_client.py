"""Tests for GitHubClient — all HTTP calls mocked via the responses library."""
from __future__ import annotations

import pytest
import responses

from sniperscope.github import GitHubClient, GitHubRateLimitError
from sniperscope import config


BASE = config.GITHUB_API_BASE


# ============================================================================
# User endpoints
# ============================================================================

class TestGetUser:

    @responses.activate
    def test_get_user_success(self, mock_github_user):
        """Fetching an existing user returns the parsed JSON profile."""
        responses.add(
            responses.GET,
            f"{BASE}/users/testuser",
            json=mock_github_user,
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        client = GitHubClient(token="fake-token")
        user = client.get_user("testuser")

        assert user is not None
        assert user["login"] == "testuser"
        assert user["id"] == 12345
        assert user["public_repos"] == 42
        assert client.requests_made == 1

    @responses.activate
    def test_get_user_not_found(self):
        """Fetching a non-existent user returns None (not an exception)."""
        responses.add(
            responses.GET,
            f"{BASE}/users/ghost",
            json={"message": "Not Found"},
            status=404,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        client = GitHubClient(token="fake-token")
        user = client.get_user("ghost")
        assert user is None


# ============================================================================
# Repo endpoints
# ============================================================================

class TestGetUserRepos:

    @responses.activate
    def test_get_user_repos_pagination(self, mock_github_repo):
        """When page 1 returns a full page and page 2 returns a partial page,
        results from both pages are combined."""
        page1 = [mock_github_repo] * 100  # full page triggers next page fetch
        page2 = [mock_github_repo] * 15   # partial page stops pagination

        responses.add(
            responses.GET,
            f"{BASE}/users/testuser/repos",
            json=page1,
            status=200,
            headers={"X-RateLimit-Remaining": "4998"},
        )
        responses.add(
            responses.GET,
            f"{BASE}/users/testuser/repos",
            json=page2,
            status=200,
            headers={"X-RateLimit-Remaining": "4997"},
        )

        client = GitHubClient(token="fake-token")
        repos = client.get_user_repos("testuser", max_repos=200)
        assert len(repos) == 115
        assert client.requests_made == 2

    @responses.activate
    def test_get_user_repos_single_page(self, mock_github_repo):
        """When the first page is partial, no second request is made."""
        page1 = [mock_github_repo] * 3

        responses.add(
            responses.GET,
            f"{BASE}/users/testuser/repos",
            json=page1,
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        client = GitHubClient(token="fake-token")
        repos = client.get_user_repos("testuser")
        assert len(repos) == 3
        assert client.requests_made == 1

    @responses.activate
    def test_get_user_repos_respects_max(self, mock_github_repo):
        """Result list is truncated to max_repos even if API returns more."""
        page1 = [mock_github_repo] * 50

        responses.add(
            responses.GET,
            f"{BASE}/users/testuser/repos",
            json=page1,
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        client = GitHubClient(token="fake-token")
        repos = client.get_user_repos("testuser", max_repos=10)
        assert len(repos) == 10


# ============================================================================
# Rate limiting
# ============================================================================

class TestRateLimitHandling:

    @responses.activate
    def test_rate_limit_handling(self):
        """A 403 response with 'rate limit' text raises GitHubRateLimitError."""
        responses.add(
            responses.GET,
            f"{BASE}/users/testuser",
            json={"message": "API rate limit exceeded"},
            status=403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"},
        )

        client = GitHubClient(token="fake-token")
        with pytest.raises(GitHubRateLimitError):
            client.get_user("testuser")

    @responses.activate
    def test_rate_limit_low_remaining_does_not_crash(self, mock_github_user):
        """When remaining is low but non-zero and response is 200, the request
        succeeds (rate limit check just logs a warning, doesn't raise)."""
        responses.add(
            responses.GET,
            f"{BASE}/users/testuser",
            json=mock_github_user,
            status=200,
            headers={
                "X-RateLimit-Remaining": "5",
                "X-RateLimit-Reset": "0",  # reset in the past → wait capped at 1s
            },
        )

        client = GitHubClient(token="fake-token")
        user = client.get_user("testuser")
        assert user is not None
        assert user["login"] == "testuser"


# ============================================================================
# Search
# ============================================================================

class TestSearchRepos:

    @responses.activate
    def test_search_repos(self, mock_github_repo):
        """Searching repos returns items from the search response."""
        responses.add(
            responses.GET,
            f"{BASE}/search/repositories",
            json={"total_count": 2, "items": [mock_github_repo, mock_github_repo]},
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        client = GitHubClient(token="fake-token")
        repos = client.search_repos("netsuite mcp", max_results=10)
        assert len(repos) == 2
        assert repos[0]["full_name"] == "testuser/testrepo"

    @responses.activate
    def test_search_repos_empty(self):
        """Search with no results returns an empty list."""
        responses.add(
            responses.GET,
            f"{BASE}/search/repositories",
            json={"total_count": 0, "items": []},
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        client = GitHubClient(token="fake-token")
        repos = client.search_repos("xyznonexistent123")
        assert repos == []


# ============================================================================
# Contributors
# ============================================================================

class TestGetRepoContributors:

    @responses.activate
    def test_get_repo_contributors(self, mock_github_contributors):
        """Contributors endpoint returns the expected list."""
        responses.add(
            responses.GET,
            f"{BASE}/repos/testuser/testrepo/contributors",
            json=mock_github_contributors,
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        client = GitHubClient(token="fake-token")
        contributors = client.get_repo_contributors("testuser/testrepo")
        assert len(contributors) == 3
        assert contributors[0]["login"] == "testuser"
        assert contributors[0]["contributions"] == 87

    @responses.activate
    def test_get_repo_contributors_not_found(self):
        """Contributors for a non-existent repo returns an empty list."""
        responses.add(
            responses.GET,
            f"{BASE}/repos/ghost/nonexistent/contributors",
            json={"message": "Not Found"},
            status=404,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        client = GitHubClient(token="fake-token")
        contributors = client.get_repo_contributors("ghost/nonexistent")
        assert contributors == []


# ============================================================================
# Request counting
# ============================================================================

class TestRequestCounting:

    @responses.activate
    def test_requests_made_increments(self, mock_github_user, mock_github_repo):
        """requests_made property tracks total API calls across methods."""
        responses.add(
            responses.GET,
            f"{BASE}/users/testuser",
            json=mock_github_user,
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )
        responses.add(
            responses.GET,
            f"{BASE}/repos/testuser/testrepo",
            json=mock_github_repo,
            status=200,
            headers={"X-RateLimit-Remaining": "4998"},
        )

        client = GitHubClient(token="fake-token")
        assert client.requests_made == 0

        client.get_user("testuser")
        assert client.requests_made == 1

        client.get_repo("testuser/testrepo")
        assert client.requests_made == 2
