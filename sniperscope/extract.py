"""Deterministic evidence extractor — GitHub API to evidence facts.

No LLM, no judgment, no scoring. Pure mechanical fact extraction.
Every observable signal is recorded. The analysis phase decides what matters.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sniperscope import config
from sniperscope.db import Database
from sniperscope.github import GitHubClient, GitHubRateLimitError
from sniperscope.models import (
    DOMAIN_KEYWORDS,
    Candidate,
    EvidenceFact,
    ExtractionRun,
    Repo,
)

logger = logging.getLogger(__name__)

TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs"}
TEST_FILE_PATTERNS = [
    re.compile(r".*\.test\.\w+$"),
    re.compile(r".*\.spec\.\w+$"),
    re.compile(r"^test_.*\.py$"),
    re.compile(r".*_test\.\w+$"),
]

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rb", ".go", ".rs", ".java",
    ".cs", ".cpp", ".c", ".h", ".hpp", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".zsh", ".lua", ".r", ".sql",
}

CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|refactor|test|docs|chore|style|perf|build|ci|revert)(\(.+\))?:"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_iso(timestamp: str) -> Optional[datetime]:
    """Parse the first 19 chars of a GitHub ISO timestamp, or None."""
    try:
        return datetime.strptime(timestamp[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _fact(candidate_id: str, run_id: str, category: str, fact_key: str,
          fact_value: Any, fact_type: str, source: str) -> EvidenceFact:
    """Create a single EvidenceFact with standard fields."""
    return EvidenceFact(
        candidate_id=candidate_id,
        category=category,
        fact_key=fact_key,
        fact_value=str(fact_value),
        fact_type=fact_type,
        source=source,
        extraction_run_id=run_id,
    )


def _user_source(repos: List[Dict[str, Any]]) -> str:
    """Aggregate-fact source attribution, derived from the repo owner."""
    return "github:user:{}".format(repos[0]["owner"]["login"]) if repos else ""


def _repo_source(full_name: str) -> str:
    return "github:repo:{}".format(full_name)


# ----------------------------------------------------------------------
# Profile
# ----------------------------------------------------------------------

def fetch_and_upsert_candidate(username: str, discovered_via: str,
                               db: Database, client: GitHubClient) -> Optional[str]:
    """Fetch the GitHub profile and upsert the candidate row.

    Returns the candidate id, or None if the user does not exist.
    """
    user_data = client.get_user(username)
    if not user_data:
        logger.error("User '%s' not found on GitHub.", username)
        return None

    candidate = Candidate(
        github_id=user_data["id"],
        github_login=user_data["login"],
        display_name=user_data.get("name"),
        email=user_data.get("email"),
        bio=user_data.get("bio"),
        company=user_data.get("company"),
        location=user_data.get("location"),
        avatar_url=user_data.get("avatar_url"),
        public_repos=user_data.get("public_repos"),
        followers=user_data.get("followers"),
        following=user_data.get("following"),
        github_created_at=user_data.get("created_at"),
        discovered_via=discovered_via,
    )
    return db.upsert_candidate(candidate)


# ----------------------------------------------------------------------
# Repos and commits
# ----------------------------------------------------------------------

def fetch_active_repos(username: str, client: GitHubClient) -> List[Dict[str, Any]]:
    """Repos that are non-fork, non-archived, pushed within the lookback window."""
    all_repos = client.get_user_repos(username)
    cutoff = (_utcnow() - timedelta(days=config.COMMIT_LOOKBACK_DAYS)).isoformat() + "Z"
    active = [
        repo for repo in all_repos
        if not repo.get("fork", False)
        and not repo.get("archived", False)
        and (repo.get("pushed_at") or "") >= cutoff
    ]
    logger.info(
        "User '%s': %d total repos, %d active (non-fork, non-archived, pushed in last %d days).",
        username, len(all_repos), len(active), config.COMMIT_LOOKBACK_DAYS,
    )
    return active


def fetch_commits_for_repos(username: str, repos: List[Dict[str, Any]],
                            client: GitHubClient) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch commits authored by the user for each repo."""
    commits_by_repo: Dict[str, List[Dict[str, Any]]] = {}
    for repo in repos:
        full_name = repo["full_name"]
        try:
            commits = client.get_repo_commits(full_name, author=username)
            commits_by_repo[full_name] = commits
            logger.debug("Fetched %d commits for %s (author: %s).",
                         len(commits), full_name, username)
        except GitHubRateLimitError:
            logger.warning("Rate limit hit while fetching commits for %s. "
                           "Stopping commit fetch.", full_name)
            break
        except Exception as e:
            logger.warning("Failed to fetch commits for %s: %s", full_name, e)
            commits_by_repo[full_name] = []
    return commits_by_repo


# ----------------------------------------------------------------------
# Languages
# ----------------------------------------------------------------------

def extract_language_facts(
    candidate_id: str, run_id: str, repos: List[Dict[str, Any]],
    client: GitHubClient,
) -> Tuple[List[EvidenceFact], Dict[str, Dict[str, int]]]:
    """Language facts across all repos.

    Returns (facts, per_repo_languages) where per_repo_languages maps
    full_name -> {language: bytes}.
    """
    facts: List[EvidenceFact] = []
    total_loc: Dict[str, int] = defaultdict(int)
    per_repo_languages: Dict[str, Dict[str, int]] = {}
    user_source = _user_source(repos)

    for repo in repos:
        full_name = repo["full_name"]
        try:
            languages = client.get_repo_languages(full_name)
        except Exception as e:
            logger.warning("Failed to get languages for %s: %s", full_name, e)
            languages = {}

        per_repo_languages[full_name] = languages

        primary = repo.get("language")
        if primary:
            facts.append(_fact(candidate_id, run_id, "language",
                               "primary_language", primary, "string",
                               _repo_source(full_name)))

        for lang, bytes_count in languages.items():
            total_loc[lang] += bytes_count

    for lang, total_bytes in sorted(total_loc.items(), key=lambda x: -x[1]):
        facts.append(_fact(candidate_id, run_id, "language",
                           "total_bytes:{}".format(lang), total_bytes,
                           "number", user_source))

    facts.append(_fact(candidate_id, run_id, "language",
                       "language_diversity", len(total_loc), "number", user_source))

    return facts, per_repo_languages


# ----------------------------------------------------------------------
# Testing
# ----------------------------------------------------------------------

def _is_test_file(filename: str) -> bool:
    lower = filename.lower()
    return any(pattern.match(lower) for pattern in TEST_FILE_PATTERNS)


def _is_source_file(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in SOURCE_EXTENSIONS)


def _count_repo_files(full_name: str, client: GitHubClient) -> Tuple[int, int]:
    """Count (test_files, source_files) for one repo via the contents API.

    Test directories are scanned wholesale; the root and one level of
    non-test directories are scanned for source files.
    """
    test_count = 0
    source_count = 0

    for test_dir in TEST_DIR_NAMES:
        try:
            entries = client.list_directory(full_name, test_dir)
            test_count += sum(1 for e in entries if e.get("type") == "file")
        except Exception as e:
            logger.debug("Error listing %s/%s: %s", full_name, test_dir, e)

    try:
        for entry in client.list_directory(full_name, ""):
            name = entry.get("name", "")
            if entry.get("type") == "file":
                if _is_test_file(name):
                    test_count += 1
                if _is_source_file(name):
                    source_count += 1
            elif entry.get("type") == "dir" and name.lower() not in TEST_DIR_NAMES:
                try:
                    source_count += sum(
                        1 for sub in client.list_directory(full_name, name)
                        if sub.get("type") == "file"
                        and _is_source_file(sub.get("name", ""))
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.debug("Error listing root of %s: %s", full_name, e)

    return test_count, source_count


def extract_testing_facts(
    candidate_id: str, run_id: str, repos: List[Dict[str, Any]],
    commits_by_repo: Dict[str, List[Dict[str, Any]]], client: GitHubClient,
) -> Tuple[List[EvidenceFact], Dict[str, int], Dict[str, int]]:
    """Testing-related facts. Returns (facts, test_counts, source_counts)."""
    facts: List[EvidenceFact] = []
    test_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    user_source = _user_source(repos)

    for repo in repos:
        full_name = repo["full_name"]
        test_count, source_count = _count_repo_files(full_name, client)
        test_counts[full_name] = test_count
        source_counts[full_name] = source_count
        facts.append(_fact(candidate_id, run_id, "testing",
                           "test_file_count", test_count, "number",
                           _repo_source(full_name)))

    total_test_files = sum(test_counts.values())
    total_source_files = sum(source_counts.values())
    total_files = total_test_files + total_source_files
    ratio = round(total_test_files / total_files, 4) if total_files > 0 else 0.0

    facts.append(_fact(candidate_id, run_id, "testing",
                       "test_file_ratio", ratio, "number", user_source))
    facts.append(_fact(candidate_id, run_id, "testing",
                       "total_test_files", total_test_files, "number", user_source))
    facts.append(_fact(candidate_id, run_id, "testing",
                       "total_source_files", total_source_files, "number", user_source))

    # Commits touching test files — message-based heuristic. The list-commits
    # endpoint does not return the `files` array (that would be an N+1 of
    # get_commit_detail calls), so commit messages mentioning "test" stand in.
    all_commits = [c for commits in commits_by_repo.values() for c in commits]
    total_commits = len(all_commits)
    if total_commits > 0:
        test_touching = sum(
            1 for c in all_commits
            if "test" in (c.get("commit", {}).get("message", "") or "").lower()
        )
        facts.append(_fact(candidate_id, run_id, "testing",
                           "commits_touching_tests", test_touching,
                           "number", user_source))
        facts.append(_fact(candidate_id, run_id, "testing",
                           "commits_touching_tests_ratio",
                           round(test_touching / total_commits, 4),
                           "number", user_source))

    return facts, test_counts, source_counts


# ----------------------------------------------------------------------
# Commit patterns
# ----------------------------------------------------------------------

def extract_commit_facts(
    candidate_id: str, run_id: str,
    commits_by_repo: Dict[str, List[Dict[str, Any]]],
) -> List[EvidenceFact]:
    """Commit pattern facts from all commits across repos."""
    facts: List[EvidenceFact] = []
    all_commits: List[Dict[str, Any]] = []
    user_source = ""

    for commits in commits_by_repo.values():
        if not user_source and commits:
            author_login = (commits[0].get("author") or {}).get("login", "")
            if author_login:
                user_source = "github:user:{}".format(author_login)
        all_commits.extend(commits)

    if not user_source:
        user_source = "github:commits:aggregate"

    total = len(all_commits)
    facts.append(_fact(candidate_id, run_id, "commit_pattern",
                       "total_commits_in_lookback", total, "number", user_source))
    if total == 0:
        return facts

    lookback_weeks = config.COMMIT_LOOKBACK_DAYS / 7.0
    freq = round(total / lookback_weeks, 2) if lookback_weeks > 0 else 0
    facts.append(_fact(candidate_id, run_id, "commit_pattern",
                       "commits_per_week_avg", freq, "number", user_source))

    first_lines = [
        (c.get("commit", {}).get("message", "") or "").split("\n")[0]
        for c in all_commits
    ]
    msg_lengths = [len(line) for line in first_lines]
    conventional_count = sum(1 for line in first_lines
                             if CONVENTIONAL_COMMIT_RE.match(line))
    coauthor_count = sum(
        1 for c in all_commits
        if "co-authored-by" in (c.get("commit", {}).get("message", "") or "").lower()
    )

    facts.append(_fact(candidate_id, run_id, "commit_pattern",
                       "commit_message_length_avg",
                       round(statistics.mean(msg_lengths), 1), "number", user_source))
    facts.append(_fact(candidate_id, run_id, "commit_pattern",
                       "commit_message_length_median",
                       round(statistics.median(msg_lengths), 1), "number", user_source))
    facts.append(_fact(candidate_id, run_id, "commit_pattern",
                       "conventional_commit_count", conventional_count,
                       "number", user_source))
    facts.append(_fact(candidate_id, run_id, "commit_pattern",
                       "conventional_commit_ratio", round(conventional_count / total, 4),
                       "number", user_source))
    # Co-author trailers are an AI pair-programming signal
    facts.append(_fact(candidate_id, run_id, "commit_pattern",
                       "coauthor_commit_count", coauthor_count, "number", user_source))
    facts.append(_fact(candidate_id, run_id, "commit_pattern",
                       "coauthor_commit_ratio", round(coauthor_count / total, 4),
                       "number", user_source))

    return facts


# ----------------------------------------------------------------------
# CI/CD
# ----------------------------------------------------------------------

def extract_ci_facts(
    candidate_id: str, run_id: str, repos: List[Dict[str, Any]],
    client: GitHubClient,
) -> Tuple[List[EvidenceFact], Dict[str, bool]]:
    """CI/CD facts. Returns (facts, ci_status_by_repo)."""
    facts: List[EvidenceFact] = []
    ci_by_repo: Dict[str, bool] = {}
    total_workflow_files = 0
    user_source = _user_source(repos)

    for repo in repos:
        full_name = repo["full_name"]
        has_ci = False
        workflow_count = 0

        # One API call: list the workflows directory directly; a missing
        # directory comes back as an empty list.
        try:
            entries = client.list_directory(full_name, ".github/workflows")
            if entries:
                has_ci = True
                workflow_count = sum(
                    1 for e in entries
                    if e.get("type") == "file"
                    and e.get("name", "").endswith((".yml", ".yaml"))
                )
        except Exception as e:
            logger.debug("Error checking CI for %s: %s", full_name, e)

        ci_by_repo[full_name] = has_ci
        total_workflow_files += workflow_count

        facts.append(_fact(candidate_id, run_id, "ci_cd",
                           "has_ci", str(has_ci).lower(), "boolean",
                           _repo_source(full_name)))
        facts.append(_fact(candidate_id, run_id, "ci_cd",
                           "workflow_file_count", workflow_count, "number",
                           _repo_source(full_name)))

    repos_with_ci = sum(1 for has_ci in ci_by_repo.values() if has_ci)
    facts.append(_fact(candidate_id, run_id, "ci_cd",
                       "repos_with_ci", repos_with_ci, "number", user_source))
    facts.append(_fact(candidate_id, run_id, "ci_cd",
                       "total_workflow_files", total_workflow_files,
                       "number", user_source))
    facts.append(_fact(candidate_id, run_id, "ci_cd",
                       "has_any_ci", str(repos_with_ci > 0).lower(),
                       "boolean", user_source))

    return facts, ci_by_repo


# ----------------------------------------------------------------------
# Domain keywords
# ----------------------------------------------------------------------

def extract_domain_keyword_facts(
    candidate_id: str, run_id: str, repos: List[Dict[str, Any]],
    commits_by_repo: Dict[str, List[Dict[str, Any]]],
) -> List[EvidenceFact]:
    """Domain keyword mentions in repo metadata and commit messages.

    Counts only — DOMAIN_KEYWORDS must never filter or exclude candidates.
    """
    facts: List[EvidenceFact] = []
    user_source = _user_source(repos)

    for domain, keywords in DOMAIN_KEYWORDS.items():
        keyword_set = [kw.lower() for kw in keywords]
        matching_repos: List[str] = []

        for repo in repos:
            desc = (repo.get("description") or "").lower()
            name = repo.get("name", "").lower()
            topics = [t.lower() for t in (repo.get("topics") or [])]
            # count each repo at most once per domain
            if any(kw in desc or kw in name or kw in topics for kw in keyword_set):
                matching_repos.append(repo["full_name"])

        commit_mention_count = sum(
            1 for commits in commits_by_repo.values() for commit in commits
            if any(kw in (commit.get("commit", {}).get("message", "") or "").lower()
                   for kw in keyword_set)
        )

        facts.append(_fact(candidate_id, run_id, "domain_keyword",
                           "domain:{}:repo_mentions".format(domain),
                           len(matching_repos), "number", user_source))
        facts.append(_fact(candidate_id, run_id, "domain_keyword",
                           "domain:{}:commit_mentions".format(domain),
                           commit_mention_count, "number", user_source))
        if matching_repos:
            facts.append(_fact(candidate_id, run_id, "domain_keyword",
                               "domain:{}:matching_repos".format(domain),
                               json.dumps(matching_repos), "json", user_source))

    return facts


# ----------------------------------------------------------------------
# Temporal
# ----------------------------------------------------------------------

def extract_temporal_facts(
    candidate_id: str, run_id: str, repos: List[Dict[str, Any]],
    commits_by_repo: Dict[str, List[Dict[str, Any]]],
    github_created_at: Optional[str],
) -> List[EvidenceFact]:
    """Temporal activity facts — timeline, gaps, account age."""
    facts: List[EvidenceFact] = []
    user_source = _user_source(repos)

    created_dates = [r["created_at"] for r in repos if r.get("created_at")]
    push_dates = [r["pushed_at"] for r in repos if r.get("pushed_at")]

    if created_dates:
        facts.append(_fact(candidate_id, run_id, "temporal",
                           "first_repo_created_at", min(created_dates),
                           "string", user_source))
    if push_dates:
        facts.append(_fact(candidate_id, run_id, "temporal",
                           "most_recent_push_at", max(push_dates),
                           "string", user_source))

    commit_dates = sorted(
        dt for commits in commits_by_repo.values() for commit in commits
        for dt in [_parse_iso(commit.get("commit", {}).get("author", {}).get("date") or "")]
        if dt is not None
    )

    if commit_dates:
        active_months = {"{}-{:02d}".format(dt.year, dt.month) for dt in commit_dates}
        facts.append(_fact(candidate_id, run_id, "temporal",
                           "active_months_count", len(active_months),
                           "number", user_source))
        facts.append(_fact(candidate_id, run_id, "temporal",
                           "active_months", json.dumps(sorted(active_months)),
                           "json", user_source))

        if len(commit_dates) >= 2:
            longest_gap = max(
                (b - a).days for a, b in zip(commit_dates, commit_dates[1:])
            )
            facts.append(_fact(candidate_id, run_id, "temporal",
                               "longest_commit_gap_days", longest_gap,
                               "number", user_source))

    if github_created_at:
        account_created = _parse_iso(github_created_at)
        if account_created:
            facts.append(_fact(candidate_id, run_id, "temporal",
                               "account_age_days",
                               (_utcnow() - account_created).days,
                               "number", user_source))

    return facts


# ----------------------------------------------------------------------
# Collaboration
# ----------------------------------------------------------------------

def extract_collaboration_facts(
    candidate_id: str, run_id: str, username: str,
    repos: List[Dict[str, Any]], client: GitHubClient,
) -> List[EvidenceFact]:
    """Collaboration facts — PRs, reviews, solo-repo ratio."""
    facts: List[EvidenceFact] = []
    user_source = "github:user:{}".format(username)

    try:
        pr_count = len(client.get_user_prs(username))
    except Exception as e:
        logger.warning("Failed to fetch PRs for %s: %s", username, e)
        pr_count = 0
    facts.append(_fact(candidate_id, run_id, "collaboration",
                       "total_prs_opened", pr_count, "number", user_source))

    try:
        review_count = len(client.get_user_pr_reviews(username))
    except Exception as e:
        logger.warning("Failed to fetch PR reviews for %s: %s", username, e)
        review_count = 0
    facts.append(_fact(candidate_id, run_id, "collaboration",
                       "total_pr_reviews_given", review_count, "number", user_source))

    solo_count = 0
    checked_count = 0
    for repo in repos:
        try:
            contributors = client.get_repo_contributors(repo["full_name"])
            checked_count += 1
            if len(contributors) <= 1:
                solo_count += 1
        except Exception as e:
            logger.debug("Error getting contributors for %s: %s",
                         repo["full_name"], e)

    solo_ratio = round(solo_count / checked_count, 4) if checked_count > 0 else 0.0
    facts.append(_fact(candidate_id, run_id, "collaboration",
                       "solo_repo_count", solo_count, "number", user_source))
    facts.append(_fact(candidate_id, run_id, "collaboration",
                       "solo_repo_ratio", solo_ratio, "number", user_source))

    return facts


# ----------------------------------------------------------------------
# Repo metadata
# ----------------------------------------------------------------------

def extract_repo_metadata_facts(
    candidate_id: str, run_id: str, repos: List[Dict[str, Any]],
) -> List[EvidenceFact]:
    """Aggregate repo metadata facts — stars, forks, ages, topics."""
    facts: List[EvidenceFact] = []
    user_source = _user_source(repos)

    facts.append(_fact(candidate_id, run_id, "repo_metadata",
                       "total_public_repos", len(repos), "number", user_source))
    facts.append(_fact(candidate_id, run_id, "repo_metadata",
                       "total_stars_received",
                       sum(r.get("stargazers_count", 0) for r in repos),
                       "number", user_source))
    facts.append(_fact(candidate_id, run_id, "repo_metadata",
                       "total_forks_received",
                       sum(r.get("forks_count", 0) for r in repos),
                       "number", user_source))

    now = _utcnow()
    repo_ages = [
        (now - created).days for r in repos
        for created in [_parse_iso(r.get("created_at") or "")]
        if created is not None
    ]
    if repo_ages:
        facts.append(_fact(candidate_id, run_id, "repo_metadata",
                           "avg_repo_age_days", round(statistics.mean(repo_ages), 1),
                           "number", user_source))

    all_topics = {t for r in repos for t in (r.get("topics") or [])}
    if all_topics:
        facts.append(_fact(candidate_id, run_id, "repo_metadata",
                           "topics_used", json.dumps(sorted(all_topics)),
                           "json", user_source))

    return facts


# ----------------------------------------------------------------------
# Repo snapshots
# ----------------------------------------------------------------------

def upsert_repos(
    candidate_id: str, run_id: str, repos: List[Dict[str, Any]],
    ci_by_repo: Dict[str, bool], test_counts: Dict[str, int],
    source_counts: Dict[str, int],
    languages_by_repo: Dict[str, Dict[str, int]], db: Database,
) -> None:
    """Upsert all repos into the database with enriched metadata."""
    for repo in repos:
        full_name = repo["full_name"]
        languages = languages_by_repo.get(full_name, {})
        test_count = test_counts.get(full_name, 0)
        topics = repo.get("topics") or []

        repo_model = Repo(
            candidate_id=candidate_id,
            github_repo_id=repo["id"],
            full_name=full_name,
            description=repo.get("description"),
            primary_language=repo.get("language"),
            languages_json=json.dumps(languages) if languages else None,
            stars=repo.get("stargazers_count", 0),
            forks=repo.get("forks_count", 0),
            is_fork=repo.get("fork", False),
            is_archived=repo.get("archived", False),
            created_at=repo.get("created_at"),
            pushed_at=repo.get("pushed_at"),
            topics_json=json.dumps(topics) if topics else None,
            has_ci=ci_by_repo.get(full_name, False),
            has_tests=test_count > 0,
            test_file_count=test_count,
            source_file_count=source_counts.get(full_name, 0),
            license=(repo.get("license") or {}).get("spdx_id") if repo.get("license") else None,
            default_branch=repo.get("default_branch"),
            extraction_run_id=run_id,
        )
        try:
            db.upsert_repo(repo_model)
        except Exception as e:
            logger.warning("Failed to upsert repo %s: %s", full_name, e)


# ----------------------------------------------------------------------
# Main extraction
# ----------------------------------------------------------------------

def extract_user(username: str, discovered_via: str,
                 db: Database, client: GitHubClient) -> ExtractionRun:
    """Extract all observable evidence for a GitHub user.

    Creates an ExtractionRun, upserts the candidate, extracts every fact
    category, writes facts (append-only), snapshots repos, and completes
    the run. Returns the completed ExtractionRun.
    """
    start_time = time.time()
    run = ExtractionRun(trigger_type="initial")
    db.create_extraction_run(run)

    logger.info("Starting extraction for user '%s' (run %s).", username, run.id)

    def finish(status: str, error: Optional[str] = None) -> None:
        duration_ms = int((time.time() - start_time) * 1000)
        db.complete_extraction_run(run.id, run.repos_scanned, run.facts_extracted,
                                   duration_ms, status=status, error_message=error)
        run.status = status
        run.error_message = error
        run.duration_ms = duration_ms

    try:
        candidate_id = fetch_and_upsert_candidate(username, discovered_via, db, client)
        if not candidate_id:
            finish("failed", "User not found: {}".format(username))
            return run

        run.candidate_id = candidate_id
        db.set_extraction_run_candidate(run.id, candidate_id)

        candidate_row = db.get_candidate(candidate_id)
        github_created_at = candidate_row.get("github_created_at") if candidate_row else None

        repos = fetch_active_repos(username, client)
        if not repos:
            logger.info("No active repos found for '%s'.", username)
            finish("completed")
            return run

        commits_by_repo = fetch_commits_for_repos(username, repos, client)

        all_facts: List[EvidenceFact] = []

        logger.info("Extracting language facts...")
        lang_facts, languages_by_repo = extract_language_facts(
            candidate_id, run.id, repos, client)
        all_facts.extend(lang_facts)

        logger.info("Extracting CI/CD facts...")
        ci_facts, ci_by_repo = extract_ci_facts(candidate_id, run.id, repos, client)
        all_facts.extend(ci_facts)

        logger.info("Extracting testing facts...")
        test_facts, test_counts, source_counts = extract_testing_facts(
            candidate_id, run.id, repos, commits_by_repo, client)
        all_facts.extend(test_facts)

        logger.info("Extracting commit pattern facts...")
        all_facts.extend(extract_commit_facts(candidate_id, run.id, commits_by_repo))

        logger.info("Extracting domain keyword facts...")
        all_facts.extend(extract_domain_keyword_facts(
            candidate_id, run.id, repos, commits_by_repo))

        logger.info("Extracting temporal facts...")
        all_facts.extend(extract_temporal_facts(
            candidate_id, run.id, repos, commits_by_repo, github_created_at))

        logger.info("Extracting collaboration facts...")
        all_facts.extend(extract_collaboration_facts(
            candidate_id, run.id, username, repos, client))

        logger.info("Extracting repo metadata facts...")
        all_facts.extend(extract_repo_metadata_facts(candidate_id, run.id, repos))

        logger.info("Writing %d facts to database...", len(all_facts))
        db.insert_facts_batch(all_facts)

        upsert_repos(candidate_id, run.id, repos,
                     ci_by_repo, test_counts, source_counts, languages_by_repo, db)

        run.repos_scanned = len(repos)
        run.facts_extracted = len(all_facts)
        finish("completed")

        logger.info(
            "Extraction complete for '%s': %d repos, %d facts, %dms. API requests: %d.",
            username, len(repos), len(all_facts), run.duration_ms, client.requests_made,
        )

    except GitHubRateLimitError as e:
        finish("partial", "Rate limit: {}".format(str(e)[:200]))
        logger.error("Rate limit hit during extraction for '%s': %s", username, e)

    except Exception as e:
        finish("failed", str(e)[:500])
        logger.exception("Extraction failed for '%s'.", username)

    return run


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract GitHub evidence for a user (deterministic, no LLM)."
    )
    parser.add_argument("--user", required=True, help="GitHub username")
    parser.add_argument("--discovered-via", default="manual",
                        help="How this user was found (default: manual)")
    parser.add_argument("--db-path", help="SQLite database path (overrides config)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    client = GitHubClient()
    with Database(db_path=args.db_path) as db:
        run = extract_user(args.user, args.discovered_via, db, client)

        logging.info("=" * 60)
        logging.info("EXTRACTION SUMMARY")
        logging.info("=" * 60)
        logging.info("User:            %s", args.user)
        logging.info("Status:          %s", run.status)
        logging.info("Repos scanned:   %d", run.repos_scanned)
        logging.info("Facts extracted: %d", run.facts_extracted)
        logging.info("Duration:        %dms", run.duration_ms or 0)
        logging.info("API requests:    %d", client.requests_made)
        if run.error_message:
            logging.info("Error:           %s", run.error_message)
        logging.info("Run ID:          %s", run.id)
        logging.info("=" * 60)


if __name__ == "__main__":
    main()
