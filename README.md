# Sniperscope

> Evidence-based talent radar. Find the signal in the GitHub noise.

An anti-flattery hiring tool that separates evidence gathering from judgment — so the LLM that analyzes a candidate can only reference facts that were mechanically extracted, never invent narrative, never use flattering language. Critic-loop validated. Training data as a side effect.

Built to find engineers at the intersection of ERP systems (NetSuite, SAP, Oracle) and Anthropic/Claude tooling. The architecture works for any niche hiring problem.

## The Problem

Every resume in 2026 is LLM-polished. Every cover letter too. LinkedIn search returns thousands of identical claims. Interview theater is expensive. You need a different filter.

GitHub is the only public source where claims are timestamped and backed by artifacts. But if you give an LLM a GitHub profile and ask "is this person good?", it will write a flattering paragraph regardless. This tool solves that.

## Architecture: Extraction ≠ Analysis

```
1. DISCOVERY      Seed repos + search queries → candidate GitHub users
2. EXTRACTION     Python + GitHub API → append-only evidence facts (no LLM)
3. ANALYSIS       Claude reads evidence JSON only → constrained assessment
4. CRITIC LOOP    Worker/Critic/Ralph validates every analysis
5. OUTCOMES       Your hiring decision + performance → labeled training data
```

The extraction program has zero LLM calls. The analysis program only reads the evidence database. Separate processes, separate responsibilities. The critic catches flattery, unsupported claims, and narrative bias before any analysis is stored.

## Quickstart

```bash
git clone https://github.com/chrbailey/sniperscope.git
cd sniperscope
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Add GITHUB_TOKEN (classic, public_repo + read:user scopes)
# Add ANTHROPIC_API_KEY

python -m pytest tests/ -q           # 112 tests, all local mocks
```

## Usage

**Extract evidence for a GitHub user:**
```bash
python extract.py --user <username>
```

**Crawl seed repos and extract all contributors:**
```bash
python crawl.py --seeds seeds.json
```

**Search GitHub for new repos at your intersection:**
```bash
python crawl.py --search
```

**Analyze extracted candidates (critic-loop validated):**
```bash
python analyze.py --unanalyzed
```

**Search arXiv papers at the intersection + cross-reference authors to GitHub:**
```bash
python arxiv_search.py --search --cross-ref-github
```

## Data Model

Six tables, one append-only guarantee:

| Table | Purpose |
|-------|---------|
| `candidates` | GitHub users discovered by crawler |
| `repos` | Per-candidate repo snapshots (mutable) |
| `evidence_facts` | **Append-only** — every observed fact with source + timestamp |
| `extraction_runs` | Audit log of each extraction execution |
| `analysis_runs` | Claude analyses + critic pass/fail + prompt_hash for reproducibility |
| `outcomes` | Your hiring decisions — the training data sidecar |

SQLite triggers prevent UPDATE and DELETE on `evidence_facts`. This is the anti-manipulation wall.

## The Critic Loop

The analysis prompt explicitly bans 20+ flattery words. The critic reviews every analysis against six violation categories:

1. **Unsupported claims** — references to facts not in the evidence JSON
2. **Flattery** — superlatives, promotional language
3. **Narrative bias** — career arcs or growth trajectories the data doesn't support
4. **Missing thin-evidence flags** — claims based on <3 data points without disclosure
5. **Scoring / ranking** — any numerical or letter grade
6. **Schema violations** — invalid output JSON

If any high-severity issue is found, the worker retries with the critic's feedback. Max 3 attempts. After that, the analysis is stored with `critic_passed=False` for human review.

Same pattern as Worker/Critic/Ralph in software quality work — just applied to evaluating humans from artifacts.

## Real Results

Against the Claude + ERP GitHub population:

- **95 seed repos** discovered (from 7 initial + 8 search queries)
- **282 candidates** extracted
- **11,452 evidence facts** stored
- **15 repos** deep-code-reviewed
- **7 candidates** due-diligenced (LinkedIn, company, conferences, third-party citations)
- **3 verified PURSUE targets**

The 3 survivors passed independent verification across 5 dimensions. The other 279 were templates, demos, student projects, commercial marketing, or talented developers in adjacent domains.

## Tests

```bash
python -m pytest tests/ -v
# 112 passed
```

Every test uses local fixtures and mocked HTTP. No real API calls. Append-only triggers are tested explicitly — if that guarantee breaks, the test suite catches it.

## Writeup

Full architectural discussion: [How to Hire in 2026 While Creating Training Data as a Side Chick](#) (Substack, coming soon)

## License

MIT. Build on it, fork it, use it for your own niche.

## About

Built by [Christopher Bailey](https://github.com/chrbailey), ERP Access Inc. 29 years in ERP (SAP, NetSuite, Oracle, Workday). Anthropic Partner Network applicant. Bay Area.

This tool was built in a single session using Claude Code with parallel subagents and a critic-loop validation pass on the design spec. It is, in that sense, also an example of Karpathy's Software 3.0: small, composable, LLM-assisted code where the programming language is the prompt.
