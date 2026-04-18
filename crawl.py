"""Seed crawler — discovers candidates from seed repos and GitHub search."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import config
from db import Database
from github_client import GitHubClient, GitHubRateLimitError
from models import Candidate, DOMAIN_KEYWORDS

logger = logging.getLogger(__name__)

# Search API budget: GitHub allows 10 req/min. One second is the minimum
# spacing we use; a larger value lives in config for production tuning.
SEARCH_SLEEP_SECONDS = 1
RE_EXTRACT_DAYS = config.RE_EXTRACT_DAYS


# ============================================================================
# Helpers
# ============================================================================

def _load_seeds(seeds_path: str) -> Dict[str, Any]:
    """Load seeds.json — repos and search queries."""
    with open(seeds_path) as f:
        return json.load(f)


def _candidate_recently_extracted(db: Database, candidate_id: str) -> bool:
    """Return True if the candidate was extracted within RE_EXTRACT_DAYS."""
    cutoff = (datetime.utcnow() - timedelta(days=RE_EXTRACT_DAYS)).isoformat()
    row = db.conn.execute(
        """SELECT id FROM extraction_runs
           WHERE candidate_id = ?
             AND status = 'completed'
             AND completed_at > ?
           LIMIT 1""",
        (candidate_id, cutoff),
    ).fetchone()
    return row is not None


def _update_seed_crawl_stats(
    db: Database, full_name: str, contributor_count: int
) -> None:
    """Stamp last_crawled_at and contributor_count on a seed repo row."""
    db.conn.execute(
        """UPDATE seed_repos
           SET last_crawled_at = ?, contributor_count = ?
           WHERE full_name = ?""",
        (datetime.utcnow().isoformat(), contributor_count, full_name),
    )
    db.conn.commit()


def _contributor_to_candidate(
    contributor: Dict[str, Any],
    discovered_via: str,
    client: GitHubClient,
) -> Optional[Candidate]:
    """Build a Candidate from a contributor dict, enriching with user profile."""
    login = contributor.get("login")
    github_id = contributor.get("id")
    if not login or not github_id:
        return None

    # Enrich with full user profile
    profile = client.get_user(login)
    if profile is None:
        # User deleted or suspended
        return None

    return Candidate(
        github_id=profile["id"],
        github_login=profile["login"],
        display_name=profile.get("name"),
        email=profile.get("email"),
        bio=profile.get("bio"),
        company=profile.get("company"),
        location=profile.get("location"),
        avatar_url=profile.get("avatar_url"),
        public_repos=profile.get("public_repos"),
        followers=profile.get("followers"),
        following=profile.get("following"),
        github_created_at=profile.get("created_at"),
        discovered_via=discovered_via,
    )


def _try_extract(db: Database, client: GitHubClient, candidate_id: str) -> bool:
    """Run extraction for a candidate. Returns True if extraction ran."""
    # Imported here to avoid a circular-import hazard: extract.py itself
    # does not import crawl, so this is safe, but keeping the import local
    # documents that the dependency is one-way.
    from extract import extract_user

    candidate = db.conn.execute(
        "SELECT github_login, discovered_via FROM candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if candidate is None:
        logger.warning("Candidate %s not found in DB — skipping extraction", candidate_id)
        return False

    try:
        extract_user(
            username=candidate["github_login"],
            discovered_via=candidate["discovered_via"],
            db=db,
            client=client,
        )
        return True
    except Exception:
        logger.exception("Extraction failed for candidate %s", candidate_id)
        return False


# Domain labels considered "ERP" vs "AI" for seed-matching heuristic.
# The actual keyword lists live in models.DOMAIN_KEYWORDS; this mapping
# says which domain categories count as "ERP" vs "AI" for this filter.
_ERP_DOMAINS = {"erp", "netsuite", "sap", "oracle", "workday", "salesforce"}
_AI_DOMAINS = {"anthropic", "mcp", "ai_agent"}


def _repo_matches_erp_ai(
    name: str,
    description: Optional[str],
    topics: Optional[List[str]],
) -> bool:
    """Check if a repo looks ERP + AI related based on name/desc/topics.

    Derives keyword lists from models.DOMAIN_KEYWORDS so we have a single
    source of truth; adding a new ERP keyword in models.py automatically
    extends this filter.
    """
    erp_terms = {term for domain in _ERP_DOMAINS for term in DOMAIN_KEYWORDS.get(domain, [])}
    ai_terms = {term for domain in _AI_DOMAINS for term in DOMAIN_KEYWORDS.get(domain, [])}

    searchable = " ".join(
        filter(None, [name.lower(), (description or "").lower()])
    )
    if topics:
        searchable += " " + " ".join(t.lower() for t in topics)

    has_erp = any(term in searchable for term in erp_terms)
    has_ai = any(term in searchable for term in ai_terms)
    return has_erp or has_ai  # either signal qualifies a repo for seeds


# ============================================================================
# crawl_seeds
# ============================================================================

def crawl_seeds(
    db: Database,
    client: GitHubClient,
    seeds_path: str,
) -> Dict[str, int]:
    """Crawl all seed repos: register, get contributors, create candidates."""
    seeds = _load_seeds(seeds_path)
    repos = seeds.get("repos", [])

    repos_crawled = 0
    contributors_found = 0
    new_candidates = 0
    extractions_triggered = 0

    for repo_full_name in repos:
        logger.info("Crawling seed repo %s", repo_full_name)

        # Register in seed_repos table
        db.upsert_seed_repo(repo_full_name, discovered_via="manual")

        # Get contributors
        try:
            contributors = client.get_repo_contributors(repo_full_name)
        except GitHubRateLimitError:
            logger.warning("Rate limit hit crawling %s — stopping", repo_full_name)
            break
        except Exception:
            logger.exception("Failed to get contributors for %s", repo_full_name)
            continue

        if not contributors:
            logger.info("  %s: no contributors (404 or empty)", repo_full_name)
            _update_seed_crawl_stats(db, repo_full_name, 0)
            repos_crawled += 1
            continue

        contributors_found += len(contributors)
        repo_new = 0

        for contrib in contributors:
            github_id = contrib.get("id")
            if not github_id:
                continue

            existing = db.get_candidate_by_github_id(github_id)
            if existing:
                candidate_id = existing["id"]
            else:
                candidate = _contributor_to_candidate(
                    contrib,
                    discovered_via=f"seed:{repo_full_name}",
                    client=client,
                )
                if candidate is None:
                    continue
                candidate_id = db.upsert_candidate(candidate)
                new_candidates += 1
                repo_new += 1

            # Trigger extraction if not recently done
            if not _candidate_recently_extracted(db, candidate_id):
                if _try_extract(db, client, candidate_id):
                    extractions_triggered += 1

        _update_seed_crawl_stats(db, repo_full_name, len(contributors))
        repos_crawled += 1
        logger.info(
            "  %s: found %d contributors, %d new",
            repo_full_name,
            len(contributors),
            repo_new,
        )

    summary = {
        "repos_crawled": repos_crawled,
        "contributors_found": contributors_found,
        "new_candidates": new_candidates,
        "extractions_triggered": extractions_triggered,
    }
    logger.info("crawl_seeds complete: %s", summary)
    return summary


# ============================================================================
# search_and_discover
# ============================================================================

def search_and_discover(
    db: Database,
    client: GitHubClient,
    seeds_path: str,
) -> Dict[str, int]:
    """Search GitHub for new repos and users matching ERP + AI patterns."""
    seeds = _load_seeds(seeds_path)
    queries = seeds.get("search_queries", [])

    # Collect known seed repo names for dedup
    known_seeds: Set[str] = {
        s["full_name"] for s in db.list_seed_repos()
    }

    repos_discovered = 0
    contributors_found = 0
    new_candidates = 0
    extractions_triggered = 0

    # --- Phase 1: repo search ---
    for query in queries:
        logger.info("Searching repos: %r", query)
        try:
            results = client.search_repos(query)
        except GitHubRateLimitError:
            logger.warning("Rate limit hit on repo search — stopping")
            break
        except Exception:
            logger.exception("Repo search failed for query %r", query)
            time.sleep(SEARCH_SLEEP_SECONDS)
            continue

        for repo_data in results:
            full_name = repo_data.get("full_name", "")
            if full_name in known_seeds:
                continue

            # Add as a discovered seed
            db.upsert_seed_repo(full_name, discovered_via=f"search:{query}")
            known_seeds.add(full_name)
            repos_discovered += 1
            logger.info("  New seed from search: %s", full_name)

            # Get contributors for the new seed
            try:
                contributors = client.get_repo_contributors(full_name)
            except GitHubRateLimitError:
                logger.warning("Rate limit hit getting contributors — stopping")
                break
            except Exception:
                logger.exception("Failed to get contributors for %s", full_name)
                continue

            contributors_found += len(contributors)
            _update_seed_crawl_stats(db, full_name, len(contributors))

            for contrib in contributors:
                github_id = contrib.get("id")
                if not github_id:
                    continue

                existing = db.get_candidate_by_github_id(github_id)
                if existing:
                    candidate_id = existing["id"]
                else:
                    candidate = _contributor_to_candidate(
                        contrib,
                        discovered_via=f"search:{query}",
                        client=client,
                    )
                    if candidate is None:
                        continue
                    candidate_id = db.upsert_candidate(candidate)
                    new_candidates += 1

                if not _candidate_recently_extracted(db, candidate_id):
                    if _try_extract(db, client, candidate_id):
                        extractions_triggered += 1

        time.sleep(SEARCH_SLEEP_SECONDS)

    # --- Phase 2: user bio search ---
    bio_keywords = ["netsuite", "sap consultant", "erp ai"]
    for keyword in bio_keywords:
        logger.info("Searching users with bio keyword: %r", keyword)
        try:
            users = client.search_users(keyword)
        except GitHubRateLimitError:
            logger.warning("Rate limit hit on user search — stopping")
            break
        except Exception:
            logger.exception("User search failed for keyword %r", keyword)
            time.sleep(SEARCH_SLEEP_SECONDS)
            continue

        for user_data in users:
            github_id = user_data.get("id")
            if not github_id:
                continue

            existing = db.get_candidate_by_github_id(github_id)
            if existing:
                candidate_id = existing["id"]
            else:
                candidate = _contributor_to_candidate(
                    user_data,
                    discovered_via=f"user_search:{keyword}",
                    client=client,
                )
                if candidate is None:
                    continue
                candidate_id = db.upsert_candidate(candidate)
                new_candidates += 1

            if not _candidate_recently_extracted(db, candidate_id):
                if _try_extract(db, client, candidate_id):
                    extractions_triggered += 1

        time.sleep(SEARCH_SLEEP_SECONDS)

    summary = {
        "repos_discovered": repos_discovered,
        "contributors_found": contributors_found,
        "new_candidates": new_candidates,
        "extractions_triggered": extractions_triggered,
    }
    logger.info("search_and_discover complete: %s", summary)
    return summary


# ============================================================================
# discover_from_stars
# ============================================================================

def discover_from_stars(
    db: Database,
    client: GitHubClient,
    username: str,
) -> Dict[str, int]:
    """Expand seeds by examining repos starred by a known-good candidate."""
    logger.info("Discovering seeds from stars of %s", username)

    # Fetch starred repos via public API method
    try:
        starred = client.get_user_starred_repos(username, max_results=500)
    except GitHubRateLimitError:
        logger.warning("Rate limit hit fetching starred repos for %s", username)
        return {"new_seeds": 0, "new_candidates": 0}
    except Exception:
        logger.exception("Failed to fetch starred repos for %s", username)
        return {"new_seeds": 0, "new_candidates": 0}

    known_seeds = {s["full_name"] for s in db.list_seed_repos()}

    new_seeds = 0
    new_candidates = 0

    for repo_data in starred:
        full_name = repo_data.get("full_name", "")
        if full_name in known_seeds:
            continue

        description = repo_data.get("description")
        topics = repo_data.get("topics", [])

        if not _repo_matches_erp_ai(full_name, description, topics):
            continue

        # Add as seed
        db.upsert_seed_repo(full_name, discovered_via=f"starred_by:{username}")
        known_seeds.add(full_name)
        new_seeds += 1
        logger.info("  New seed from stars: %s", full_name)

        # Get contributors
        try:
            contributors = client.get_repo_contributors(full_name)
        except GitHubRateLimitError:
            logger.warning("Rate limit hit getting contributors — stopping")
            break
        except Exception:
            logger.exception("Failed to get contributors for %s", full_name)
            continue

        _update_seed_crawl_stats(db, full_name, len(contributors))

        for contrib in contributors:
            github_id = contrib.get("id")
            if not github_id:
                continue

            existing = db.get_candidate_by_github_id(github_id)
            if not existing:
                candidate = _contributor_to_candidate(
                    contrib,
                    discovered_via=f"starred_by:{username}",
                    client=client,
                )
                if candidate is None:
                    continue
                db.upsert_candidate(candidate)
                new_candidates += 1

    summary = {
        "new_seeds": new_seeds,
        "new_candidates": new_candidates,
    }
    logger.info("discover_from_stars(%s) complete: %s", username, summary)
    return summary


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sniperscope seed crawler — discover candidates from GitHub",
    )
    parser.add_argument(
        "--seeds",
        action="store_true",
        help="Crawl seed repos and extract all contributors",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Run search queries to discover new repos and candidates",
    )
    parser.add_argument(
        "--discover-stars",
        metavar="USERNAME",
        help="Expand seeds from a user's starred repos",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (default: from config)",
    )
    parser.add_argument(
        "--seeds-path",
        default=config.SEEDS_PATH,
        help="Path to seeds.json",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not (args.seeds or args.search or args.discover_stars):
        parser.error("Specify at least one of --seeds, --search, or --discover-stars USERNAME")

    db = Database(db_path=args.db_path)
    client = GitHubClient()

    try:
        if args.seeds:
            result = crawl_seeds(db, client, args.seeds_path)
            print(json.dumps(result, indent=2))

        if args.search:
            result = search_and_discover(db, client, args.seeds_path)
            print(json.dumps(result, indent=2))

        if args.discover_stars:
            result = discover_from_stars(db, client, args.discover_stars)
            print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
