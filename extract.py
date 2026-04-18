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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import config
from db import Database
from github_client import GitHubClient, GitHubRateLimitError
from models import Candidate, EvidenceFact, ExtractionRun, Repo, DOMAIN_KEYWORDS

logger = logging.getLogger(__name__)

# Test file/directory patterns
TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs"}
TEST_FILE_PATTERNS = [
    re.compile(r".*\.test\.\w+$"),
    re.compile(r".*\.spec\.\w+$"),
    re.compile(r"^test_.*\.py$"),
    re.compile(r".*_test\.\w+$"),
]

# Source file extensions (for counting non-test source files)
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rb", ".go", ".rs", ".java",
    ".cs", ".cpp", ".c", ".h", ".hpp", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".zsh", ".lua", ".r", ".sql",
}

# Conventional commit pattern
CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|refactor|test|docs|chore|style|perf|build|ci|revert)(\(.+\))?:"
)


# ============================================================================
# Fact builder — convenience to avoid repeating boilerplate
# ============================================================================

def _fact(
    candidate_id: str,
    run_id: str,
    category: str,
    fact_key: str,
    fact_value: Any,
    fact_type: str,
    source: str,
) -> EvidenceFact:
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


# ============================================================================
# Profile extraction
# ============================================================================

def fetch_and_upsert_candidate(
    username: str,
    discovered_via: str,
    db: Database,
    client: GitHubClient,
) -> Optional[str]:
    """Fetch GitHub user profile and upsert into candidates table.

    Returns candidate_id or None if user not found.
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


# ============================================================================
# Repo fetching and filtering
# ============================================================================

def fetch_active_repos(
    username: str,
    client: GitHubClient,
) -> List[Dict[str, Any]]:
    """Fetch repos that are non-fork, non-archived, pushed within lookback window."""
    all_repos = client.get_user_repos(username)
    cutoff = (datetime.utcnow() - timedelta(days=config.COMMIT_LOOKBACK_DAYS)).isoformat() + "Z"
    active = []
    for repo in all_repos:
        if repo.get("fork", False):
            continue
        if repo.get("archived", False):
            continue
        pushed = repo.get("pushed_at", "")
        if pushed and pushed >= cutoff:
            active.append(repo)
    logger.info(
        "User '%s': %d total repos, %d active (non-fork, non-archived, pushed in last %d days).",
        username, len(all_repos), len(active), config.COMMIT_LOOKBACK_DAYS,
    )
    return active


# ============================================================================
# Language extraction
# ============================================================================

def extract_language_facts(
    candidate_id: str,
    run_id: str,
    repos: List[Dict[str, Any]],
    client: GitHubClient,
) -> Tuple[List[EvidenceFact], Dict[str, Dict[str, int]]]:
    """Extract language facts across all repos.

    Returns (facts, per_repo_languages) where per_repo_languages maps
    full_name -> {language: bytes}.
    """
    facts = []  # type: List[EvidenceFact]
    total_loc = defaultdict(int)  # type: Dict[str, int]
    per_repo_languages = {}  # type: Dict[str, Dict[str, int]]
    user_source = "github:user:{}".format(repos[0]["owner"]["login"]) if repos else ""

    for repo in repos:
        full_name = repo["full_name"]
        repo_source = "github:repo:{}".format(full_name)
        try:
            languages = client.get_repo_languages(full_name)
        except Exception as e:
            logger.warning("Failed to get languages for %s: %s", full_name, e)
            languages = {}

        per_repo_languages[full_name] = languages

        # Primary language per repo
        primary = repo.get("language")
        if primary:
            facts.append(_fact(
                candidate_id, run_id, "language",
                "primary_language", primary, "string", repo_source,
            ))

        # Accumulate total LOC
        for lang, bytes_count in languages.items():
            total_loc[lang] += bytes_count

    # Total LOC per language (aggregate)
    for lang, total_bytes in sorted(total_loc.items(), key=lambda x: -x[1]):
        facts.append(_fact(
            candidate_id, run_id, "language",
            "total_bytes:{}".format(lang), total_bytes, "number", user_source,
        ))

    # Language diversity
    facts.append(_fact(
        candidate_id, run_id, "language",
        "language_diversity", len(total_loc), "number", user_source,
    ))

    return facts, per_repo_languages


# ============================================================================
# Testing extraction
# ============================================================================

def _is_test_file(filename: str) -> bool:
    """Check if a filename matches test file patterns."""
    lower = filename.lower()
    for pattern in TEST_FILE_PATTERNS:
        if pattern.match(lower):
            return True
    return False


def _is_source_file(filename: str) -> bool:
    """Check if a filename is a source code file (by extension)."""
    for ext in SOURCE_EXTENSIONS:
        if filename.lower().endswith(ext):
            return True
    return False


def extract_testing_facts(
    candidate_id: str,
    run_id: str,
    repos: List[Dict[str, Any]],
    commits_by_repo: Dict[str, List[Dict[str, Any]]],
    client: GitHubClient,
) -> Tuple[List[EvidenceFact], Dict[str, int], Dict[str, int]]:
    """Extract testing-related facts.

    Returns (facts, test_counts_by_repo, source_counts_by_repo).
    """
    facts = []  # type: List[EvidenceFact]
    test_counts = {}  # type: Dict[str, int]
    source_counts = {}  # type: Dict[str, int]
    total_test_files = 0
    total_source_files = 0
    user_source = "github:user:{}".format(repos[0]["owner"]["login"]) if repos else ""

    for repo in repos:
        full_name = repo["full_name"]
        repo_source = "github:repo:{}".format(full_name)
        repo_test_count = 0
        repo_source_count = 0

        # Check for test directories
        for test_dir in TEST_DIR_NAMES:
            try:
                entries = client.list_directory(full_name, test_dir)
                if entries:
                    # Count test files in the directory
                    for entry in entries:
                        if entry.get("type") == "file":
                            repo_test_count += 1
            except Exception as e:
                logger.debug("Error listing %s/%s: %s", full_name, test_dir, e)

        # Check root for test files (test_*.py, *.test.js, etc.)
        try:
            root_entries = client.list_directory(full_name, "")
            for entry in root_entries:
                name = entry.get("name", "")
                if entry.get("type") == "file":
                    if _is_test_file(name):
                        repo_test_count += 1
                    if _is_source_file(name):
                        repo_source_count += 1
                elif entry.get("type") == "dir" and name.lower() not in TEST_DIR_NAMES:
                    # Count source files in non-test directories (top level only)
                    try:
                        subdir = client.list_directory(full_name, name)
                        for sub_entry in subdir:
                            if sub_entry.get("type") == "file" and _is_source_file(sub_entry.get("name", "")):
                                repo_source_count += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Error listing root of %s: %s", full_name, e)

        test_counts[full_name] = repo_test_count
        source_counts[full_name] = repo_source_count
        total_test_files += repo_test_count
        total_source_files += repo_source_count

        facts.append(_fact(
            candidate_id, run_id, "testing",
            "test_file_count", repo_test_count, "number", repo_source,
        ))

    # Aggregate test_file_ratio
    total_files = total_test_files + total_source_files
    ratio = round(total_test_files / total_files, 4) if total_files > 0 else 0.0
    facts.append(_fact(
        candidate_id, run_id, "testing",
        "test_file_ratio", ratio, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "testing",
        "total_test_files", total_test_files, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "testing",
        "total_source_files", total_source_files, "number", user_source,
    ))

    # Commits touching test files — message-based heuristic.
    # The GitHub list-commits endpoint does not return the `files` array
    # (that requires per-commit get_commit_detail which is N+1), so we
    # rely on commit messages mentioning "test".
    total_commits = 0
    test_touching_commits = 0
    for full_name, commits in commits_by_repo.items():
        for commit in commits:
            total_commits += 1
            msg = (commit.get("commit", {}).get("message", "") or "").lower()
            if "test" in msg:
                test_touching_commits += 1

    if total_commits > 0:
        test_commit_ratio = round(test_touching_commits / total_commits, 4)
        facts.append(_fact(
            candidate_id, run_id, "testing",
            "commits_touching_tests", test_touching_commits, "number", user_source,
        ))
        facts.append(_fact(
            candidate_id, run_id, "testing",
            "commits_touching_tests_ratio", test_commit_ratio, "number", user_source,
        ))

    return facts, test_counts, source_counts


# ============================================================================
# Commit pattern extraction
# ============================================================================

def extract_commit_facts(
    candidate_id: str,
    run_id: str,
    commits_by_repo: Dict[str, List[Dict[str, Any]]],
) -> List[EvidenceFact]:
    """Extract commit pattern facts from all commits across repos."""
    facts = []  # type: List[EvidenceFact]
    all_commits = []  # type: List[Dict[str, Any]]
    user_source = ""

    for full_name, commits in commits_by_repo.items():
        if not user_source and commits:
            author_login = (commits[0].get("author") or {}).get("login", "")
            if author_login:
                user_source = "github:user:{}".format(author_login)
        all_commits.extend(commits)

    if not user_source:
        user_source = "github:commits:aggregate"

    total = len(all_commits)
    facts.append(_fact(
        candidate_id, run_id, "commit_pattern",
        "total_commits_in_lookback", total, "number", user_source,
    ))

    if total == 0:
        return facts

    # Commit frequency (commits per week)
    lookback_weeks = config.COMMIT_LOOKBACK_DAYS / 7.0
    freq = round(total / lookback_weeks, 2) if lookback_weeks > 0 else 0
    facts.append(_fact(
        candidate_id, run_id, "commit_pattern",
        "commits_per_week_avg", freq, "number", user_source,
    ))

    # Commit message lengths
    msg_lengths = []  # type: List[int]
    conventional_count = 0
    coauthor_count = 0

    for commit in all_commits:
        msg = commit.get("commit", {}).get("message", "") or ""
        # Use first line for length/conventional check
        first_line = msg.split("\n")[0]
        msg_lengths.append(len(first_line))

        if CONVENTIONAL_COMMIT_RE.match(first_line):
            conventional_count += 1

        if "co-authored-by" in msg.lower():
            coauthor_count += 1

    avg_len = round(statistics.mean(msg_lengths), 1) if msg_lengths else 0
    median_len = round(statistics.median(msg_lengths), 1) if msg_lengths else 0

    facts.append(_fact(
        candidate_id, run_id, "commit_pattern",
        "commit_message_length_avg", avg_len, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "commit_pattern",
        "commit_message_length_median", median_len, "number", user_source,
    ))

    # Conventional commits
    conventional_ratio = round(conventional_count / total, 4)
    facts.append(_fact(
        candidate_id, run_id, "commit_pattern",
        "conventional_commit_count", conventional_count, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "commit_pattern",
        "conventional_commit_ratio", conventional_ratio, "number", user_source,
    ))

    # Co-author detection (AI pair programming signal)
    facts.append(_fact(
        candidate_id, run_id, "commit_pattern",
        "coauthor_commit_count", coauthor_count, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "commit_pattern",
        "coauthor_commit_ratio", round(coauthor_count / total, 4), "number", user_source,
    ))

    return facts


# ============================================================================
# CI/CD extraction
# ============================================================================

def extract_ci_facts(
    candidate_id: str,
    run_id: str,
    repos: List[Dict[str, Any]],
    client: GitHubClient,
) -> Tuple[List[EvidenceFact], Dict[str, bool]]:
    """Extract CI/CD facts. Returns (facts, ci_status_by_repo)."""
    facts = []  # type: List[EvidenceFact]
    ci_by_repo = {}  # type: Dict[str, bool]
    total_workflow_files = 0
    repos_with_ci = 0
    user_source = "github:user:{}".format(repos[0]["owner"]["login"]) if repos else ""

    for repo in repos:
        full_name = repo["full_name"]
        repo_source = "github:repo:{}".format(full_name)
        has_ci = False
        workflow_count = 0

        # Single API call: list the workflows directory directly. If it
        # doesn't exist, list_directory returns []. Saves one check_path_exists
        # call per repo (N extra calls across N repos).
        try:
            entries = client.list_directory(full_name, ".github/workflows")
            if entries:
                has_ci = True
                workflow_count = sum(
                    1 for e in entries
                    if e.get("type") == "file" and (
                        e.get("name", "").endswith(".yml") or
                        e.get("name", "").endswith(".yaml")
                    )
                )
        except Exception as e:
            logger.debug("Error checking CI for %s: %s", full_name, e)

        ci_by_repo[full_name] = has_ci
        total_workflow_files += workflow_count

        if has_ci:
            repos_with_ci += 1

        facts.append(_fact(
            candidate_id, run_id, "ci_cd",
            "has_ci", str(has_ci).lower(), "boolean", repo_source,
        ))
        facts.append(_fact(
            candidate_id, run_id, "ci_cd",
            "workflow_file_count", workflow_count, "number", repo_source,
        ))

    # Aggregates
    facts.append(_fact(
        candidate_id, run_id, "ci_cd",
        "repos_with_ci", repos_with_ci, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "ci_cd",
        "total_workflow_files", total_workflow_files, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "ci_cd",
        "has_any_ci", str(repos_with_ci > 0).lower(), "boolean", user_source,
    ))

    return facts, ci_by_repo


# ============================================================================
# Domain keyword extraction
# ============================================================================

def extract_domain_keyword_facts(
    candidate_id: str,
    run_id: str,
    repos: List[Dict[str, Any]],
    commits_by_repo: Dict[str, List[Dict[str, Any]]],
) -> List[EvidenceFact]:
    """Extract domain keyword mentions from repo descriptions and commit messages."""
    facts = []  # type: List[EvidenceFact]
    user_source = "github:user:{}".format(repos[0]["owner"]["login"]) if repos else ""

    for domain, keywords in DOMAIN_KEYWORDS.items():
        repo_mention_count = 0
        commit_mention_count = 0
        matching_repos = []  # type: List[str]

        # Check repo descriptions and names
        for repo in repos:
            full_name = repo["full_name"]
            desc = (repo.get("description") or "").lower()
            name = repo.get("name", "").lower()
            topics = [t.lower() for t in (repo.get("topics") or [])]

            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower in desc or kw_lower in name or kw_lower in topics:
                    repo_mention_count += 1
                    matching_repos.append(full_name)
                    break  # count each repo at most once per domain

        # Check commit messages
        for full_name, commits in commits_by_repo.items():
            for commit in commits:
                msg = (commit.get("commit", {}).get("message", "") or "").lower()
                for kw in keywords:
                    if kw.lower() in msg:
                        commit_mention_count += 1
                        break  # count each commit at most once per domain

        facts.append(_fact(
            candidate_id, run_id, "domain_keyword",
            "domain:{}:repo_mentions".format(domain), repo_mention_count,
            "number", user_source,
        ))
        facts.append(_fact(
            candidate_id, run_id, "domain_keyword",
            "domain:{}:commit_mentions".format(domain), commit_mention_count,
            "number", user_source,
        ))
        if matching_repos:
            facts.append(_fact(
                candidate_id, run_id, "domain_keyword",
                "domain:{}:matching_repos".format(domain), json.dumps(matching_repos),
                "json", user_source,
            ))

    return facts


# ============================================================================
# Temporal extraction
# ============================================================================

def extract_temporal_facts(
    candidate_id: str,
    run_id: str,
    repos: List[Dict[str, Any]],
    commits_by_repo: Dict[str, List[Dict[str, Any]]],
    github_created_at: Optional[str],
) -> List[EvidenceFact]:
    """Extract temporal activity facts."""
    facts = []  # type: List[EvidenceFact]
    user_source = "github:user:{}".format(repos[0]["owner"]["login"]) if repos else ""

    # Repo creation dates and push dates
    created_dates = []  # type: List[str]
    push_dates = []  # type: List[str]
    for repo in repos:
        created = repo.get("created_at")
        pushed = repo.get("pushed_at")
        if created:
            created_dates.append(created)
        if pushed:
            push_dates.append(pushed)

    if created_dates:
        first_repo = min(created_dates)
        facts.append(_fact(
            candidate_id, run_id, "temporal",
            "first_repo_created_at", first_repo, "string", user_source,
        ))

    if push_dates:
        most_recent = max(push_dates)
        facts.append(_fact(
            candidate_id, run_id, "temporal",
            "most_recent_push_at", most_recent, "string", user_source,
        ))

    # Collect all commit dates for activity analysis
    commit_dates = []  # type: List[datetime]
    for full_name, commits in commits_by_repo.items():
        for commit in commits:
            date_str = commit.get("commit", {}).get("author", {}).get("date")
            if date_str:
                try:
                    dt = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
                    commit_dates.append(dt)
                except ValueError:
                    pass

    if commit_dates:
        commit_dates.sort()

        # Active months
        active_months = set()  # type: set
        for dt in commit_dates:
            active_months.add("{}-{}".format(dt.year, str(dt.month).zfill(2)))
        facts.append(_fact(
            candidate_id, run_id, "temporal",
            "active_months_count", len(active_months), "number", user_source,
        ))
        facts.append(_fact(
            candidate_id, run_id, "temporal",
            "active_months", json.dumps(sorted(active_months)), "json", user_source,
        ))

        # Longest gap between consecutive commits
        if len(commit_dates) >= 2:
            gaps = []  # type: List[int]
            for i in range(1, len(commit_dates)):
                gap_days = (commit_dates[i] - commit_dates[i - 1]).days
                gaps.append(gap_days)
            longest_gap = max(gaps)
            facts.append(_fact(
                candidate_id, run_id, "temporal",
                "longest_commit_gap_days", longest_gap, "number", user_source,
            ))

    # Account age
    if github_created_at:
        try:
            account_created = datetime.strptime(github_created_at[:19], "%Y-%m-%dT%H:%M:%S")
            account_age_days = (datetime.utcnow() - account_created).days
            facts.append(_fact(
                candidate_id, run_id, "temporal",
                "account_age_days", account_age_days, "number", user_source,
            ))
        except ValueError:
            pass

    return facts


# ============================================================================
# Collaboration extraction
# ============================================================================

def extract_collaboration_facts(
    candidate_id: str,
    run_id: str,
    username: str,
    repos: List[Dict[str, Any]],
    client: GitHubClient,
) -> List[EvidenceFact]:
    """Extract collaboration facts (PRs, reviews, solo repos)."""
    facts = []  # type: List[EvidenceFact]
    user_source = "github:user:{}".format(username)

    # PRs opened
    try:
        prs = client.get_user_prs(username)
        pr_count = len(prs)
    except Exception as e:
        logger.warning("Failed to fetch PRs for %s: %s", username, e)
        pr_count = 0

    facts.append(_fact(
        candidate_id, run_id, "collaboration",
        "total_prs_opened", pr_count, "number", user_source,
    ))

    # PR reviews given
    try:
        reviews = client.get_user_pr_reviews(username)
        review_count = len(reviews)
    except Exception as e:
        logger.warning("Failed to fetch PR reviews for %s: %s", username, e)
        review_count = 0

    facts.append(_fact(
        candidate_id, run_id, "collaboration",
        "total_pr_reviews_given", review_count, "number", user_source,
    ))

    # Solo repo ratio (repos where user is the only contributor)
    solo_count = 0
    checked_count = 0
    for repo in repos:
        full_name = repo["full_name"]
        try:
            contributors = client.get_repo_contributors(full_name)
            checked_count += 1
            if len(contributors) <= 1:
                solo_count += 1
        except Exception as e:
            logger.debug("Error getting contributors for %s: %s", full_name, e)

    if checked_count > 0:
        solo_ratio = round(solo_count / checked_count, 4)
    else:
        solo_ratio = 0.0

    facts.append(_fact(
        candidate_id, run_id, "collaboration",
        "solo_repo_count", solo_count, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "collaboration",
        "solo_repo_ratio", solo_ratio, "number", user_source,
    ))

    return facts


# ============================================================================
# Repo metadata extraction
# ============================================================================

def extract_repo_metadata_facts(
    candidate_id: str,
    run_id: str,
    repos: List[Dict[str, Any]],
) -> List[EvidenceFact]:
    """Extract aggregate repo metadata facts."""
    facts = []  # type: List[EvidenceFact]
    user_source = "github:user:{}".format(repos[0]["owner"]["login"]) if repos else ""

    total_repos = len(repos)
    total_stars = sum(repo.get("stargazers_count", 0) for repo in repos)
    total_forks = sum(repo.get("forks_count", 0) for repo in repos)

    facts.append(_fact(
        candidate_id, run_id, "repo_metadata",
        "total_public_repos", total_repos, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "repo_metadata",
        "total_stars_received", total_stars, "number", user_source,
    ))
    facts.append(_fact(
        candidate_id, run_id, "repo_metadata",
        "total_forks_received", total_forks, "number", user_source,
    ))

    # Average repo age in days
    repo_ages = []  # type: List[int]
    now = datetime.utcnow()
    for repo in repos:
        created = repo.get("created_at")
        if created:
            try:
                created_dt = datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")
                repo_ages.append((now - created_dt).days)
            except ValueError:
                pass

    if repo_ages:
        avg_age = round(statistics.mean(repo_ages), 1)
        facts.append(_fact(
            candidate_id, run_id, "repo_metadata",
            "avg_repo_age_days", avg_age, "number", user_source,
        ))

    # Aggregate topics
    all_topics = set()  # type: set
    for repo in repos:
        topics = repo.get("topics") or []
        for t in topics:
            all_topics.add(t)

    if all_topics:
        facts.append(_fact(
            candidate_id, run_id, "repo_metadata",
            "topics_used", json.dumps(sorted(all_topics)), "json", user_source,
        ))

    return facts


# ============================================================================
# Commit fetching (shared across extraction steps)
# ============================================================================

def fetch_commits_for_repos(
    username: str,
    repos: List[Dict[str, Any]],
    client: GitHubClient,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch commits for each repo, authored by the user.

    Returns dict mapping full_name -> list of commits.
    """
    commits_by_repo = {}  # type: Dict[str, List[Dict[str, Any]]]
    for repo in repos:
        full_name = repo["full_name"]
        try:
            commits = client.get_repo_commits(full_name, author=username)
            commits_by_repo[full_name] = commits
            logger.debug(
                "Fetched %d commits for %s (author: %s).",
                len(commits), full_name, username,
            )
        except GitHubRateLimitError:
            logger.warning("Rate limit hit while fetching commits for %s. Stopping commit fetch.", full_name)
            break
        except Exception as e:
            logger.warning("Failed to fetch commits for %s: %s", full_name, e)
            commits_by_repo[full_name] = []
    return commits_by_repo


# ============================================================================
# Repo upsert helper
# ============================================================================

def upsert_repos(
    candidate_id: str,
    run_id: str,
    repos: List[Dict[str, Any]],
    ci_by_repo: Dict[str, bool],
    test_counts: Dict[str, int],
    source_counts: Dict[str, int],
    languages_by_repo: Dict[str, Dict[str, int]],
    db: Database,
) -> None:
    """Upsert all repos into the database with enriched metadata."""
    for repo in repos:
        full_name = repo["full_name"]
        languages = languages_by_repo.get(full_name, {})
        has_ci = ci_by_repo.get(full_name, False)
        test_count = test_counts.get(full_name, 0)
        source_count = source_counts.get(full_name, 0)
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
            has_ci=has_ci,
            has_tests=test_count > 0,
            test_file_count=test_count,
            source_file_count=source_count,
            license=(repo.get("license") or {}).get("spdx_id") if repo.get("license") else None,
            default_branch=repo.get("default_branch"),
            extraction_run_id=run_id,
        )
        try:
            db.upsert_repo(repo_model)
        except Exception as e:
            logger.warning("Failed to upsert repo %s: %s", full_name, e)


# ============================================================================
# Main extraction function
# ============================================================================

def extract_user(
    username: str,
    discovered_via: str,
    db: Database,
    client: GitHubClient,
) -> ExtractionRun:
    """Extract all observable evidence for a GitHub user.

    This is the main entry point. It:
    1. Creates an ExtractionRun record
    2. Fetches user profile and upserts Candidate
    3. Fetches active repos
    4. Extracts all evidence fact categories
    5. Writes facts to DB
    6. Completes the ExtractionRun

    Returns the completed ExtractionRun.
    """
    start_time = time.time()
    run = ExtractionRun(trigger_type="initial")
    db.create_extraction_run(run)

    logger.info("Starting extraction for user '%s' (run %s).", username, run.id)

    try:
        # Step 1: Fetch and upsert candidate
        candidate_id = fetch_and_upsert_candidate(username, discovered_via, db, client)
        if not candidate_id:
            duration_ms = int((time.time() - start_time) * 1000)
            db.complete_extraction_run(
                run.id, 0, 0, duration_ms,
                status="failed", error_message="User not found: {}".format(username),
            )
            run.status = "failed"
            run.error_message = "User not found: {}".format(username)
            return run

        run.candidate_id = candidate_id
        # Update candidate_id on the run record
        db.conn.execute(
            "UPDATE extraction_runs SET candidate_id = ? WHERE id = ?",
            (candidate_id, run.id),
        )
        db.conn.commit()

        # Get github_created_at for temporal facts
        candidate_row = db.get_candidate_by_github_id(
            client.get_user(username)["id"]
        )
        github_created_at = candidate_row.get("github_created_at") if candidate_row else None

        # Step 2: Fetch active repos
        repos = fetch_active_repos(username, client)
        if not repos:
            logger.info("No active repos found for '%s'.", username)
            duration_ms = int((time.time() - start_time) * 1000)
            db.complete_extraction_run(run.id, 0, 0, duration_ms, status="completed")
            run.status = "completed"
            run.repos_scanned = 0
            run.facts_extracted = 0
            return run

        # Step 3: Fetch commits for all repos (shared data)
        commits_by_repo = fetch_commits_for_repos(username, repos, client)

        # Step 4: Extract all fact categories
        all_facts = []  # type: List[EvidenceFact]

        # 4a: Language facts
        logger.info("Extracting language facts...")
        lang_facts, languages_by_repo = extract_language_facts(
            candidate_id, run.id, repos, client,
        )
        all_facts.extend(lang_facts)

        # 4b: CI/CD facts
        logger.info("Extracting CI/CD facts...")
        ci_facts, ci_by_repo = extract_ci_facts(
            candidate_id, run.id, repos, client,
        )
        all_facts.extend(ci_facts)

        # 4c: Testing facts
        logger.info("Extracting testing facts...")
        test_facts, test_counts, source_counts = extract_testing_facts(
            candidate_id, run.id, repos, commits_by_repo, client,
        )
        all_facts.extend(test_facts)

        # 4d: Commit pattern facts
        logger.info("Extracting commit pattern facts...")
        commit_facts = extract_commit_facts(candidate_id, run.id, commits_by_repo)
        all_facts.extend(commit_facts)

        # 4e: Domain keyword facts
        logger.info("Extracting domain keyword facts...")
        domain_facts = extract_domain_keyword_facts(
            candidate_id, run.id, repos, commits_by_repo,
        )
        all_facts.extend(domain_facts)

        # 4f: Temporal facts
        logger.info("Extracting temporal facts...")
        temporal_facts = extract_temporal_facts(
            candidate_id, run.id, repos, commits_by_repo, github_created_at,
        )
        all_facts.extend(temporal_facts)

        # 4g: Collaboration facts
        logger.info("Extracting collaboration facts...")
        collab_facts = extract_collaboration_facts(
            candidate_id, run.id, username, repos, client,
        )
        all_facts.extend(collab_facts)

        # 4h: Repo metadata facts
        logger.info("Extracting repo metadata facts...")
        metadata_facts = extract_repo_metadata_facts(candidate_id, run.id, repos)
        all_facts.extend(metadata_facts)

        # Step 5: Write all facts to DB
        logger.info("Writing %d facts to database...", len(all_facts))
        db.insert_facts_batch(all_facts)

        # Step 6: Upsert repos with enriched metadata
        upsert_repos(
            candidate_id, run.id, repos,
            ci_by_repo, test_counts, source_counts, languages_by_repo, db,
        )

        # Step 7: Complete the run
        duration_ms = int((time.time() - start_time) * 1000)
        db.complete_extraction_run(
            run.id, len(repos), len(all_facts), duration_ms, status="completed",
        )
        run.status = "completed"
        run.repos_scanned = len(repos)
        run.facts_extracted = len(all_facts)
        run.duration_ms = duration_ms

        logger.info(
            "Extraction complete for '%s': %d repos, %d facts, %dms. API requests: %d.",
            username, len(repos), len(all_facts), duration_ms, client.requests_made,
        )

    except GitHubRateLimitError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        db.complete_extraction_run(
            run.id, run.repos_scanned, run.facts_extracted, duration_ms,
            status="partial", error_message="Rate limit: {}".format(str(e)[:200]),
        )
        run.status = "partial"
        run.error_message = "Rate limit: {}".format(str(e)[:200])
        logger.error("Rate limit hit during extraction for '%s': %s", username, e)

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        db.complete_extraction_run(
            run.id, run.repos_scanned, run.facts_extracted, duration_ms,
            status="failed", error_message=str(e)[:500],
        )
        run.status = "failed"
        run.error_message = str(e)[:500]
        logger.exception("Extraction failed for '%s'.", username)

    return run


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract GitHub evidence for a user (deterministic, no LLM)."
    )
    parser.add_argument("--user", required=True, help="GitHub username")
    parser.add_argument(
        "--discovered-via", default="manual",
        help="How this user was found (default: manual)",
    )
    parser.add_argument("--db-path", help="SQLite database path (overrides config)")
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    # Initialize components
    db = Database(db_path=args.db_path)
    client = GitHubClient()

    try:
        run = extract_user(args.user, args.discovered_via, db, client)

        # Print summary
        logging.info("=" * 60)
        logging.info("EXTRACTION SUMMARY")
        logging.info("=" * 60)
        logging.info("User:           %s", args.user)
        logging.info("Status:         %s", run.status)
        logging.info("Repos scanned:  %d", run.repos_scanned)
        logging.info("Facts extracted: %d", run.facts_extracted)
        logging.info("Duration:       %dms", run.duration_ms or 0)
        logging.info("API requests:   %d", client.requests_made)
        if run.error_message:
            logging.info("Error:          %s", run.error_message)
        logging.info("Run ID:         %s", run.id)
        logging.info("=" * 60)

    finally:
        db.close()


if __name__ == "__main__":
    main()
