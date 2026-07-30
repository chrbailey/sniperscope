"""Tests for arXiv search module — all HTTP calls mocked."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import responses

from sniperscope import config
from sniperscope.arxiv import (
    ARXIV_API_URL,
    SEARCH_QUERIES,
    _build_query_url,
    _extract_arxiv_id,
    _make_author_candidate,
    _paper_to_facts,
    cross_ref_github,
    extract_abstract_keywords,
    parse_arxiv_response,
    search_all_queries,
    search_arxiv,
    store_papers,
)
from sniperscope.models import EvidenceFact


# ============================================================================
# Sample arXiv XML response (realistic Atom format)
# ============================================================================

SAMPLE_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query: all:ERP AND LLM</title>
  <id>http://arxiv.org/api/query</id>
  <totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">2</totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>Large Language Models for Enterprise Resource Planning:
      A Survey</title>
    <summary>This paper surveys the application of large language models (LLMs)
      to enterprise resource planning (ERP) systems, including SAP and NetSuite
      integrations. We examine how AI agents can automate business processes
      and improve compliance with SOX regulations.</summary>
    <author><name>Alice Researcher</name></author>
    <author><name>Bob Scientist</name></author>
    <author><name>Carol Engineer</name></author>
    <published>2024-01-15T18:00:00Z</published>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.SE" scheme="http://arxiv.org/schemas/atom"/>
    <link href="http://arxiv.org/abs/2401.12345v1" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/2401.12345v1" title="pdf" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2403.67890v2</id>
    <title>Automating SAP Workflows with Claude and GPT Agents</title>
    <summary>We present a framework for automating SAP ABAP workflows using
      Claude and GPT-based AI agents. Our approach uses the model context
      protocol (MCP) to bridge ERP transactions with language model tooling.</summary>
    <author><name>Dave Coder</name></author>
    <published>2024-03-20T12:00:00Z</published>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <link href="http://arxiv.org/abs/2403.67890v2" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/2403.67890v2" title="pdf" rel="related" type="application/pdf"/>
  </entry>
</feed>"""

EMPTY_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query: all:xyznonexistent</title>
  <id>http://arxiv.org/api/query</id>
  <totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</totalResults>
</feed>"""

MALFORMED_XML = """<this is not valid xml at all"""


GITHUB_BASE = config.GITHUB_API_BASE


# ============================================================================
# test_parse_arxiv_response
# ============================================================================

class TestParseArxivResponse:

    def test_parse_two_papers(self):
        """Parsing the sample XML extracts both papers with all fields."""
        papers = parse_arxiv_response(SAMPLE_ARXIV_XML)

        assert len(papers) == 2

        # First paper
        p1 = papers[0]
        assert "Large Language Models" in p1["title"]
        assert "Enterprise Resource Planning" in p1["title"]
        assert p1["authors"] == ["Alice Researcher", "Bob Scientist", "Carol Engineer"]
        assert "ERP" in p1["abstract"]
        assert "SAP" in p1["abstract"]
        assert p1["published"] == "2024-01-15T18:00:00Z"
        assert p1["arxiv_id"] == "2401.12345v1"
        assert "cs.AI" in p1["categories"]
        assert p1["pdf_url"] == "http://arxiv.org/pdf/2401.12345v1"

        # Second paper
        p2 = papers[1]
        assert "SAP Workflows" in p2["title"]
        assert p2["authors"] == ["Dave Coder"]
        assert p2["arxiv_id"] == "2403.67890v2"

    def test_parse_empty_response(self):
        """An empty arXiv response (no entries) returns an empty list."""
        papers = parse_arxiv_response(EMPTY_ARXIV_XML)
        assert papers == []

    def test_parse_malformed_xml(self):
        """Malformed XML is handled gracefully, returning an empty list."""
        papers = parse_arxiv_response(MALFORMED_XML)
        assert papers == []

    def test_title_whitespace_normalized(self):
        """Multi-line titles are collapsed into single-line strings."""
        papers = parse_arxiv_response(SAMPLE_ARXIV_XML)
        # The sample XML has a newline in the first title
        assert "\n" not in papers[0]["title"]


# ============================================================================
# test_search_queries_constructed_correctly
# ============================================================================

class TestSearchQueryConstruction:

    def test_build_query_url_contains_query(self):
        """The built URL includes the search query and parameters."""
        url = _build_query_url('"ERP" AND "LLM"', start=0, max_results=25)
        assert ARXIV_API_URL in url
        assert "max_results=25" in url
        assert "start=0" in url
        assert "sortBy=submittedDate" in url

    def test_all_queries_are_strings(self):
        """All predefined search queries are non-empty strings."""
        assert len(SEARCH_QUERIES) >= 6
        for q in SEARCH_QUERIES:
            assert isinstance(q, str)
            assert len(q) > 0

    @responses.activate
    def test_search_arxiv_sends_correct_request(self):
        """search_arxiv makes a GET to the arXiv API with the right query."""
        responses.add(
            responses.GET,
            ARXIV_API_URL,
            body=EMPTY_ARXIV_XML,
            status=200,
        )

        papers = search_arxiv('"ERP" AND "LLM"', max_results=10)

        assert len(responses.calls) == 1
        request_url = responses.calls[0].request.url
        assert "search_query=" in request_url
        assert "max_results=10" in request_url
        assert papers == []


# ============================================================================
# test_author_extraction
# ============================================================================

class TestAuthorExtraction:

    def test_multiple_authors_extracted(self):
        """All authors are extracted from a multi-author paper."""
        papers = parse_arxiv_response(SAMPLE_ARXIV_XML)
        p1 = papers[0]
        assert len(p1["authors"]) == 3
        assert "Alice Researcher" in p1["authors"]
        assert "Bob Scientist" in p1["authors"]
        assert "Carol Engineer" in p1["authors"]

    def test_single_author_extracted(self):
        """A single-author paper correctly extracts one author."""
        papers = parse_arxiv_response(SAMPLE_ARXIV_XML)
        p2 = papers[1]
        assert len(p2["authors"]) == 1
        assert p2["authors"][0] == "Dave Coder"

    def test_arxiv_id_extraction(self):
        """arXiv IDs are correctly extracted from entry URLs."""
        assert _extract_arxiv_id("http://arxiv.org/abs/2401.12345v1") == "2401.12345v1"
        assert _extract_arxiv_id("http://arxiv.org/abs/2403.67890v2") == "2403.67890v2"
        assert _extract_arxiv_id("http://arxiv.org/abs/cs/0601001v1") == "cs/0601001v1"

    def test_abstract_keyword_extraction(self):
        """Keywords from DOMAIN_KEYWORDS are found in the abstract."""
        abstract = (
            "This paper examines SAP ABAP workflows and how Claude-based "
            "AI agents can automate ERP compliance with SOX regulations."
        )
        keywords = extract_abstract_keywords(abstract)
        assert "sap" in keywords
        assert "abap" in keywords
        assert "claude" in keywords
        assert "erp" in keywords
        assert "agent" in keywords


# ============================================================================
# test_cross_ref_github_creates_candidate
# ============================================================================

class TestCrossRefGitHub:

    @responses.activate
    def test_cross_ref_finds_github_user(self):
        """When GitHub search returns a match, cross_ref returns a profile."""
        # Mock the search endpoint
        responses.add(
            responses.GET,
            f"{GITHUB_BASE}/search/users",
            json={
                "total_count": 1,
                "items": [{"login": "alice_r", "id": 54321}],
            },
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )
        # Mock the user profile endpoint
        responses.add(
            responses.GET,
            f"{GITHUB_BASE}/users/alice_r",
            json={
                "login": "alice_r",
                "id": 54321,
                "name": "Alice Researcher",
                "bio": "ML researcher focusing on enterprise AI",
                "public_repos": 15,
                "followers": 200,
            },
            status=200,
            headers={"X-RateLimit-Remaining": "4998"},
        )

        profile = cross_ref_github("Alice Researcher", github_token="fake-token")

        assert profile is not None
        assert profile["login"] == "alice_r"
        assert profile["id"] == 54321

    @responses.activate
    def test_cross_ref_no_github_match(self):
        """When GitHub search returns no results, cross_ref returns None."""
        responses.add(
            responses.GET,
            f"{GITHUB_BASE}/search/users",
            json={"total_count": 0, "items": []},
            status=200,
            headers={"X-RateLimit-Remaining": "4999"},
        )

        profile = cross_ref_github("Unknown Author", github_token="fake-token")
        assert profile is None

    def test_make_candidate_with_github_profile(self):
        """When a GitHub profile is available, the candidate uses that data."""
        github_profile = {
            "login": "alice_r",
            "id": 54321,
            "name": "Alice Researcher",
            "email": "alice@example.com",
            "bio": "ML researcher",
            "company": "University",
            "location": "Boston",
            "avatar_url": "https://avatars.githubusercontent.com/u/54321",
            "public_repos": 15,
            "followers": 200,
            "following": 30,
            "created_at": "2020-01-01T00:00:00Z",
        }

        candidate = _make_author_candidate(
            "Alice Researcher", "2401.12345v1", github_profile
        )

        assert candidate.github_id == 54321
        assert candidate.github_login == "alice_r"
        assert candidate.display_name == "Alice Researcher"
        assert candidate.discovered_via == "arxiv:2401.12345v1"

    def test_make_candidate_without_github_profile(self):
        """Without a GitHub profile, a synthetic candidate is created."""
        candidate = _make_author_candidate(
            "Bob Scientist", "2401.12345v1", None
        )

        assert candidate.github_id < 0  # synthetic negative ID
        assert candidate.github_login == "arxiv:bob_scientist"
        assert candidate.display_name == "Bob Scientist"
        assert candidate.discovered_via == "arxiv:2401.12345v1"


# ============================================================================
# test_rate_limiting
# ============================================================================

class TestRateLimiting:

    @responses.activate
    def test_sleep_called_between_queries(self):
        """search_all_queries calls sleep between each query for rate limiting."""
        # Mock arXiv for all queries
        for _ in SEARCH_QUERIES:
            responses.add(
                responses.GET,
                ARXIV_API_URL,
                body=EMPTY_ARXIV_XML,
                status=200,
            )

        mock_sleep = MagicMock()
        search_all_queries(max_results_per_query=5, sleep_fn=mock_sleep)

        # Should sleep between queries (N-1 times for N queries)
        assert mock_sleep.call_count == len(SEARCH_QUERIES) - 1
        for call in mock_sleep.call_args_list:
            assert call[0][0] == 3  # 3-second delay

    @responses.activate
    def test_no_sleep_after_last_query(self):
        """No sleep is added after the final query."""
        for _ in SEARCH_QUERIES:
            responses.add(
                responses.GET,
                ARXIV_API_URL,
                body=EMPTY_ARXIV_XML,
                status=200,
            )

        mock_sleep = MagicMock()
        search_all_queries(max_results_per_query=5, sleep_fn=mock_sleep)

        # If N queries, we sleep N-1 times (not after the last)
        expected_sleeps = len(SEARCH_QUERIES) - 1
        assert mock_sleep.call_count == expected_sleeps


# ============================================================================
# test_store_papers (integration with DB)
# ============================================================================

class TestStorePapers:

    def test_store_creates_candidates_and_facts(self, db):
        """Storing papers creates candidate records and evidence facts."""
        papers = parse_arxiv_response(SAMPLE_ARXIV_XML)
        result = store_papers(db, papers, cross_ref_github_enabled=False)

        # 2 papers: paper 1 has 3 authors, paper 2 has 1 author = 4 unique authors
        assert result["papers_processed"] == 2
        assert result["new_candidates"] == 4
        assert result["facts_stored"] > 0

        # Verify candidates exist in DB
        candidates = db.list_candidates()
        assert len(candidates) == 4

        # Verify evidence facts exist
        for c in candidates:
            facts = db.get_facts_for_candidate(c["id"])
            assert len(facts) > 0
            # All facts should be in research_paper category
            for f in facts:
                assert f["category"] == "research_paper"

    def test_store_deduplicates_authors_across_papers(self, db):
        """Same author appearing in multiple papers only creates one candidate."""
        # Create two papers with overlapping authors
        papers = [
            {
                "title": "Paper One",
                "authors": ["Shared Author", "Unique A"],
                "abstract": "About ERP and SAP automation",
                "published": "2024-01-01T00:00:00Z",
                "arxiv_id": "2401.00001v1",
                "categories": ["cs.AI"],
                "pdf_url": "http://arxiv.org/pdf/2401.00001v1",
            },
            {
                "title": "Paper Two",
                "authors": ["Shared Author", "Unique B"],
                "abstract": "About NetSuite and Claude agents",
                "published": "2024-02-01T00:00:00Z",
                "arxiv_id": "2401.00002v1",
                "categories": ["cs.SE"],
                "pdf_url": "http://arxiv.org/pdf/2401.00002v1",
            },
        ]

        result = store_papers(db, papers, cross_ref_github_enabled=False)

        # 3 unique authors, not 4
        assert result["new_candidates"] == 3
        assert result["papers_processed"] == 2

    def test_paper_facts_include_expected_keys(self, db):
        """Evidence facts contain the expected fact_keys for a paper."""
        papers = parse_arxiv_response(SAMPLE_ARXIV_XML)
        store_papers(db, papers, cross_ref_github_enabled=False)

        # Check first candidate's facts
        candidates = db.list_candidates()
        first_candidate = candidates[0]
        facts = db.get_facts_for_candidate(first_candidate["id"])

        fact_keys = {f["fact_key"] for f in facts}
        assert "paper:title" in fact_keys
        assert "paper:coauthor_count" in fact_keys
        assert "paper:published_date" in fact_keys

    def test_extraction_run_created_and_completed(self, db):
        """An extraction run is created with status 'completed'."""
        papers = parse_arxiv_response(SAMPLE_ARXIV_XML)
        store_papers(db, papers, cross_ref_github_enabled=False)

        runs = db.conn.execute(
            "SELECT * FROM extraction_runs WHERE trigger_type = 'arxiv_search'"
        ).fetchall()
        assert len(runs) == 1
        run = dict(runs[0])
        assert run["status"] == "completed"
        assert run["facts_extracted"] > 0


# ============================================================================
# test_search_arxiv (HTTP mocked)
# ============================================================================

class TestSearchArxiv:

    @responses.activate
    def test_search_returns_parsed_papers(self):
        """search_arxiv fetches from API and returns parsed papers."""
        responses.add(
            responses.GET,
            ARXIV_API_URL,
            body=SAMPLE_ARXIV_XML,
            status=200,
        )

        papers = search_arxiv('"ERP" AND "LLM"', max_results=50)
        assert len(papers) == 2
        assert papers[0]["arxiv_id"] == "2401.12345v1"

    @responses.activate
    def test_search_handles_api_error(self):
        """search_arxiv returns empty list on HTTP error."""
        responses.add(
            responses.GET,
            ARXIV_API_URL,
            body="Server Error",
            status=500,
        )

        papers = search_arxiv('"ERP"', max_results=10)
        assert papers == []

    @responses.activate
    def test_search_all_deduplicates_by_arxiv_id(self):
        """search_all_queries deduplicates papers across queries."""
        # Return the same XML for every query — same papers each time
        for _ in SEARCH_QUERIES:
            responses.add(
                responses.GET,
                ARXIV_API_URL,
                body=SAMPLE_ARXIV_XML,
                status=200,
            )

        mock_sleep = MagicMock()
        papers = search_all_queries(max_results_per_query=50, sleep_fn=mock_sleep)

        # Even though each query returns 2 papers, dedup means only 2 unique
        assert len(papers) == 2
