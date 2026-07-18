"""arXiv paper search — discovers researchers at the ERP + AI intersection."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode

import requests

from sniperscope import config
from sniperscope.db import Database
from sniperscope.models import DOMAIN_KEYWORDS, Candidate, EvidenceFact, ExtractionRun

logger = logging.getLogger(__name__)

ARXIV_API_URL = "http://export.arxiv.org/api/query"
ARXIV_RATE_LIMIT_SECONDS = 3  # arXiv asks for max 1 request per 3 seconds

# Atom XML namespaces
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"

# Search queries targeting the ERP + AI/LLM intersection
SEARCH_QUERIES = [
    '"enterprise resource planning" AND ("large language model" OR "LLM" OR "AI agent")',
    '"ERP" AND ("language model" OR "artificial intelligence" OR "automation")',
    '"NetSuite" OR "SAP" AND "machine learning"',
    '"business process" AND ("AI agent" OR "LLM" OR "Claude" OR "GPT")',
    '"ERP migration" AND "artificial intelligence"',
    '"enterprise software" AND ("agent" OR "automation" OR "LLM")',
]


# ----------------------------------------------------------------------
# XML parsing
# ----------------------------------------------------------------------

def _ns(tag: str, ns: str = ATOM_NS) -> str:
    return "{{{}}}{}".format(ns, tag)


def _extract_arxiv_id(entry_id: str) -> str:
    """'http://arxiv.org/abs/2401.12345v2' -> '2401.12345v2'."""
    match = re.search(r"abs/(.+)$", entry_id)
    return match.group(1) if match else entry_id


def _text(entry: ET.Element, tag: str) -> str:
    el = entry.find(_ns(tag))
    return " ".join((el.text or "").split()) if el is not None else ""


def parse_arxiv_response(xml_text: str) -> List[Dict[str, Any]]:
    """Parse an arXiv Atom response into paper dicts.

    Each dict contains: title, authors, abstract, published, arxiv_id,
    categories, pdf_url. Entries without a title or authors are skipped.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.error("Failed to parse arXiv XML response")
        return []

    papers = []
    for entry in root.findall(_ns("entry")):
        title = _text(entry, "title")
        abstract = _text(entry, "summary")

        authors = [
            name_el.text.strip()
            for author_el in entry.findall(_ns("author"))
            for name_el in [author_el.find(_ns("name"))]
            if name_el is not None and name_el.text
        ]

        published_el = entry.find(_ns("published"))
        published = (published_el.text or "").strip() if published_el is not None else ""

        id_el = entry.find(_ns("id"))
        arxiv_id = _extract_arxiv_id((id_el.text or "").strip() if id_el is not None else "")

        # arXiv returns categories in both namespaces
        categories: List[str] = []
        for ns in (ARXIV_NS, ATOM_NS):
            for cat_el in entry.findall(_ns("category", ns)):
                term = cat_el.get("term")
                if term and term not in categories:
                    categories.append(term)

        pdf_url = next(
            (link_el.get("href", "") for link_el in entry.findall(_ns("link"))
             if link_el.get("title") == "pdf"),
            "",
        )

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


# ----------------------------------------------------------------------
# arXiv search
# ----------------------------------------------------------------------

def _build_query_url(query: str, start: int = 0, max_results: int = 50) -> str:
    params = {
        "search_query": "all:{}".format(query),
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    return "{}?{}".format(ARXIV_API_URL, urlencode(params))


def search_arxiv(query: str, max_results: int = 50,
                 sleep_fn: Any = time.sleep) -> List[Dict[str, Any]]:
    """Search arXiv for papers matching the query.

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


def search_all_queries(max_results_per_query: int = 50,
                       sleep_fn: Any = time.sleep) -> List[Dict[str, Any]]:
    """Run all predefined queries and deduplicate results by arXiv ID."""
    seen_ids: Set[str] = set()
    all_papers: List[Dict[str, Any]] = []

    for i, query in enumerate(SEARCH_QUERIES):
        for paper in search_arxiv(query, max_results=max_results_per_query,
                                  sleep_fn=sleep_fn):
            if paper["arxiv_id"] not in seen_ids:
                seen_ids.add(paper["arxiv_id"])
                all_papers.append(paper)

        if i < len(SEARCH_QUERIES) - 1:
            sleep_fn(ARXIV_RATE_LIMIT_SECONDS)

    logger.info("Total unique papers found: %d", len(all_papers))
    return all_papers


# ----------------------------------------------------------------------
# Abstract keywords
# ----------------------------------------------------------------------

def extract_abstract_keywords(abstract: str) -> List[str]:
    """Return DOMAIN_KEYWORDS terms that appear in the abstract (counting only)."""
    abstract_lower = abstract.lower()
    return sorted({
        term for terms in DOMAIN_KEYWORDS.values() for term in terms
        if term in abstract_lower
    })


# ----------------------------------------------------------------------
# GitHub cross-reference
# ----------------------------------------------------------------------

def cross_ref_github(author_name: str,
                     github_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Search GitHub for a user matching the author name.

    Returns the best-match profile (GitHub's relevance ranking) or None.
    """
    from sniperscope.github import GitHubClient

    client = GitHubClient(token=github_token or config.GITHUB_TOKEN)

    try:
        users = client.search_users(author_name, max_results=5)
    except Exception:
        logger.debug("GitHub user search failed for %r", author_name)
        return None

    login = users[0].get("login") if users else None
    return client.get_user(login) if login else None


# ----------------------------------------------------------------------
# Database storage
# ----------------------------------------------------------------------

def _synthetic_login(author_name: str) -> str:
    return "arxiv:{}".format(author_name.lower().replace(" ", "_"))


def _make_author_candidate(author_name: str, arxiv_id: str,
                           github_profile: Optional[Dict[str, Any]]) -> Candidate:
    """Create a Candidate for a paper author.

    With a cross-referenced GitHub profile, use its data. Otherwise create a
    placeholder with a deterministic negative synthetic github_id — hashlib
    rather than hash(), which varies across processes (PYTHONHASHSEED).
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
            discovered_via="arxiv:{}".format(arxiv_id),
        )

    digest = hashlib.sha256(author_name.encode("utf-8")).hexdigest()
    synthetic_id = -int(digest[:12], 16) % (10 ** 15)
    return Candidate(
        github_id=-synthetic_id if synthetic_id > 0 else synthetic_id,
        github_login=_synthetic_login(author_name),
        display_name=author_name,
        discovered_via="arxiv:{}".format(arxiv_id),
    )


def _paper_to_facts(paper: Dict[str, Any], candidate_id: str,
                    run_id: str) -> List[EvidenceFact]:
    """Convert a parsed paper dict into evidence facts for one author."""
    source = "arxiv:{}".format(paper["arxiv_id"])

    def fact(key: str, value: str, fact_type: str) -> EvidenceFact:
        return EvidenceFact(
            candidate_id=candidate_id,
            category="research_paper",
            fact_key=key,
            fact_value=value,
            fact_type=fact_type,
            source=source,
            extraction_run_id=run_id,
        )

    facts = [fact("paper:title", paper["title"], "string")]

    keywords = extract_abstract_keywords(paper["abstract"])
    if keywords:
        facts.append(fact("paper:abstract_keywords", json.dumps(keywords), "json"))
    if paper["categories"]:
        facts.append(fact("paper:category", json.dumps(paper["categories"]), "json"))

    facts.append(fact("paper:coauthor_count", str(len(paper["authors"])), "number"))

    if paper["published"]:
        facts.append(fact("paper:published_date", paper["published"], "string"))
    if paper["pdf_url"]:
        facts.append(fact("paper:pdf_url", paper["pdf_url"], "string"))

    return facts


def _resolve_author(db: Database, author_name: str, arxiv_id: str,
                    cross_ref_enabled: bool, github_token: Optional[str],
                    stats: Dict[str, int]) -> str:
    """Find or create the candidate record for a paper author.

    Resolution order: existing synthetic-login candidate, then (optionally)
    GitHub cross-reference to an existing or new profile-backed candidate,
    then a synthetic placeholder. Returns the candidate id.
    """
    existing = db.get_candidate_by_login(_synthetic_login(author_name))
    if existing:
        return existing["id"]

    github_profile = None
    if cross_ref_enabled:
        github_profile = cross_ref_github(author_name, github_token=github_token)
        if github_profile:
            stats["authors_cross_reffed"] += 1
            existing_gh = db.get_candidate_by_github_id(github_profile["id"])
            if existing_gh:
                return existing_gh["id"]

    candidate = _make_author_candidate(author_name, arxiv_id, github_profile)
    candidate_id = db.upsert_candidate(candidate)
    stats["new_candidates"] += 1
    return candidate_id


def store_papers(db: Database, papers: List[Dict[str, Any]],
                 cross_ref_github_enabled: bool = False,
                 github_token: Optional[str] = None) -> Dict[str, int]:
    """Store parsed papers and their authors in the database.

    For each (paper, author): resolve the author to a candidate and store
    the paper's evidence facts against them. Returns summary stats.
    """
    run_id = db.create_extraction_run(ExtractionRun(trigger_type="arxiv_search"))

    stats = {
        "papers_processed": 0,
        "new_candidates": 0,
        "facts_stored": 0,
        "authors_cross_reffed": 0,
    }
    seen_authors: Dict[str, str] = {}  # author_name -> candidate_id
    start_time = time.time()

    for paper in papers:
        for author_name in paper["authors"]:
            candidate_id = seen_authors.get(author_name)
            if candidate_id is None:
                candidate_id = _resolve_author(
                    db, author_name, paper["arxiv_id"],
                    cross_ref_github_enabled, github_token, stats,
                )
                seen_authors[author_name] = candidate_id

            facts = _paper_to_facts(paper, candidate_id, run_id)
            db.insert_facts_batch(facts)
            stats["facts_stored"] += len(facts)

        stats["papers_processed"] += 1

    db.complete_extraction_run(
        run_id,
        repos_scanned=0,
        facts_extracted=stats["facts_stored"],
        duration_ms=int((time.time() - start_time) * 1000),
        status="completed",
    )

    logger.info("store_papers complete: %s", stats)
    return stats


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sniperscope arXiv search — discover researchers at the "
                    "ERP + AI intersection",
    )
    parser.add_argument("--search", action="store_true",
                        help="Run all predefined search queries against arXiv")
    parser.add_argument("--db-path", default=None,
                        help="SQLite database path (default: from config)")
    parser.add_argument("--max-results", type=int, default=50,
                        help="Max papers per query (default: 50)")
    parser.add_argument("--cross-ref-github", action="store_true",
                        help="Also search GitHub for author profiles "
                             "(requires GITHUB_TOKEN)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.search:
        parser.error("Specify --search to run arXiv search queries")

    with Database(db_path=args.db_path) as db:
        papers = search_all_queries(max_results_per_query=args.max_results)
        result = store_papers(db, papers,
                              cross_ref_github_enabled=args.cross_ref_github)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
