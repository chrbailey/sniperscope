"""Tests for Pydantic models — data structure validation."""
from __future__ import annotations

import uuid
from datetime import datetime

from sniperscope.models import (
    Candidate,
    EvidenceFact,
    EVIDENCE_CATEGORIES,
    DOMAIN_KEYWORDS,
)


# ============================================================================
# Candidate defaults
# ============================================================================

class TestCandidateDefaults:

    def test_candidate_defaults(self):
        """A Candidate gets a UUID id and created_at timestamp automatically."""
        c = Candidate(
            github_id=12345,
            github_login="testuser",
            discovered_via="seed:opensuitemcp/opensuitemcp",
        )

        # ID is a valid UUID
        parsed = uuid.UUID(c.id)
        assert str(parsed) == c.id

        # created_at is a valid ISO timestamp
        dt = datetime.fromisoformat(c.created_at)
        assert dt.year >= 2026

    def test_candidate_optional_fields_default_to_none(self):
        """Optional fields should be None when not provided."""
        c = Candidate(
            github_id=1,
            github_login="minimal",
            discovered_via="manual",
        )
        assert c.display_name is None
        assert c.email is None
        assert c.bio is None
        assert c.company is None
        assert c.location is None
        assert c.avatar_url is None
        assert c.public_repos is None
        assert c.followers is None
        assert c.following is None
        assert c.github_created_at is None

    def test_candidate_preserves_explicit_values(self):
        """Explicit values override defaults."""
        c = Candidate(
            id="custom-id",
            github_id=12345,
            github_login="testuser",
            discovered_via="manual",
            display_name="Jane Doe",
            created_at="2020-01-01T00:00:00",
        )
        assert c.id == "custom-id"
        assert c.display_name == "Jane Doe"
        assert c.created_at == "2020-01-01T00:00:00"


# ============================================================================
# EvidenceFact required fields
# ============================================================================

class TestEvidenceFactRequiredFields:

    def test_evidence_fact_required_fields(self):
        """All required fields must be present for a valid EvidenceFact."""
        fact = EvidenceFact(
            candidate_id="cand-001",
            category="testing",
            fact_key="test_file_ratio",
            fact_value="0.34",
            fact_type="number",
            source="github:repo:testuser/testrepo",
            extraction_run_id="run-001",
        )
        assert fact.candidate_id == "cand-001"
        assert fact.category == "testing"
        assert fact.fact_key == "test_file_ratio"
        assert fact.fact_value == "0.34"
        assert fact.fact_type == "number"
        assert fact.source == "github:repo:testuser/testrepo"
        assert fact.extraction_run_id == "run-001"

        # Auto-generated fields
        parsed = uuid.UUID(fact.id)
        assert str(parsed) == fact.id
        dt = datetime.fromisoformat(fact.extracted_at)
        assert dt.year >= 2026

    def test_evidence_fact_missing_required_field_raises(self):
        """Omitting a required field raises a validation error."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EvidenceFact(
                candidate_id="cand-001",
                # missing category, fact_key, etc.
            )


# Need pytest for the raises test
import pytest


# ============================================================================
# Evidence categories
# ============================================================================

class TestEvidenceCategories:

    def test_evidence_categories_defined(self):
        """EVIDENCE_CATEGORIES should be a non-empty list of strings."""
        assert isinstance(EVIDENCE_CATEGORIES, list)
        assert len(EVIDENCE_CATEGORIES) > 0
        for cat in EVIDENCE_CATEGORIES:
            assert isinstance(cat, str)
            assert len(cat) > 0

    def test_evidence_categories_expected_entries(self):
        """Known categories must be present."""
        expected = {
            "language", "testing", "commit_pattern", "ci_cd",
            "domain_keyword", "temporal", "collaboration",
            "dependency", "repo_metadata", "research_paper",
        }
        assert expected == set(EVIDENCE_CATEGORIES)

    def test_evidence_categories_no_duplicates(self):
        """Categories must not have duplicates."""
        assert len(EVIDENCE_CATEGORIES) == len(set(EVIDENCE_CATEGORIES))


# ============================================================================
# Domain keywords
# ============================================================================

class TestDomainKeywords:

    def test_domain_keywords_structure(self):
        """DOMAIN_KEYWORDS is a dict mapping domain names to keyword lists."""
        assert isinstance(DOMAIN_KEYWORDS, dict)
        assert len(DOMAIN_KEYWORDS) > 0

        for domain, keywords in DOMAIN_KEYWORDS.items():
            assert isinstance(domain, str), f"Domain key must be str, got {type(domain)}"
            assert isinstance(keywords, list), f"Keywords for '{domain}' must be list"
            assert len(keywords) > 0, f"Domain '{domain}' has no keywords"
            for kw in keywords:
                assert isinstance(kw, str), f"Keyword must be str, got {type(kw)} in '{domain}'"

    def test_domain_keywords_expected_domains(self):
        """Known domain categories must be present."""
        expected_domains = {"erp", "netsuite", "sap", "oracle", "salesforce",
                           "anthropic", "mcp", "ai_agent", "governance"}
        assert expected_domains.issubset(set(DOMAIN_KEYWORDS.keys()))

    def test_domain_keywords_all_lowercase(self):
        """All keywords should be lowercase for case-insensitive matching."""
        for domain, keywords in DOMAIN_KEYWORDS.items():
            for kw in keywords:
                assert kw == kw.lower(), (
                    f"Keyword '{kw}' in domain '{domain}' is not lowercase"
                )
