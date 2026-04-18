# Sniperscope

> Evidence-based talent radar. Find the signal in the GitHub noise.

An anti-flattery hiring tool that separates mechanical evidence gathering from LLM-assisted judgment. The LLM that analyzes a candidate can only reference facts that were deterministically extracted. A verifier checks every analysis for flattery, unsupported claims, and narrative bias before storage.

## The Pattern

This is **rejection sampling with a learned verifier**, applied to candidate evaluation. The same pattern underneath RLHF reward-model sampling ([Ouyang et al. 2022](https://arxiv.org/abs/2203.02155)), Constitutional AI ([Bai et al. 2022](https://arxiv.org/abs/2212.08073)), and Best-of-N inference. Not novel. The contribution is the domain application and the open-source pilot.

Placed in Karpathy's Software 1.0 / 2.0 / 3.0 taxonomy:

- **Software 1.0** — extraction layer. Explicit Python, GitHub REST API, deterministic facts into an append-only SQLite table.
- **Software 3.0** — analysis layer. Claude call whose only input is the evidence JSON. Separate verifier call. Worker/Verifier/Router loop.
- **Software 2.0** — future state. The outcomes table accumulates `(evidence_json, analysis_json, human_decision, outcome_at_N_months)` tuples — outcome-labeled supervised records from which a reward model could eventually be fit. No such model has been trained yet.

## The Problem

Every resume in 2026 is LLM-polished. Every cover letter too. LinkedIn search returns thousands of identical claims. If you give an LLM a GitHub profile and ask "is this person good?", it writes a flattering paragraph regardless of what the evidence shows. This tool addresses that failure mode directly.

## What This Is Optimizing For

**Precision@K with zero tolerance for fabricated claims.** K candidates are surfaced for human review. I care about how many of the K are actually hireable, not about recall across the full population. Zero tolerance for fabrication is a hard constraint — a system that surfaces good candidates but occasionally invents skills is worse than a system that surfaces fewer candidates with verifiable claims. The loss is asymmetric.

The verifier is currently a hard pass/fail classifier, not a continuous scorer. A continuous reward model would be better; it is future work that depends on accumulating enough outcomes to train one.

## Architecture

```
1. DISCOVERY      Seed repos + search queries → candidate GitHub users
2. EXTRACTION     Python + GitHub API → append-only evidence facts (no LLM)
3. ANALYSIS       Claude reads evidence JSON only → constrained assessment
4. CRITIC LOOP    Worker/Verifier/Router validates every analysis
5. OUTCOMES       Your hiring decision + performance → training corpus
```

The extraction program has zero LLM calls. The analysis program only reads the evidence database. The verifier is a separate Claude call that reviews the analysis against the evidence. Three programs, three responsibilities, physically separated.

## Quickstart

```bash
git clone https://github.com/chrbailey/sniperscope.git
cd sniperscope
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Add GITHUB_TOKEN (classic, public_repo + read:user scopes)
# Add ANTHROPIC_API_KEY

python -m pytest tests/ -q           # no real API calls, all local mocks
```

## Usage

```bash
# Extract evidence for a GitHub user
python extract.py --user <username>

# Crawl seed repos and extract all contributors
python crawl.py --seeds seeds.json

# Discover new repos via GitHub search
python crawl.py --search

# Analyze extracted candidates (verifier-loop validated)
python analyze.py --unanalyzed

# arXiv paper search + cross-reference to GitHub
python arxiv_search.py --search --cross-ref-github
```

## Data Model

Six tables, one append-only guarantee:

| Table | Purpose |
|-------|---------|
| `candidates` | GitHub users discovered by crawler |
| `repos` | Per-candidate repo snapshots (mutable — snapshot at extraction time) |
| `evidence_facts` | **Append-only, DB-enforced** — every observed fact with source + timestamp |
| `extraction_runs` | Audit log of each extraction execution |
| `analysis_runs` | Claude analyses + verifier pass/fail + prompt_hash for reproducibility |
| `outcomes` | Your hiring decisions — the training corpus |

SQLite triggers reject `UPDATE` and `DELETE` on `evidence_facts` at the database layer. Append-only is enforced by the database, not by convention.

## The Critic Loop

The analysis prompt explicitly bans 20+ flattery words. The verifier reviews every analysis against six violation categories:

1. **Unsupported claims** — references to facts not in the evidence JSON
2. **Flattery** — superlatives, promotional language
3. **Narrative bias** — career arcs or growth trajectories the data doesn't support
4. **Missing thin-evidence flags** — claims based on <3 data points without disclosure
5. **Scoring / ranking** — any numerical or letter grade
6. **Schema violations** — invalid output JSON

If any high-severity issue is found, the worker retries with the verifier's feedback. Bounded at 3 attempts. After that, the analysis is stored with `critic_passed=False` for human review.

## Real Results

As of the most recent snapshot:

- **138 seed repos** tracked
- **772 candidates** crawled with extraction runs
- **29,792 evidence facts** stored
- Full Worker/Verifier/Router analysis pipeline **executed end-to-end on 4 real candidates and one n=14 synthetic forensic set**
- **3 candidates survived manual due-diligence** (LinkedIn match, company exists, conference presence, third-party citations, consistent story)

**Scope honesty:** the extraction layer runs at n=772. The full analysis loop has been executed at a much smaller sample. Claims about the verifier's correctness cannot be made without ground-truth labels, which do not yet exist.

## What This Is Not

- **Not a scoring or ranking tool.** It surfaces candidates for human review. No numerical scores. No letter grades. Ranking is a post-hoc human decision.
- **Not a fully-automated hiring pipeline.** The outputs go to a human. The outcomes table captures the human decision, not an algorithmic one.
- **Not validated at scale.** Claims about the pattern working at n>100 are projections, not measurements.
- **Not a reward model.** The verifier is a prompt-based hard classifier. A learned reward model is future work.

## Known Limitations

- **Private work is invisible.** GitHub catches public artifacts only. Senior engineers who don't push publicly are missed.
- **Oracle calibration unknown.** The user is the oracle; oracle biases become labels. Intra-rater reliability has not been measured.
- **Verifier and worker share a model family.** Any consistent blind spot in Claude Sonnet affects both. Reward-hacking on lexical heuristics is a known open problem.
- **No A/B comparison against "just read resumes yourself."** The null hypothesis has not been beaten with a controlled test.
- **GitHub REST rate limit** of 5,000/hr is the throughput ceiling. GraphQL with batched queries is the scaling path, not implemented.

## Tests

```bash
python -m pytest tests/ -v
```

All tests use local fixtures and mocked HTTP/API. No real network calls. Append-only triggers are tested explicitly — if that guarantee breaks, the suite catches it.

## Writeup

- [Hiring in 2026: The Interview Process Is the Training Data](https://baileyai.substack.com/p/hiring-in-2026-the-interview-process) — practitioner voice
- [Hiring in 2026: The Show-Your-Work Version](https://baileyai.substack.com/) — rigorous version after Karpathy-style critique

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md) for responsible disclosure.

## License

MIT. Build on it, fork it, use it for your own niche.

## About

Built by [Christopher Bailey](https://github.com/chrbailey), ERP Access Inc. 29 years in ERP (SAP, NetSuite, Oracle, Workday). Cleared initial review in the Anthropic Partner Network, April 2026. Bay Area.
