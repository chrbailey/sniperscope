# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- Restructured the flat script layout into an installable `sniperscope` package with `pyproject.toml` and one console script per program (`sniperscope-extract`, `sniperscope-analyze`, `sniperscope-crawl`, `sniperscope-arxiv`); extraction and analysis remain physically separate executables
- `db.py` rewritten: INSERT/UPSERT statements are generated from a single per-table column list (SQLite `ON CONFLICT` upserts) instead of hand-maintained duplicates; `Database` is now a context manager
- All raw SQL moved behind `Database` — `extract.py` and `crawl.py` no longer touch `db.conn` directly (new `set_extraction_run_candidate`, `has_recent_extraction`, `update_seed_crawl_stats`, `get_candidate` methods)
- `crawl.py`: the three duplicated contributor-ingest loops collapsed into one `_ingest_users` helper
- `analyze.py`: worker and critic share one `_call_json` helper; prompt text is byte-identical to preserve `prompt_hash` reproducibility
- `arxiv.py` (renamed from `arxiv_search.py`): author resolution flattened into `_resolve_author`; synthetic IDs unchanged
- `extract.py`: shared `_user_source`/`_parse_iso` helpers; removed a redundant GitHub profile fetch per extraction (one fewer API call per user)
- Replaced deprecated `datetime.utcnow()` with a single timezone-aware `utc_now_iso()` helper (stored timestamp format unchanged)
- Type comments (`# type:`) replaced with real annotations throughout
- Tests no longer need `sys.path` hacks; CI installs the package via `pip install -e ".[dev]"`

### Removed
- `requirements.txt` (dependencies now declared in `pyproject.toml`)
- Unused `supabase` and `pytest-asyncio` dependencies (nothing imported them)

### Added
- CI via GitHub Actions (Python 3.9, 3.10, 3.11, 3.12)
- CONTRIBUTING.md, SECURITY.md, CHANGELOG.md
- Tests for `analyze_candidate` (mocked Anthropic client)
- Tests for `diff_extractions` (interview verification feature)
- Tests for `crawl_seeds` orchestration
- Tests for `outcome` operations in `db.py`

### Changed
- README rewritten with precise scope claims (n=772 extraction, n=4 full analysis)
- README now names the pattern: rejection sampling with a learned verifier
- Outcomes table artifact precisely labeled as outcome-labeled supervised data (not preference pair)
- Software 1.0/2.0/3.0 taxonomy explained with canonical ordering and architectural role

### Fixed
- Prior README claimed "95 seed repos / 282 candidates / 11,452 facts" — corrected to 138 / 772 / 29,792 to match current database
- Removed overstated claim that the architecture was "validated on n=772"; actual validation scope is much smaller

## [0.1.0] — 2026-04-17

### Added
- Initial public release
- `extract.py` — GitHub API → evidence_facts (deterministic, no LLM)
- `analyze.py` — Worker/Critic/Ralph analysis loop
- `crawl.py` — seed repo contributor discovery
- `arxiv_search.py` — research paper cross-reference
- `db.py` — SQLite layer with append-only triggers on evidence_facts
- `github_client.py` — rate-limited GitHub REST API wrapper
- 112 tests, all passing with local mocks
- Published under MIT license

### Companion Writeup
- [Hiring in 2026: The Interview Process Is the Training Data](https://baileyai.substack.com/p/hiring-in-2026-the-interview-process)
