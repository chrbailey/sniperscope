# Sniperscope Growth Plan: From Niche Radar to Viral Proof-of-Work Standard

**Date:** 2026-07-18
**Status:** Proposal
**Prerequisite reading:** `docs/2026-04-14-sniperscope-design.md`, `README.md`

## The Original Goal, Restated

Sniperscope was built to solve one problem: in a world where every resume and
cover letter is LLM-polished, the only verifiable hiring signal left is public
proof of work. The system finds builders at the Claude + ERP intersection by
deterministically extracting GitHub evidence (no LLM), then running a
constrained, critic-validated analysis that is physically incapable of
flattery or fabrication. As a byproduct, every human decision lands in an
outcomes table — a training corpus for a future learned verifier.

That goal generalizes far beyond ERP. The generalized statement is:

> **Become the neutral, evidence-only layer between "what people claim" and
> "what people have provably built."**

The niche (Claude + ERP) was the pilot. The architecture — deterministic
extraction, append-only evidence, anti-flattery critic loop, no scores — is
the product. This document is the plan to take that architecture viral using
capabilities that exist today.

## The Core Insight: Flip the Mirror

Today Sniperscope is **recruiter-facing**: one operator scans hundreds of
strangers. That shape has no viral loop — the people being scanned never find
out, and the one operator has no reason to share.

The viral version is **builder-facing**: anyone can point Sniperscope at
*their own* GitHub handle and get back the thing no LLM will otherwise give
them — an assessment that refuses to flatter them.

The hook writes itself: **"The AI that won't compliment you."** Every other
tool in 2026 tells you your profile is impressive. Sniperscope is the only
one architecturally banned from doing so (20+ flattery words blocked at the
prompt layer, a separate verifier that fails any superlative, and an
append-only evidence table backing every sentence). In a culture drowning in
AI sycophancy, honesty is the novelty. People share what surprises them, and
"an AI told me the unvarnished truth about my GitHub" is surprising.

This is the same mechanic that made "GitHub Wrapped" and "roast my repo"
tools explode — self-curiosity plus a shareable artifact — but with a
defensible difference: those were toys; this one produces claims a hiring
manager can independently verify, because every claim links to an evidence
fact with a source and timestamp.

Crucially, the recruiter-facing mode doesn't go away. Every builder who
self-scans becomes a row in the same evidence database. The viral loop
*builds the radar's coverage* — the original goal — instead of replacing it.

## What Exists Today That Makes This Possible

Everything below is a shipped, generally-available capability as of mid-2026.
No speculative tech.

1. **Claude API with structured outputs + prompt caching.** The analysis and
   critic prompts are static; only the evidence JSON changes. Cached system
   prompts cut per-scan analysis cost to cents, which makes a free public
   tier economically survivable.
2. **The MCP ecosystem and its directories.** MCP is now the distribution
   channel for agent tools. A `sniperscope` MCP server ("scan this GitHub
   user, return evidence-backed assessment") gets discovered organically by
   everyone browsing MCP registries — distribution we don't have to buy.
3. **Claude Code plugins/skills.** A `/sniperscope <user>` skill puts the
   tool inside the daily driver of exactly the audience that would share it.
4. **GitHub GraphQL API.** The README names the REST 5,000/hr limit as the
   throughput ceiling. GraphQL batching raises effective throughput ~10x for
   profile-shaped queries, which a public tool needs on launch day.
5. **Supabase + Vercel.** Already in the stack (`db.py` has the Supabase
   path). Hosted Postgres with row-level security plus edge deployment means
   the single-page app is a weekend of work, not a platform build.
6. **OG-image generation at the edge.** Dynamic share cards (evidence
   summary, verified-claims count, "critic passed" seal) render at request
   time. The share card *is* the viral surface — it must be beautiful and
   scrupulously score-free.
7. **GitHub Actions + README badges.** A "Sniperscope-verified" badge in a
   README is a permanent backlink. Badges are how Codecov, shields.io, and
   every CI vendor grew — each badge is an ad placed by the user.
8. **GitHub webhooks / scheduled re-extraction.** The design doc already
   calls for continuous monitoring. A "your evidence changed" notification
   is the retention loop that turns one-time scanners into repeat users.

## The Plan

### Phase 0 — Make self-scan a one-liner (week 1)

- `python extract.py --user <me> && python analyze.py --user <me> --self`
  collapsed into a single `sniperscope me` entry point (uvx/pipx
  installable). Zero config beyond a GitHub token; Anthropic key optional
  (extraction-only mode still produces the evidence report).
- Output: a markdown "proof-of-work profile" — every claim footnoted to an
  evidence fact ID. This artifact is the seed of everything downstream.
- Ship the `--self` prompt variant: same constraints, second person voice,
  explicit "what the evidence does NOT show" section. That section is the
  screenshot people will post.

### Phase 1 — Hosted scan + share card (weeks 2–4)

- Single-page app on Vercel, evidence store on Supabase: enter a handle,
  watch extraction stream in (facts appearing live is the theater), get the
  critic-validated assessment.
- Share card: OG image with facts count, verified-claims list, thin-evidence
  flags, and the critic seal. **No scores, no grades, no rank** — the
  architecture rule is also the brand. "Not a score. Evidence." is the
  tagline on the card.
- Every claim on the page links to its `evidence_facts` row (public,
  read-only view). Verifiability is the differentiator; make it visible.
- Rate-limit by GitHub OAuth login: you can scan yourself freely; scanning
  others costs a queued slot. This is both abuse control and a consent
  gradient.

### Phase 2 — Distribution through the agent ecosystem (weeks 4–8)

- **MCP server** exposing `scan_user`, `get_evidence`, `diff_extractions`.
  Submit to the major MCP directories. The interview-verification use case
  ("re-extract live, diff against the submitted profile") is uniquely
  compelling for agents that assist recruiters.
- **Claude Code skill/plugin** wrapping the same server.
- **GitHub Action**: `sniperscope-verify` — on release, re-extract the
  maintainer's evidence and refresh a README badge. Badge links to the
  hosted profile. This is the compounding distribution channel.

### Phase 3 — Lens packs: let communities aim the scope (weeks 8–12)

- `seeds.json` + search queries generalize into a **lens pack**: a small
  JSON file defining a niche (seed repos, search queries, domain keywords —
  counting only, never filtering, per the design doc).
- Ship lenses for 5 starter niches beyond ERP (MCP-server builders, Rust
  systems, bioinformatics, infra/SRE, embedded). Accept community lens packs
  by PR — the contributor flywheel. Every lens maintainer becomes an
  evangelist to their own niche.
- Weekly "surfaced this week" digest per lens (Substack, already
  established). Surfaced, never ranked — alphabetical order, evidence
  summaries only.

### Phase 4 — The outcomes network (quarter 2)

- The long-game moat from the original article: hiring produces
  `(evidence, analysis, decision, outcome-at-N-months)` tuples. Multi-tenant
  outcomes (each org's decisions private to them, schema shared) accumulate
  the corpus the learned verifier needs.
- When the corpus supports it, train the continuous reward model the README
  names as future work — and publish the eval. "We replaced the prompt
  critic with a learned verifier, here's the A/B" is itself a launch event.

## Launch Playbook

1. **Show HN:** "Sniperscope — the AI that refuses to compliment your
   GitHub." Self-scan is the demo; the critic-loop architecture is the
   substance HN actually upvotes.
2. **The Karpathy angle is already in the repo.** The README places the
   system in the 1.0/2.0/3.0 taxonomy and the design doc ran a critic pass
   on itself. The second Substack article ("Show-Your-Work Version") is the
   credibility anchor — lead with the limitations section; the scope-honesty
   voice is rare enough to be shareable on its own.
3. **Seed the first screenshots.** Scan 20 well-known open-source
   maintainers *with their consent* and let them post their own results.
   Consent-first turns a potential backlash vector into an endorsement
   chain.
4. **Anti-slop timing.** Every week produces a new viral complaint about
   AI-polished resumes. Reply-guy strategically: the tool is the answer to a
   complaint people are already making.

## Guardrails (What We Refuse to Ship)

Virality that betrays the architecture kills the asset. Hard lines:

- **No scores, ranks, or leaderboards, ever.** The moment a number appears,
  the tool becomes optimizable, gameable, and indistinguishable from the
  slop it replaces. This is also the existing repo rule (`CLAUDE.md`).
- **Public data only; opt-out honored within 24h; deletion of derived
  analyses on request.** Evidence facts about opted-out users are excluded
  from all reads (append-only table stays intact; visibility is a query
  concern).
- **Self-scan is unrestricted; scanning others requires auth and is rate
  limited.** Mass unsolicited scanning is the failure mode that turns "the
  honest tool" into "the surveillance tool" in one news cycle.
- **The critic loop runs on every public output.** No fast path that skips
  verification for latency. `critic_passed=False` results are never shown
  publicly.

## What Success Looks Like

- **Loop metric:** share-card views → new self-scans (K-factor). Above ~0.4
  the badge/action channels compound it past 1.
- **Coverage metric:** candidates with fresh (<30 day) extractions per lens
  — the original radar goal, now community-fueled.
- **Moat metric:** outcomes rows accumulated. Nobody else has this dataset;
  it cannot be scraped, only earned.

The pattern in one line: the recruiter tool becomes a mirror, the mirror
becomes a badge, the badge becomes a standard — and the standard feeds the
radar it started as.
