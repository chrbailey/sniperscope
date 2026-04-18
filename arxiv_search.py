"""arXiv paper search — discovers researchers at the ERP + AI intersection."""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode

import requests

import config
from db import Database
from models import Candidate, EvidenceFact, ExtractionRun

logger = logging.getLogger(__name__)

# arXiv API
ARXIV_API_URL = "http://export.arxiv.org/api/query"
ARXIV_RATE_LIMIT_SECONDS = 3  # arXiv asks for max 1 request per 3 seconds

# Atom XML namespace
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"

# Search queries targeting ERP + AI/LLM intersection
SEARCH_QUERIES = [
    '"enterprise resource planning" AND ("large language model" OR "LLM" OR "AI agent")',
    '"ERP" AND ("language model" OR "artificial intelligence" OR "automation")',
    '"NetSuite" OR "SAP" AND "machine learning"',
    '"business process" AND ("AI agent" OR "LLM" OR "Claude" OR "GPT")',
    '"ERP migration" AND "artificial intelligence"',
    '"enterprise software" AND ("agent" OR "automation" OR "LLM")',
]


# ============================================================================
# XML Parsing
# ============================================================================

def _ns(tag: str, ns: str = ATOM_NS) -> str:
    """Build a namespaced XML tag."""
    return f"{{{ns}}}{tag}"


def _extract_arxiv_id(entry_id: str) -> str:
    """Extract the arXiv paper ID from the entry URL.

    Example: 'http://arxiv.org/abs/2401.12345v2' -> '2401.12345v2'
    """
    match = re.search(r"abs/(.+)$", entry_id)
    return match.group(1) if match else entry_id


def parse_arxiv_response(xml_text: str) -> List[Dict[str, Any]]:
    """Parse arXiv Atom XML response into a list of paper dicts.

    Each dict contains: title, authors, abstract, published, arxiv_id,
    categories, pdf_url.
    """
    papers = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.error("Failed to parse arXiv XML response")
        return papers

    for entry in root.findall(_ns("entry")):
        # Title — strip newlines and extra whitespace
        title_el = entry.find(_ns("title"))
        title = " ".join((title_el.text or "").split()) if title_el is not None else ""

        # Abstract / summary
        summary_el = entry.find(_ns("summary"))
        abstract = " ".join((summary_el.text or "").split()) if summary_el is not None else ""

        # Authors
        authors = []
        for author_el in entry.findall(_ns("author")):
            name_el = author_el.find(_ns("name"))
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        # Published date
        published_el = entry.find(_ns("published"))
        published = (published_el.text or "").strip() if published_el is not None else ""

        # arXiv ID from the entry id URL
        id_el = entry.find(_ns("id"))
        entry_id = (id_el.text or "").strip() if id_el is not None else ""
        arxiv_id = _extract_arxiv_id(entry_id)

        # Categories
        categories = []
        for cat_el in entry.findall(_ns("category", ARXIV_NS)):
            term = cat_el.get("term")
            if term:
                categories.append(term)
        # Also check Atom-namespaced categories (arXiv returns both)
        for cat_el in entry.findall(_ns("category")):
            term = cat_el.get("term")
            if term and term not in categories:
                categories.append(term)

        # PDF URL
        pdf_url = ""
        for link_el in entry.findall(_ns("link")):
            if link_el.get("title") == "pdf":
                pdf_url = link_el.get("href", "")
                break

        if not title or not authors:
            continue

        papers.append({
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "published": published,
            "arxiv_id": arxiv_id,
            "categories": categories,
            "pdf_url": pdf_url,
        })

    return papers


# ============================================================================
# arXiv Search
# ============================================================================

def _build_query_url(query: str, start: int = 0, max_results: int = 50) -> str:
    """Build the arXiv API query URL."""
    params = {
        "search_query": f"all:{query}",
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    return f"{ARXIV_API_URL}?{urlencode(params)}"


def search_arxiv(
    query: str,
    max_results: int = 50,
    sleep_fn: Any = time.sleep,
) -> List[Dict[str, Any]]:
    """Search arXiv for papers matching the query.

    Returns a list of paper dicts parsed from the API response.
    sleep_fn is injectable for testing.
    """
    url = _build_query_url(query, max_results=max_results)
    logger.info("Searching arXiv: %s", query)

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("arXiv API request failed for query %r: %s", query, e)
        return []

    papers = parse_arxiv_response(response.text)
    logger.info("  Found %d papers for query %r", len(papers), query)
    return papers


def search_all_queries(
    max_results_per_query: int = 50,
    sleep_fn: Any = time.sleep,
) -> List[Dict[str, Any]]:
    """Run all predefined search queries and deduplicate results by arXiv ID."""
    seen_ids: Set[str] = set()
    all_papers: List[Dict[str, Any]] = []

    for i, query in enumerate(SEARCH_QUERIES):
        papers = search_arxiv(query, max_results=max_results_per_query, sleep_fn=sleep_fn)

        for paper in papers:
            if paper["arxiv_id"] not in seen_ids:
                seen_ids.add(paper["arxiv_id"])
                all_papers.append(paper)

        # Rate limit: wait between queries (not after the last one)
        if i < len(SEARCH_QUERIES) - 1:
            sleep_fn(ARXIV_RATE_LIMIT_SECONDS)

    logger.info("Total unique papers found: %d", len(all_papers))
    return all_papers


# ============================================================================
# Abstract Keyword Extraction
# ============================================================================

def extract_abstract_keywords(abstract: str) -> List[str]:
    """Extract domain-relevant keywords from an abstract.

    Scans against DOMAIN_KEYWORDS from models.py and returns matched terms.
    """
    from models import DOMAIN_KEYWORDS

    abstract_lower = abstract.lower()
    matched = []
    for group, terms in DOMAIN_KEYWORDS.items():
        for term in terms:
            if term in abstract_lower:
                matched.append(term)
    return sorted(set(matched))


# ============================================================================
# GitHub Cross-Reference
# ============================================================================

def cross_ref_github(
    author_name: str,
    github_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Search GitHub for a user matching the author name.

    Returns the best-match GitHub user profile or None.
    """
    from github_client import GitHubClient

    client = GitHubClient(token=github_token or config.GITHUB_TOKEN)

    try:
        users = client.search_users(author_name, max_results=5)
    except Exception:
        logger.debug("GitHub user search failed for %r", author_name)
        return None

    if not users:
        return None

    # Return the first match — GitHub's relevance ranking does the work
    best = users[0]
    login = best.get("login")
    if not login:
        return None

    # Enrich with full profile
    profile = client.get_user(login)
    return profile


# ============================================================================
# Database Storage
# ============================================================================

def _make_author_candidate(
    author_name: str,
    arxiv_id: str,
    github_profile: Optional[Dict[str, Any]],
) -> Candidate:
    """Create a Candidate record for a paper author.

    If we found a GitHub profile via cross-reference, use that data.
    Otherwise, create a minimal candidate with a synthetic github_id.
    """
    if github_profile:
        return Candidate(
            github_id=github_profile["id"],
            github_login=github_profile["login"],
            display_name=github_profile.get("name") or author_name,
            email=github_profile.get("email"),
            bio=github_profile.get("bio"),
            company=github_profile.get("company"),
            location=github_profile.get("location"),
            avatar_url=github_profile.get("avatar_url"),
            public_repos=github_profile.get("public_repos"),
            followers=github_profile.get("followers"),
            following=github_profile.get("following"),
            github_created_at=github_profile.get("created_at"),
            discovered_via=f"arxiv:{arxiv_id}",
        )
    else:
        # No GitHub profile — create a placeholder candidate.
        # Deterministic synthetic github_id: sha256(name)[:8] as signed negative int.
        # Using hashlib instead of hash() — the builtin is non-deterministic
        # across processes (PYTHONHASHSEED) and would give the same author
        # different IDs on successive runs.
        import hashlib
        digest = hashlib.sha256(author_name.encode("utf-8")).hexdigest()
        synthetic_id = -int(digest[:12], 16) % (10**15)
        return Candidate(
            github_id=-synthetic_id if synthetic_id > 0 else synthetic_id,
            github_login=f"arxiv:{author_name.lower().replace(' ', '_')}",
            display_name=author_name,
            discovered_via=f"arxiv:{arxiv_id}",
        )


def _paper_to_facts(
    paper: Dict[str, Any],
    candidate_id: str,
    run_id: str,
) -> List[EvidenceFact]:
    """Convert a parsed paper dict into evidence facts."""
    source = f"arxiv:{paper['arxiv_id']}"
    facts = []

    facts.append(EvidenceFact(
        candidate_id=candidate_id,
        category="research_paper",
        fact_key="paper:title",
        fact_value=paper["title"],
        fact_type="string",
        source=source,
        extraction_run_id=run_id,
    ))

    keywords = extract_abstract_keywords(paper["abstract"])
    if keywords:
        facts.append(EvidenceFact(
            candidate_id=candidate_id,
            category="research_paper",
            fact_key="paper:abstract_keywords",
            fact_value=json.dumps(keywords),
            fact_type="json",
            source=source,
            extraction_run_id=run_id,
        ))

    if paper["categories"]:
        facts.append(EvidenceFact(
            candidate_id=candidate_id,
            category="research_paper",
            fact_key="paper:category",
            fact_value=json.dumps(paper["categories"]),
            fact_type="json",
            source=source,
            extraction_run_id=run_id,
        ))

    facts.append(EvidenceFact(
        candidate_id=candidate_id,
        category="research_paper",
        fact_key="paper:coauthor_count",
        fact_value=str(len(paper["authors"])),
        fact_type="number",
        source=source,
        extraction_run_id=run_id,
    ))

    if paper["published"]:
        facts.append(EvidenceFact(
            candidate_id=candidate_id,
            category="research_paper",
            fact_key="paper:published_date",
            fact_value=paper["published"],
            fact_type="string",
            source=source,
            extraction_run_id=run_id,
        ))

    if paper["pdf_url"]:
        facts.append(EvidenceFact(
            candidate_id=candidate_id,
            category="research_paper",
            fact_key="paper:pdf_url",
            fact_value=paper["pdf_url"],
            fact_type="string",
            source=source,
            extraction_run_id=run_id,
        ))

    return facts


def store_papers(
    db: Database,
    papers: List[Dict[str, Any]],
    cross_ref_github_enabled: bool = False,
    github_token: Optional[str] = None,
) -> Dict[str, int]:
    """Store parsed papers and their authors in the database.

    For each paper:
      - For each author, create/update a candidate record
      - Store paper evidence facts linked to each author

    Returns summary stats.
    """
    run = ExtractionRun(
        trigger_type="arxiv_search",
    )
    run_id = db.create_extraction_run(run)

    new_candidates = 0
    facts_stored = 0
    papers_processed = 0
    authors_cross_reffed = 0
    seen_authors: Dict[str, str] = {}  # author_name -> candidate_id

    start_time = time.time()

    for paper in papers:
        for author_name in paper["authors"]:
            if author_name in seen_authors:
                candidate_id = seen_authors[author_name]
            else:
                # Check if author already exists by a synthetic login
                synthetic_login = f"arxiv:{author_name.lower().replace(' ', '_')}"
                existing = db.get_candidate_by_login(synthetic_login)

                github_profile = None
                if existing:
                    candidate_id = existing["id"]
                else:
                    # Try GitHub cross-reference if enabled
                    if cross_ref_github_enabled:
                        github_profile = cross_ref_github(
                            author_name, github_token=github_token
                        )
                        if github_profile:
                            authors_cross_reffed += 1
                            # Check if this GitHub user already exists
                            existing_gh = db.get_candidate_by_github_id(
                                github_profile["id"]
                            )
                            if existing_gh:
                                candidate_id = existing_gh["id"]
                                seen_authors[author_name] = candidate_id
                                # Still store facts for this paper
                                facts = _paper_to_facts(paper, candidate_id, run_id)
                                db.insert_facts_batch(facts)
                                facts_stored += len(facts)
                                continue

                    candidate = _make_author_candidate(
                        author_name, paper["arxiv_id"], github_profile
                    )
                    candidate_id = db.upsert_candidate(candidate)
                    new_candidates += 1

                seen_authors[author_name] = candidate_id

            # Store evidence facts for this paper/author
            facts = _paper_to_facts(paper, candidate_id, run_id)
            db.insert_facts_batch(facts)
            facts_stored += len(facts)

        papers_processed += 1

    duration_ms = int((time.time() - start_time) * 1000)
    db.complete_extraction_run(
        run_id,
        repos_scanned=0,
        facts_extracted=facts_stored,
        duration_ms=duration_ms,
        status="completed",
    )

    summary = {
        "papers_processed": papers_processed,
        "new_candidates": new_candidates,
        "facts_stored": facts_stored,
        "authors_cross_reffed": authors_cross_reffed,
    }
    logger.info("store_papers complete: %s", summary)
    return summary


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sniperscope arXiv search — discover researchers at the ERP + AI intersection",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Run all predefined search queries against arXiv",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (default: from config)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="Max papers per query (default: 50)",
    )
    parser.add_argument(
        "--cross-ref-github",
        action="store_true",
        help="Also search GitHub for author profiles (requires GITHUB_TOKEN)",
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

    if not args.search:
        parser.error("Specify --search to run arXiv search queries")

    db = Database(db_path=args.db_path)

    try:
        papers = search_all_queries(
            max_results_per_query=args.max_results,
        )
        result = store_papers(
            db,
            papers,
            cross_ref_github_enabled=args.cross_ref_github,
        )
        print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
