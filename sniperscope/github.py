"""GitHub REST API client with rate limiting and pagination."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from sniperscope import config

logger = logging.getLogger(__name__)


class GitHubRateLimitError(Exception):
    """Raised when the GitHub API rate limit is exhausted."""


class GitHubClient:
    """Authenticated GitHub REST client with automatic rate-limit handling."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or config.GITHUB_TOKEN
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if self.token:
            self.session.headers["Authorization"] = "Bearer {}".format(self.token)
        self._requests_made = 0

    @property
    def requests_made(self) -> int:
        return self._requests_made

    def _check_rate_limit(self, response: requests.Response) -> None:
        """Pause when the remaining-request budget dips below the buffer."""
        remaining = int(response.headers.get("X-RateLimit-Remaining", 5000))
        if remaining <= config.GITHUB_RATE_LIMIT_BUFFER:
            reset_at = int(response.headers.get("X-RateLimit-Reset", 0))
            wait_seconds = max(reset_at - int(time.time()), 1)
            logger.warning("Rate limit low (%d remaining). Waiting %d seconds.",
                           remaining, wait_seconds)
            time.sleep(min(wait_seconds, config.GITHUB_RATE_LIMIT_MAX_WAIT))

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        full_url = url if url.startswith("http") else "{}{}".format(config.GITHUB_API_BASE, url)
        response = self.session.get(full_url, params=params)
        self._requests_made += 1
        self._check_rate_limit(response)

        if response.status_code == 403 and "rate limit" in response.text.lower():
            raise GitHubRateLimitError(response.text)
        if response.status_code == 404:
            return response  # caller handles 404
        response.raise_for_status()
        return response

    def _get_paginated(self, url: str, params: Optional[Dict[str, Any]] = None,
                       max_pages: int = 10) -> List[Dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results: List[Dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            params["page"] = page
            response = self._get(url, params)
            if response.status_code == 404:
                break
            data = response.json()
            if not data:
                break
            results.extend(data)
            if len(data) < params["per_page"]:
                break

        return results

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        response = self._get("/users/{}".format(username))
        return None if response.status_code == 404 else response.json()

    # ------------------------------------------------------------------
    # Repos
    # ------------------------------------------------------------------

    def get_user_repos(self, username: str,
                       max_repos: Optional[int] = None) -> List[Dict[str, Any]]:
        """All public repos owned by a user, most recently pushed first."""
        max_repos = max_repos or config.MAX_REPOS_PER_USER
        repos = self._get_paginated(
            "/users/{}/repos".format(username),
            params={"sort": "pushed", "direction": "desc", "type": "owner"},
        )
        return repos[:max_repos]

    def get_repo(self, full_name: str) -> Optional[Dict[str, Any]]:
        response = self._get("/repos/{}".format(full_name))
        return None if response.status_code == 404 else response.json()

    def get_repo_languages(self, full_name: str) -> Dict[str, int]:
        """Language breakdown (bytes) for a repo."""
        response = self._get("/repos/{}/languages".format(full_name))
        return {} if response.status_code == 404 else response.json()

    def get_repo_topics(self, full_name: str) -> List[str]:
        response = self._get("/repos/{}/topics".format(full_name))
        if response.status_code == 404:
            return []
        return response.json().get("names", [])

    def get_repo_contributors(self, full_name: str) -> List[Dict[str, Any]]:
        return self._get_paginated("/repos/{}/contributors".format(full_name))

    # ------------------------------------------------------------------
    # Commits
    # ------------------------------------------------------------------

    def get_repo_commits(self, full_name: str, since_days: Optional[int] = None,
                         author: Optional[str] = None) -> List[Dict[str, Any]]:
        """Commits for a repo within the lookback window, optionally by author."""
        since_days = since_days or config.COMMIT_LOOKBACK_DAYS
        since = (datetime.now(timezone.utc).replace(tzinfo=None)
                 - timedelta(days=since_days)).isoformat() + "Z"
        params: Dict[str, Any] = {"since": since}
        if author:
            params["author"] = author
        max_pages = max(1, config.MAX_COMMITS_PER_REPO // 100)
        commits = self._get_paginated("/repos/{}/commits".format(full_name),
                                      params=params, max_pages=max_pages)
        return commits[:config.MAX_COMMITS_PER_REPO]

    def get_commit_detail(self, full_name: str, sha: str) -> Optional[Dict[str, Any]]:
        response = self._get("/repos/{}/commits/{}".format(full_name, sha))
        return None if response.status_code == 404 else response.json()

    # ------------------------------------------------------------------
    # Contents (CI / test-file detection)
    # ------------------------------------------------------------------

    def check_path_exists(self, full_name: str, path: str) -> bool:
        response = self._get("/repos/{}/contents/{}".format(full_name, path))
        return response.status_code != 404

    def list_directory(self, full_name: str, path: str) -> List[Dict[str, Any]]:
        response = self._get("/repos/{}/contents/{}".format(full_name, path))
        if response.status_code == 404:
            return []
        data = response.json()
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Pull requests
    # ------------------------------------------------------------------

    def get_user_prs(self, username: str, state: str = "all") -> List[Dict[str, Any]]:
        """PRs authored by a user across all repos (search API)."""
        response = self._get("/search/issues", params={
            "q": "author:{} type:pr".format(username),
            "sort": "created", "order": "desc", "per_page": 100,
        })
        return response.json().get("items", [])

    def get_user_pr_reviews(self, username: str) -> List[Dict[str, Any]]:
        """PRs reviewed by a user (search API)."""
        response = self._get("/search/issues", params={
            "q": "reviewed-by:{} type:pr".format(username),
            "sort": "created", "order": "desc", "per_page": 100,
        })
        return response.json().get("items", [])

    # ------------------------------------------------------------------
    # Stars
    # ------------------------------------------------------------------

    def get_user_starred_repos(self, username: str,
                               max_results: int = 100) -> List[Dict[str, Any]]:
        return self._get_paginated(
            "/users/{}/starred".format(username),
            params={"sort": "created", "direction": "desc"},
            max_pages=max(1, max_results // 100),
        )[:max_results]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_repos(self, query: str, max_results: int = 50) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        pages = max(1, max_results // 30)
        for page in range(1, pages + 1):
            response = self._get("/search/repositories", params={
                "q": query, "sort": "updated", "per_page": 30, "page": page,
            })
            items = response.json().get("items", [])
            results.extend(items)
            if len(items) < 30:
                break
            time.sleep(config.GITHUB_SEARCH_API_SLEEP_SECONDS)  # search API: 10 req/min
        return results[:max_results]

    def search_users(self, query: str, max_results: int = 50) -> List[Dict[str, Any]]:
        response = self._get("/search/users", params={
            "q": query, "sort": "joined", "per_page": min(max_results, 100),
        })
        return response.json().get("items", [])[:max_results]
