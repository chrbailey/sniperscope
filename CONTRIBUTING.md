# Contributing

Thanks for looking. A few rules the project takes seriously.

## The anti-flattery architecture is load-bearing

The point of this project is evidence-first evaluation. That means:

- **Extraction must never call an LLM.** `extract.py`, `crawl.py`, `arxiv_search.py`, and `github_client.py` must not import `anthropic` or any other LLM SDK. If you need to derive something, derive it deterministically from the GitHub API response.

- **Analysis must not access external sources.** `analyze.py` reads from the evidence database only. It does not call GitHub. It does not add context from the web. The prompt is the source code; the evidence JSON is the input; the output is constrained by the schema.

- **The verifier checks analyses against evidence.** Any new violation category should extend the existing six categories in `prompts.py` or `analyze.py`, not replace them. Adding a new category requires a corresponding test.

- **`evidence_facts` is append-only, enforced by SQLite triggers.** Do not add an `update_fact` or `delete_fact` method to `db.py`. The schema enforces this at the database layer. If you need to correct a fact, insert a new fact with a later timestamp and a source of `correction:<run_id>`.

- **No scoring, ranking, or numeric grading in analyses.** The verifier will reject it. Categorical severity only (none / low / medium / high).

## How to propose a change

1. **Open an issue first** if the change is larger than a typo or a test fix. The issue should describe the failure mode you're addressing, not just the code change.

2. **All code changes need tests.** Mock the network. No test should make a real GitHub or Anthropic API call.

3. **Python 3.9 compatible.** Use `Union[str, Path]` not `str | Path`. Use `Optional[X]` not `X | None`. Use `from __future__ import annotations` at the top of new files.

4. **Run the full test suite before submitting.** `python -m pytest tests/ -q` must pass on all Python versions in `.github/workflows/tests.yml`.

5. **If you touched extraction signals, document them in the README.** The data model section is authoritative.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not file security issues in the public tracker.

## What this project will not accept

- **Scoring/ranking features.** Numeric grades, leaderboards, ordered lists of "best candidates." The architecture is deliberately non-ranking.
- **Resume parsing.** The project evaluates GitHub artifacts, not self-reported resumes.
- **Outreach automation that sends on the user's behalf.** The system surfaces candidates for human review; humans decide who to contact.
- **Anything that bypasses the verifier.** Analyses must go through the Worker/Verifier/Router pipeline. No "trusted skip" flag.

## Code review criteria

Every PR is reviewed against:

1. Does it add a failure mode the critic-loop or tests would catch?
2. Does it preserve the physical separation of extraction, analysis, and verification?
3. Is the change smaller than the problem it solves?
4. Does it include tests that would fail without the change?

## Author

[Christopher Bailey](https://github.com/chrbailey). Reach out via issues for project questions.
