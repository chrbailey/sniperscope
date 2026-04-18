# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
