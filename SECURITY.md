# Security

## Responsible Disclosure

If you find a security issue, please do **not** file a public GitHub issue.

Email: chris.bailey@erp-access.com — include "SECURITY:" in the subject line.

Expect an acknowledgment within 72 hours.

## Threat Model

This tool operates against public GitHub data and a local SQLite database. It holds credentials for:

- **GitHub PAT** — for reading public repos, user profiles, search. The default instructions grant `public_repo` and `read:user` scopes only. No write scopes should ever be granted to this tool.
- **Anthropic API key** — for the analysis and verifier calls.

Both credentials live in `.env` (git-ignored, chmod 600 recommended).

## Known Considerations

- **The SQLite database contains extracted GitHub data** — including candidate identifiers, commit messages, and inferred behavior patterns. Treat the `sniperscope.db` file as sensitive. It is git-ignored by default; do not commit it.

- **The evidence_facts table is append-only.** There is intentionally no mechanism to scrub or correct records after the fact. If you need to honor a request to remove candidate data, delete the entire candidate row (which cascades via the candidate_id foreign key references) rather than trying to mutate individual facts.

- **LLM analysis outputs may include quoted content from commit messages and README files.** If a candidate's repos contain sensitive information that shouldn't be aggregated, the analysis step may surface it. Review analyses before storing or sharing them externally.

- **The verifier uses the same model family as the worker.** A consistent blind spot in Claude affects both. This is a documented open problem; the mitigation is to review flagged-for-human cases carefully.

## What This Tool Does Not Do

- It does not make authenticated requests with write permissions.
- It does not scrape private repositories or authenticated-only content.
- It does not send candidate data to any third party other than the Anthropic API for analysis.
- It does not perform any outbound action on behalf of the user (no emails, no DMs, no follows).

If you see evidence of any of these behaviors in the code, that is a security issue — please report it.
