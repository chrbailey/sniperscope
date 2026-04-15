"""Evidence-based candidate analysis with anti-flattery critic loop.

Takes structured evidence JSON from the database and sends it to Claude
for constrained analysis. The Worker/Critic/Ralph loop enforces that every
claim is backed by evidence and no flattering language survives.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import anthropic

import config
from db import Database
from models import AnalysisRun

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

MAX_CRITIC_ATTEMPTS = 3

# ============================================================================
# Analysis Prompt — CONSTANT, not dynamically generated
# This is the anti-flattery wall. Every constraint here is deliberate.
# ============================================================================

WORKER_SYSTEM_PROMPT = """\
You are a technical evidence analyst. You receive a JSON blob containing \
structured evidence about a software developer's public GitHub activity. \
Your job is to produce a factual analysis based ONLY on that evidence.

HARD CONSTRAINTS — violations will cause your output to be rejected:

1. You may ONLY reference facts present in the provided JSON. If a fact is \
not in the JSON, you must not mention it, infer it, or speculate about it.

2. You must NOT use flattering or superlative language. Prohibited words \
include but are not limited to: impressive, excellent, strong, outstanding, \
exceptional, remarkable, brilliant, talented, prolific, passionate, \
dedicated, expert, mastery, world-class, top-tier, cutting-edge. \
Use neutral, factual descriptions instead.

3. You must flag thin evidence explicitly. If a signal is based on fewer \
than 3 data points, say so. Examples: "Only 2 commits in this repo — \
insufficient for pattern assessment", "1 repo with tests out of 12 — \
cannot assess testing discipline broadly".

4. You must NOT tell a narrative or story. Do not connect facts into a \
career arc, growth trajectory, or character assessment. Report what the \
data shows, not what it might mean about the person.

5. You must NOT score, rank, or rate the candidate. No number scales, \
no letter grades, no comparative assessments.

6. For each field, if evidence is insufficient, say "Insufficient evidence" \
rather than guessing.

Output ONLY valid JSON matching this exact structure (no markdown, no \
commentary, no code fences):

{
  "summary": "2-3 sentence factual summary of what the evidence shows",
  "languages": {
    "primary": ["languages with significant usage across repos"],
    "secondary": ["languages with minor usage"]
  },
  "erp_systems": [
    {
      "name": "system name lowercase",
      "evidence_type": "code|keyword|topic",
      "evidence_count": 0
    }
  ],
  "ai_tooling": [
    {
      "name": "tool/platform name lowercase",
      "evidence_type": "code|keyword|topic",
      "evidence_count": 0
    }
  ],
  "testing_discipline": {
    "has_tests": true,
    "test_ratio": 0.0,
    "assessment": "factual description of testing evidence"
  },
  "working_style": {
    "commit_cadence": "factual description of commit patterns",
    "message_quality": "factual description of commit message patterns",
    "ai_pair_programming": "factual description of AI co-authoring signals"
  },
  "collaboration": {
    "solo_ratio": 0.0,
    "pr_activity": "factual description of PR evidence",
    "review_activity": "factual description of review evidence"
  },
  "thin_evidence_flags": [
    "list of areas where evidence is insufficient to draw conclusions"
  ],
  "notable_signals": [
    "list of factual observations that stand out — no value judgments"
  ],
  "evidence_count": 0,
  "repos_analyzed": 0
}
"""

WORKER_USER_TEMPLATE = """\
Analyze the following evidence JSON. Remember: reference ONLY facts in this \
JSON. No flattery. Flag thin evidence. Output valid JSON only.

Evidence:
{evidence_json}
"""

CRITIC_SYSTEM_PROMPT = """\
You are a strict quality reviewer for candidate analyses. You receive two \
inputs: the original evidence JSON and an analysis produced from it. Your \
job is to find problems.

Review the analysis for ALL of the following:

1. UNSUPPORTED CLAIMS: Does the analysis make any claim not directly \
supported by a fact in the evidence JSON? Flag each one with the exact \
claim and why it lacks evidence support.

2. FLATTERING LANGUAGE: Does the analysis use any flattering, superlative, \
or promotional language? Flag each instance with the exact word/phrase. \
Watch for: impressive, excellent, strong, outstanding, exceptional, \
remarkable, brilliant, talented, prolific, passionate, dedicated, expert, \
mastery, world-class, top-tier, cutting-edge, sophisticated, robust, \
comprehensive, innovative, deep, extensive, solid, proficient, adept.

3. NARRATIVE BIAS: Does the analysis tell a story the evidence does not \
support? Does it imply a career arc, growth trajectory, or character \
assessment? Flag any narrative framing.

4. MISSING THIN-EVIDENCE FLAGS: Are there areas where the evidence has \
fewer than 3 data points but the analysis does not flag this? Identify \
each missing flag.

5. SCORING OR RANKING: Does the analysis score, rate, or rank the \
candidate in any way? Flag any instances.

6. JSON VALIDITY: Is the output valid JSON matching the required schema?

Output your review as JSON:

{
  "passed": true or false,
  "findings": [
    {
      "category": "unsupported_claim|flattery|narrative_bias|missing_flag|scoring|schema",
      "severity": "high|medium|low",
      "detail": "specific description of the problem",
      "location": "which field in the analysis"
    }
  ],
  "summary": "1-2 sentence summary of your review"
}

Be strict. If you find ANY high-severity issue, set passed to false. \
If you find 3 or more medium-severity issues, set passed to false. \
Low-severity issues alone do not cause failure, but still list them.
"""

CRITIC_USER_TEMPLATE = """\
Review this analysis against the original evidence.

Original evidence:
{evidence_json}

Analysis to review:
{analysis_json}
"""


# ============================================================================
# Prompt hashing
# ============================================================================

def _compute_prompt_hash(prompt: str) -> str:
    """SHA-256 hash of the analysis prompt for reproducibility."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ============================================================================
# Worker — generates analysis from evidence
# ============================================================================

def _worker_analyze(evidence_json: Dict[str, Any],
                    critic_feedback: Optional[str] = None) -> Dict[str, Any]:
    """Worker: generate analysis from evidence. Returns analysis dict.

    Args:
        evidence_json: The structured evidence blob for one candidate.
        critic_feedback: If retrying, the critic's findings from the
            previous attempt, so the worker can correct its output.

    Returns:
        Parsed analysis dict, or a dict with an "error" key on failure.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    evidence_str = json.dumps(evidence_json, indent=2)
    user_content = WORKER_USER_TEMPLATE.format(evidence_json=evidence_str)

    if critic_feedback:
        user_content += (
            "\n\nYour previous analysis was rejected by the critic. "
            "Fix the following issues and resubmit:\n\n"
            + critic_feedback
        )

    try:
        response = client.messages.create(
            model=config.ANALYSIS_MODEL,
            max_tokens=4096,
            system=WORKER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIError as exc:
        logger.error("Anthropic API error in worker: %s", exc)
        return {"error": str(exc)}

    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if the model wraps its output
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        raw_text = "\n".join(lines)

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Worker returned invalid JSON: %s", exc)
        logger.debug("Raw worker output: %s", raw_text[:500])
        return {"error": "Worker returned invalid JSON", "raw": raw_text[:1000]}


# ============================================================================
# Critic — reviews analysis against evidence
# ============================================================================

def _critic_review(evidence_json: Dict[str, Any],
                   analysis: Dict[str, Any]) -> Tuple[bool, str]:
    """Critic: review analysis against evidence.

    Returns:
        (passed, findings_json_string)
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    evidence_str = json.dumps(evidence_json, indent=2)
    analysis_str = json.dumps(analysis, indent=2)
    user_content = CRITIC_USER_TEMPLATE.format(
        evidence_json=evidence_str,
        analysis_json=analysis_str,
    )

    try:
        response = client.messages.create(
            model=config.ANALYSIS_MODEL,
            max_tokens=4096,
            system=CRITIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIError as exc:
        logger.error("Anthropic API error in critic: %s", exc)
        # Critic failure = treat as not passed, force human review
        return False, json.dumps({
            "passed": False,
            "findings": [{"category": "api_error", "severity": "high",
                          "detail": str(exc), "location": "critic"}],
            "summary": "Critic could not execute due to API error.",
        })

    raw_text = response.content[0].text.strip()

    # Strip markdown code fences
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        raw_text = "\n".join(lines)

    try:
        review = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("Critic returned invalid JSON, treating as failure")
        return False, json.dumps({
            "passed": False,
            "findings": [{"category": "schema", "severity": "high",
                          "detail": "Critic output was not valid JSON",
                          "location": "critic"}],
            "summary": "Critic output could not be parsed.",
        })

    passed = review.get("passed", False)
    return passed, json.dumps(review)


# ============================================================================
# Main analysis loop — Worker/Critic/Ralph
# ============================================================================

def analyze_candidate(candidate_id: str, db: Database) -> AnalysisRun:
    """Run full analysis with critic loop for one candidate.

    1. Worker generates analysis from evidence JSON.
    2. Critic reviews for unsupported claims, flattery, narrative bias.
    3. Ralph decides: PASS -> store, FAIL -> retry with feedback (max 3).
       After 3 failures, store with critic_passed=False for human review.

    Returns:
        The AnalysisRun that was stored in the database.
    """
    evidence_json = db.get_evidence_json(candidate_id)
    if not evidence_json:
        logger.error("No evidence found for candidate %s", candidate_id)
        run = AnalysisRun(
            candidate_id=candidate_id,
            evidence_snapshot_at=datetime.utcnow().isoformat(),
            evidence_fact_count=0,
            analysis_output_json=json.dumps({"error": "No evidence found"}),
            model_used=config.ANALYSIS_MODEL,
            prompt_hash="",
            critic_passed=False,
            critic_findings_json=None,
            critic_attempts=0,
        )
        db.insert_analysis_run(run)
        return run

    evidence_count = evidence_json.get("metadata", {}).get("total_facts", 0)
    snapshot_at = evidence_json.get("metadata", {}).get("extracted_at")
    if not snapshot_at:
        snapshot_at = datetime.utcnow().isoformat()

    # Compute prompt hash from the full prompt that will be sent
    evidence_str = json.dumps(evidence_json, indent=2)
    full_prompt = WORKER_SYSTEM_PROMPT + WORKER_USER_TEMPLATE.format(
        evidence_json=evidence_str
    )
    prompt_hash = _compute_prompt_hash(full_prompt)

    login = evidence_json.get("candidate", {}).get("github_login", candidate_id)
    logger.info("Analyzing candidate %s (%d facts)", login, evidence_count)

    critic_feedback = None  # type: Optional[str]
    last_analysis = {}  # type: Dict[str, Any]
    last_critic_findings = None  # type: Optional[str]
    passed = False

    for attempt in range(1, MAX_CRITIC_ATTEMPTS + 1):
        logger.info("  Attempt %d/%d — Worker generating analysis",
                     attempt, MAX_CRITIC_ATTEMPTS)

        analysis = _worker_analyze(evidence_json, critic_feedback)
        last_analysis = analysis

        if "error" in analysis:
            logger.error("  Worker failed: %s", analysis["error"])
            last_critic_findings = json.dumps({
                "passed": False,
                "findings": [{"category": "worker_error", "severity": "high",
                              "detail": analysis["error"],
                              "location": "worker"}],
                "summary": "Worker failed to produce valid analysis.",
            })
            # Don't retry worker errors with critic feedback — the issue
            # is structural, not content-based
            break

        logger.info("  Attempt %d/%d — Critic reviewing analysis",
                     attempt, MAX_CRITIC_ATTEMPTS)

        passed, findings_json = _critic_review(evidence_json, analysis)
        last_critic_findings = findings_json

        if passed:
            logger.info("  Critic PASSED on attempt %d", attempt)
            break

        # Ralph says: retry with critic feedback
        logger.info("  Critic FAILED on attempt %d — retrying", attempt)
        try:
            findings = json.loads(findings_json)
            # Build targeted feedback from findings
            feedback_lines = []  # type: List[str]
            for f in findings.get("findings", []):
                feedback_lines.append(
                    "[{severity}] {category}: {detail} (in: {location})".format(
                        severity=f.get("severity", "?"),
                        category=f.get("category", "?"),
                        detail=f.get("detail", "?"),
                        location=f.get("location", "?"),
                    )
                )
            critic_feedback = "\n".join(feedback_lines)
        except (json.JSONDecodeError, KeyError):
            critic_feedback = findings_json

    if not passed:
        logger.warning(
            "  Analysis for %s did NOT pass critic after %d attempts — "
            "storing with critic_passed=False for human review",
            login, MAX_CRITIC_ATTEMPTS,
        )

    run = AnalysisRun(
        candidate_id=candidate_id,
        evidence_snapshot_at=snapshot_at,
        evidence_fact_count=evidence_count,
        analysis_output_json=json.dumps(last_analysis),
        model_used=config.ANALYSIS_MODEL,
        prompt_hash=prompt_hash,
        critic_passed=passed,
        critic_findings_json=last_critic_findings,
        critic_attempts=attempt,
    )
    db.insert_analysis_run(run)

    logger.info(
        "  Stored analysis run %s (passed=%s, attempts=%d)",
        run.id, passed, attempt,
    )
    return run


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sniperscope — evidence-based candidate analysis"
    )
    parser.add_argument(
        "--candidate-id",
        help="Analyze one specific candidate by ID",
    )
    parser.add_argument(
        "--unanalyzed",
        action="store_true",
        help="Analyze all candidates that have evidence but no analysis",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print evidence JSON without calling Claude",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.candidate_id and not args.unanalyzed:
        parser.error("Specify --candidate-id or --unanalyzed")

    if not args.dry_run and not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set. Add it to .env or environment.")
        sys.exit(1)

    db = Database()

    try:
        if args.candidate_id:
            _run_single(db, args.candidate_id, args.dry_run)
        elif args.unanalyzed:
            _run_unanalyzed(db, args.dry_run)
    finally:
        db.close()


def _run_single(db: Database, candidate_id: str, dry_run: bool) -> None:
    """Analyze a single candidate."""
    evidence = db.get_evidence_json(candidate_id)
    if not evidence:
        logger.error("No evidence found for candidate %s", candidate_id)
        sys.exit(1)

    login = evidence.get("candidate", {}).get("github_login", candidate_id)

    if dry_run:
        logger.info("Dry run — evidence JSON for %s:", login)
        print(json.dumps(evidence, indent=2))
        return

    start = time.monotonic()
    run = analyze_candidate(candidate_id, db)
    elapsed = time.monotonic() - start

    logger.info(
        "Completed analysis for %s in %.1fs — passed=%s, attempts=%d",
        login, elapsed, run.critic_passed, run.critic_attempts,
    )


def _run_unanalyzed(db: Database, dry_run: bool) -> None:
    """Analyze all candidates with evidence but no analysis."""
    candidates = db.get_unanalyzed_candidates()
    if not candidates:
        logger.info("No unanalyzed candidates found")
        return

    logger.info("Found %d unanalyzed candidates", len(candidates))

    for i, candidate in enumerate(candidates, 1):
        cid = candidate["id"]
        login = candidate["github_login"]
        logger.info("[%d/%d] Processing %s", i, len(candidates), login)

        if dry_run:
            evidence = db.get_evidence_json(cid)
            if evidence:
                print(json.dumps(evidence, indent=2))
            continue

        start = time.monotonic()
        run = analyze_candidate(cid, db)
        elapsed = time.monotonic() - start

        logger.info(
            "  [%d/%d] %s — %.1fs, passed=%s, attempts=%d",
            i, len(candidates), login, elapsed,
            run.critic_passed, run.critic_attempts,
        )


if __name__ == "__main__":
    main()
