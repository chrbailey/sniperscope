"""Tests for analyze.py — Worker/Critic/Ralph loop with mocked Anthropic."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze  # noqa: E402
from db import Database  # noqa: E402
from models import Candidate, EvidenceFact, ExtractionRun  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cached_client():
    """Reset the cached Anthropic client before each test so the
    @patch('analyze.anthropic.Anthropic', ...) decorator can intercept
    the first call instead of hitting a previously-cached client."""
    analyze._CLIENT = None
    yield
    analyze._CLIENT = None


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def db_with_candidate(tmp_path):
    db_path = tmp_path / "test.db"
    import config
    original = config.SQLITE_PATH
    config.SQLITE_PATH = str(db_path)
    database = Database(str(db_path))

    candidate = Candidate(
        github_id=42,
        github_login="testuser",
        display_name="Test User",
        discovered_via="seed:owner/repo",
    )
    cid = database.upsert_candidate(candidate)

    run = ExtractionRun(candidate_id=cid, trigger_type="initial")
    run_id = database.create_extraction_run(run)

    facts = [
        EvidenceFact(candidate_id=cid, category="language", fact_key="primary",
                     fact_value="Python", fact_type="string",
                     source="github:user:testuser", extraction_run_id=run_id),
        EvidenceFact(candidate_id=cid, category="testing", fact_key="test_ratio",
                     fact_value="0.42", fact_type="number",
                     source="github:repo:testuser/project", extraction_run_id=run_id),
    ]
    database.insert_facts_batch(facts)
    database.complete_extraction_run(run_id, repos_scanned=1,
                                      facts_extracted=2, duration_ms=100)

    yield database, cid
    database.close()
    config.SQLITE_PATH = original


# ============================================================================
# Prompt hash (reproducibility)
# ============================================================================

class TestPromptHash:
    def test_hash_is_deterministic(self):
        h1 = analyze._compute_prompt_hash("hello world")
        h2 = analyze._compute_prompt_hash("hello world")
        assert h1 == h2

    def test_hash_differs_for_different_input(self):
        h1 = analyze._compute_prompt_hash("hello world")
        h2 = analyze._compute_prompt_hash("hello world!")
        assert h1 != h2

    def test_hash_is_sha256_length(self):
        h = analyze._compute_prompt_hash("anything")
        assert len(h) == 64


# ============================================================================
# Worker
# ============================================================================

class TestWorkerAnalyze:
    def _mock_anthropic_response(self, text):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=text)]
        return mock_response

    def test_worker_parses_valid_json_output(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            json.dumps({
                "summary": "test summary",
                "languages": {"primary": ["Python"], "secondary": []},
                "erp_systems": [],
                "ai_tooling": [],
                "testing_discipline": {"has_tests": True, "test_ratio": 0.42,
                                        "assessment": "tests present"},
                "working_style": {"commit_cadence": "n/a", "message_quality": "n/a",
                                    "ai_pair_programming": "n/a"},
                "collaboration": {"solo_ratio": 1.0, "pr_activity": "n/a",
                                    "review_activity": "n/a"},
                "thin_evidence_flags": [],
                "notable_signals": [],
                "evidence_count": 2,
                "repos_analyzed": 1,
            })
        )

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            result = analyze._worker_analyze({"evidence": {}, "candidate": {}})

        assert "error" not in result
        assert result["summary"] == "test summary"
        assert result["evidence_count"] == 2

    def test_worker_strips_markdown_fences(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            "```json\n" + json.dumps({"summary": "fenced"}) + "\n```"
        )

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            result = analyze._worker_analyze({"evidence": {}})

        assert result["summary"] == "fenced"

    def test_worker_returns_error_on_invalid_json(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            "this is not JSON"
        )

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            result = analyze._worker_analyze({"evidence": {}})

        assert "error" in result

    def test_worker_appends_critic_feedback_on_retry(self):
        mock_client = MagicMock()
        captured_prompts = []

        def capture(*args, **kwargs):
            captured_prompts.append(kwargs["messages"][0]["content"])
            return self._mock_anthropic_response(json.dumps({"summary": "ok"}))

        mock_client.messages.create.side_effect = capture

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            analyze._worker_analyze({"evidence": {}},
                                     critic_feedback="[high] flattery: 'impressive'")

        assert "previous analysis was rejected" in captured_prompts[0]
        assert "flattery" in captured_prompts[0]


# ============================================================================
# Critic
# ============================================================================

class TestCriticReview:
    def _mock_anthropic_response(self, text):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=text)]
        return mock_response

    def test_critic_parses_passed_true(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            json.dumps({
                "passed": True,
                "findings": [],
                "summary": "clean",
            })
        )

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            passed, findings = analyze._critic_review(
                {"evidence": {}}, {"summary": "x"}
            )

        assert passed is True
        parsed = json.loads(findings)
        assert parsed["passed"] is True

    def test_critic_parses_passed_false_with_findings(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_anthropic_response(
            json.dumps({
                "passed": False,
                "findings": [{
                    "category": "flattery",
                    "severity": "high",
                    "detail": "used 'impressive'",
                    "location": "summary",
                }],
                "summary": "flattery violation",
            })
        )

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            passed, findings = analyze._critic_review(
                {"evidence": {}}, {"summary": "x"}
            )

        assert passed is False
        parsed = json.loads(findings)
        assert parsed["findings"][0]["category"] == "flattery"

    def test_critic_api_error_returns_not_passed(self):
        mock_client = MagicMock()
        import anthropic as anthropic_mod
        mock_client.messages.create.side_effect = anthropic_mod.APIError(
            message="rate limit", request=MagicMock(), body=None
        )

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            passed, findings = analyze._critic_review({}, {})

        assert passed is False
        parsed = json.loads(findings)
        assert parsed["passed"] is False


# ============================================================================
# Ralph (full loop with mocked API)
# ============================================================================

class TestAnalyzeCandidate:
    def _mock_response(self, text):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=text)]
        return mock_response

    def test_pass_on_first_attempt(self, db_with_candidate):
        database, cid = db_with_candidate

        mock_client = MagicMock()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:  # worker
                return self._mock_response(json.dumps({
                    "summary": "clean analysis",
                    "evidence_count": 2,
                }))
            else:  # critic
                return self._mock_response(json.dumps({
                    "passed": True,
                    "findings": [],
                    "summary": "ok",
                }))

        mock_client.messages.create.side_effect = side_effect

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            run = analyze.analyze_candidate(cid, database)

        assert run.critic_passed is True
        assert run.critic_attempts == 1

    def test_retry_then_pass(self, db_with_candidate):
        database, cid = db_with_candidate

        mock_client = MagicMock()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] in (1, 3):  # worker (attempt 1, attempt 2)
                return self._mock_response(json.dumps({"summary": "analysis"}))
            elif call_count[0] == 2:  # critic attempt 1 — FAIL
                return self._mock_response(json.dumps({
                    "passed": False,
                    "findings": [{"category": "flattery", "severity": "high",
                                   "detail": "x", "location": "summary"}],
                    "summary": "fail",
                }))
            else:  # critic attempt 2 — PASS
                return self._mock_response(json.dumps({
                    "passed": True,
                    "findings": [],
                    "summary": "ok",
                }))

        mock_client.messages.create.side_effect = side_effect

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            run = analyze.analyze_candidate(cid, database)

        assert run.critic_passed is True
        assert run.critic_attempts == 2

    def test_max_attempts_exhausted_stores_failed(self, db_with_candidate):
        database, cid = db_with_candidate

        mock_client = MagicMock()

        def side_effect(*args, **kwargs):
            # Alternate worker / critic, critic always fails
            if len(mock_client.messages.create.call_args_list) % 2 == 0:
                return self._mock_response(json.dumps({"summary": "analysis"}))
            return self._mock_response(json.dumps({
                "passed": False,
                "findings": [{"category": "flattery", "severity": "high",
                               "detail": "x", "location": "summary"}],
                "summary": "fail",
            }))

        mock_client.messages.create.side_effect = side_effect

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            run = analyze.analyze_candidate(cid, database)

        assert run.critic_passed is False
        assert run.critic_attempts == analyze.MAX_CRITIC_ATTEMPTS

    def test_candidate_with_no_facts_records_zero_facts(self, tmp_path):
        """A candidate without evidence facts still goes through the loop;
        the analysis_run records evidence_fact_count=0. The API is mocked
        so no real network call is made."""
        import config
        original = config.SQLITE_PATH
        config.SQLITE_PATH = str(tmp_path / "empty.db")
        database = Database(str(tmp_path / "empty.db"))

        candidate = Candidate(github_id=1, github_login="ghost",
                              discovered_via="manual")
        cid = database.upsert_candidate(candidate)

        mock_client = MagicMock()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._mock_response(json.dumps({"summary": "no evidence"}))
            return self._mock_response(json.dumps({
                "passed": True, "findings": [], "summary": "ok",
            }))

        mock_client.messages.create.side_effect = side_effect

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            run = analyze.analyze_candidate(cid, database)

        assert run.evidence_fact_count == 0
        assert run.candidate_id == cid

        database.close()
        config.SQLITE_PATH = original

    def test_analysis_run_stored_with_prompt_hash(self, db_with_candidate):
        database, cid = db_with_candidate

        mock_client = MagicMock()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._mock_response(json.dumps({"summary": "x"}))
            return self._mock_response(json.dumps({
                "passed": True, "findings": [], "summary": "ok",
            }))

        mock_client.messages.create.side_effect = side_effect

        with patch("analyze.anthropic.Anthropic", return_value=mock_client):
            run = analyze.analyze_candidate(cid, database)

        assert run.prompt_hash
        assert len(run.prompt_hash) == 64  # SHA-256 hex
