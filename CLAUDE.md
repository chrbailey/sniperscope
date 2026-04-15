# Sniperscope

Evidence-based talent radar for the Claude + ERP intersection.

## Architecture Principles (Karpathy Software 3.0)

- **Proof of work over pedigree.** The system evaluates what people built, not what they claim.
- **Extraction is deterministic.** No LLM touches the evidence gathering. Python + GitHub API. Reproducible.
- **Analysis is constrained.** The LLM receives a JSON evidence file and may ONLY reference facts in that file. No web access, no additional context, no narrative invention.
- **Separation is physical.** `extract.py` and `analyze.py` are separate programs. They share a database, not a process.
- **Append-only evidence.** `evidence_facts` table has no UPDATE, no DELETE. Every fact has a source and timestamp.
- **Critic loop validates every analysis.** Worker generates, Critic challenges, Ralph decides. Flattery and unsupported claims are caught before storage.
- **Training sidecar captures everything.** Every extraction and analysis produces training data rows. Outcomes (your judgment) close the loop.

## Tech Stack

- Python 3.9+ (Union[str, Path] not str | Path)
- GitHub REST API (authenticated)
- Supabase (Postgres + Auth)
- Anthropic Claude API for analysis pass
- SQLite for local development/testing
- pytest for tests

## Key Rules

- NEVER mix extraction and analysis in the same execution
- NEVER allow the analysis LLM to make claims not in the evidence JSON
- NEVER score or rank candidates — extract facts, assess patterns, flag thin evidence
- ALL evidence writes are append-only with source attribution
- Tests must pass before any commit

## File Structure

```
extract.py      — GitHub API → evidence facts (deterministic, no LLM)
analyze.py      — Evidence JSON → Claude API → constrained assessment
crawl.py        — Seed repos → contributor discovery → extraction trigger
seeds.json      — Initial repo seed list
schema.sql      — Supabase/SQLite schema
db.py           — Database abstraction (Supabase + SQLite fallback)
github_client.py — GitHub API wrapper with rate limiting
models.py       — Pydantic models for evidence and analysis
config.py       — Environment config (.env based)
tests/          — pytest suite
```

## Running

```bash
# Extract one user
python extract.py --user chrbailey

# Extract all contributors from seed repos
python crawl.py --seeds seeds.json

# Analyze unanalyzed candidates
python analyze.py --unanalyzed

# Run tests
python -m pytest tests/ -v
```
