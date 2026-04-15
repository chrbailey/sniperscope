# Sniperscope: Evidence-Based Talent Radar

**Date:** 2026-04-14
**Status:** Building
**Author:** Christopher Bailey + Claude

## Problem

Hiring at the intersection of ERP systems and AI tooling (specifically Claude/Anthropic) is a needle-in-haystack problem. The target population on GitHub is approximately 50 repos and 100-200 contributors globally. Traditional hiring approaches (inbound applications, job boards) don't work for a niche this small. You need to go find them.

Additionally, LLM-generated assessments suffer from flattery bias — the model that gathers evidence and renders judgment in the same pass unconsciously selects evidence that supports the narrative it's forming. This is the same problem that SOX audits solve by separating evidence gathering from audit opinion.

## Solution

A Python-based GitHub talent radar that:

1. **Extracts** structured evidence from GitHub profiles (deterministic, no LLM)
2. **Analyzes** evidence through a constrained LLM pass (evidence-only, no external context)
3. **Validates** analysis through a Critic Loop (catches flattery, unsupported claims)
4. **Monitors** candidates continuously via GitHub webhooks/scheduled re-extraction
5. **Captures** training data from every interaction for future model improvement

## Core Architecture Principle: Separation

Extraction and analysis are physically separated:

- `extract.py` — Python + GitHub API. No LLM. Deterministic. Reproducible.
- `analyze.py` — Claude API. Constrained to evidence JSON. Critic-loop validated.
- These are separate programs sharing a database, not functions in the same process.

## Anti-Manipulation Architecture

1. Evidence is append-only (database triggers prevent UPDATE/DELETE)
2. Every fact has a source and timestamp
3. Re-extraction produces a diff against prior runs
4. Candidates cannot see the evidence structure or analysis rubric
5. Interview verification: re-extract live, diff against submitted profile

## Design Principles (Karpathy Software 3.0)

- **Proof of work over pedigree.** Evaluate what people built, not what they claim.
- **Extraction is Software 1.0** (explicit code, deterministic, testable).
- **Analysis is Software 3.0** (neural net is the program, prompt is the language).
- **Rubric-free extraction.** Count everything; what matters is learned from outcomes, not prescribed.
- **DOMAIN_KEYWORDS are for counting only** — never for filtering or excluding candidates.
- **Training sidecar captures everything** — eventually replaces heuristics with learned weights.

## Critic Loop Findings (Pre-Build)

Ran Worker/Critic/Ralph against the design:
- **PASS:** Separation of 1.0 (extraction) and 3.0 (analysis) is correct
- **PASS:** prompt_hash provides reproducibility for analysis versioning
- **FLAG → FIXED:** DOMAIN_KEYWORDS must never be used as filters (documented)
- **FLAG → ACKNOWLEDGED:** Test file detection is heuristic (source field documents confidence)
- **FLAG → FIXED:** diff_extractions() added for interview verification

## Data Model

Six tables: candidates, repos, evidence_facts (append-only), extraction_runs, analysis_runs, outcomes (training sidecar), seed_repos.

See `schema.sql` for full DDL.

## Seed Repos (Initial)

```
opensuitemcp/opensuitemcp         — NetSuite + Claude/Gemini/OpenAI
dsvantien/netsuite-mcp-server     — NetSuite MCP (official listing)
OpenAEC-Foundation/Frappe_Claude  — 60 Claude skills for ERPNext
JoelStell/erp-migration-agent     — ERP migration with Anthropic
oracle/netsuite-suitecloud-sdk    — Oracle's Claude Code integration
CDataSoftware/sap-erp-mcp-server  — SAP ERP via MCP
pantalytics/pan_ai_pro            — Claude as Odoo AI provider
```

GitHub search tells us: ~50 repos at this intersection, ~200 contributors total.

## Deployment: Claude Code Routines

Three scheduled routines (announced 2026-04-14):

| Routine | Trigger | Purpose |
|---------|---------|---------|
| sniperscope-extract | Daily 2am | Incremental extraction for tracked candidates |
| sniperscope-analyze | Weekly Sunday | Critic-loop analysis of unanalyzed evidence |
| sniperscope-seeds | Weekly Wednesday | GitHub search for new repos at the intersection |

## Training Data Flow

```
evidence_facts (raw behavior) 
  + analysis_runs (LLM assessment) 
  + outcomes (human judgment) 
  = labeled dataset for learning what predicts success
```

After 50+ labeled outcomes, the system can weight evidence signals by actual predictive value — replacing hardcoded keyword lists with learned importance.
