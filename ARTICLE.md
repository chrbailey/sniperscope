<!-- Published at https://baileyai.substack.com/p/hiring-in-2026-the-interview-process -->
<!-- Companion code for this article. See README.md for tool documentation. -->

# Hiring in 2026: The Interview Process Is the Training Data

*How to walk away from every hiring round with a dataset Scale AI would charge you six figures to build. An application of rejection sampling with a learned verifier to the hiring problem.*

Nearly every resume I've read in the last six months reads as LLM-polished. Same for the cover letters. A year ago this felt like a cheat code. Now it's a noise floor so loud you can't hear the signal.

I needed to find people. Specifically: engineers at the intersection of ERP systems (NetSuite, SAP, Oracle) and Claude/Anthropic tooling. Small niche — when I actually crawled GitHub I surfaced 772 accounts, of which only a few dozen had active public code in the last six months. I tried the old ways first. LinkedIn search returned thousands of "NetSuite AI Consultants" with nearly identical bullet points. Job boards returned the same. Every resume looked equally credible. Every credible claim looked equally unverifiable.

So I built something different. And in the process I discovered that the right hiring architecture produces training data as a byproduct — an expert-domain dataset that Scale AI would charge six figures to manufacture. Companies pay billions for this kind of data. You can generate it for free, as a side effect, while doing the hiring you were going to do anyway. This post is about both halves.

## The 2026 problem with resumes

The resume was already a bad artifact in 2024. Self-reported skills. Inflated tenure. "Led a team" without naming the team. By 2026 every line is also AI-polished, every cover letter references the exact job description with suspicious precision, and the interview is theater we pay thousands of dollars per candidate to perform.

What you actually want to know:

1. Can they do the work?
2. Do they know the domain?
3. Will they tell you when they don't know something?
4. Can you verify any of the above?

A resume can't answer these. An interview partially can, but only after you've invested hours filtering the funnel. And the filtering step — reading resumes to pick interview candidates — is the step where LLMs have destroyed the signal the worst.

## The only remaining verifiable signal

GitHub is the one place where claims are automatically timestamped and backed by artifacts. A developer who says they know SuiteScript can be checked: do they have SuiteScript code in public repos, committed over years, with tests and CI and real bug fixes? A developer who says they've built MCP servers either has MCP servers in their commit history or doesn't.

This isn't perfect. GitHub misses private repos, internal work, and the most senior people who don't code anymore. But it's the only public signal that is:

- **Server-side timestamped** (commit dates can be locally backdated, but GitHub's push timeline and contribution calendar are server-authoritative — you can check whether the history was backfilled)
- **Artifact-backed** (every claim comes with the code)
- **Cross-referenceable** (co-authors, PRs, stars, forks — a web of relationships)
- **Continuously updated** (the profile you looked at six months ago is not the profile today)

For the niche I needed — ERP + Claude — GitHub is the only place where this intersection is visible at all. You can't search LinkedIn for "built an MCP server for NetSuite" and get a useful answer. You can search GitHub.

## The architecture: extraction and analysis are different programs

Here's the piece most AI-assisted hiring tools get wrong. They give an LLM a candidate profile and ask "is this person good?" The LLM, being helpful, writes a nice paragraph about how impressive the candidate is. This is what I call the **flattery wall failure**: when the same model gathers evidence and renders judgment in the same pass, it writes a flattering paragraph about how strong the candidate is regardless of what the evidence actually shows.

This is the same problem SOX audits solved thirty years ago. Evidence collection is one team. Audit opinion is a different team reading only the workpapers. You never let the person forming the opinion also choose which evidence to collect.

The architecture I built separates these physically:

```
┌─────────────────────────────────────────────────────────────┐
│  1. DISCOVERY                                                │
│                                                              │
│     Seed repos + search queries                              │
│                │                                             │
│                ▼                                             │
│     GitHub API crawler                                       │
└───────────────────┬─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  2. EXTRACTION — deterministic, no LLM                       │
│                                                              │
│     Python + GitHub API                                      │
│                │                                             │
│                ▼                                             │
│     ╔══════════════════════╗                                │
│     ║ evidence_facts table ║  ◄── APPEND-ONLY              │
│     ║ (SQL triggers block  ║      (no retroactive edits)    │
│     ║  UPDATE and DELETE)  ║                                │
│     ╚══════════════════════╝                                │
└───────────────────┬─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  3. ANALYSIS — LLM constrained to evidence JSON only         │
│                                                              │
│    ┌─────────────┐      ┌─────────────────┐                 │
│    │ WORKER      │─────▶│ CRITIC          │                 │
│    │             │      │                 │                 │
│    │ Proposes    │ FAIL │ Checks 6        │                 │
│    │ analysis    │◄─────│ violation       │                 │
│    │ from        │      │ categories      │                 │
│    │ evidence    │      └────────┬────────┘                 │
│    └─────────────┘               │ PASS                     │
│                                  ▼                          │
│                         ┌──────────────┐                    │
│                         │ RALPH routes │                    │
│                         └──┬────────┬──┘                    │
│                            │        │                       │
│                 passed     │        │   failed 3x           │
│                            ▼        ▼                       │
│                 ╔════════════╗  ┌────────────┐             │
│                 ║ analysis_  ║  │ Human      │             │
│                 ║ runs table ║  │ review Q   │             │
│                 ╚════════════╝  └────────────┘             │
└───────────────────┬─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│  4. OUTCOME — the asset that compounds                       │
│                                                              │
│     Your hiring decision  ───┐                               │
│                              ▼                               │
│     6-month performance ──▶ ╔═════════════╗                 │
│                             ║ outcomes    ║                 │
│                             ║ table       ║                 │
│                             ╚═════════════╝                 │
│                                    │                         │
│                                    ▼ labeled training rows   │
│                                                              │
│   What signals actually predict good hires                   │
│   for YOUR specific context? (No one else has this data)     │
│                                                              │
│                                    │ tune prompts over time  │
│                                    ▼                         │
│                          (feeds back to Worker)              │
└─────────────────────────────────────────────────────────────┘
```


**Phase 1 — Extraction.** A Python script with no LLM in it. Reads GitHub via the API. For each candidate, extracts structured facts: language usage, test file ratios, commit cadence, message quality, CI/CD presence, co-author patterns (which reveal AI pair programming), domain keyword counts, temporal patterns, collaboration signals. Writes every fact to an append-only SQLite table with a source attribution and timestamp. No judgment. No scoring. Just mechanical facts.

**Phase 2 — Analysis.** A separate Python script calls Claude with a strict prompt: "You may ONLY reference facts in this JSON blob. No flattery. No narrative. Flag thin evidence." The LLM never sees the GitHub API, never adds its own context, never discovers facts outside the evidence it was given.

**Phase 3 — Critic.** Another separate call reviews the analysis against the evidence. It checks for six specific failure modes: unsupported claims, flattering language, narrative bias, missing thin-evidence flags, scoring/ranking, schema violations. If any high-severity issue is found, the analysis fails and goes back to the worker with the critic's feedback.

**Phase 4 — Ralph.** A simple router: passed → stored, failed → retry (max 3), uncertain → flagged for human. The human review queue is where the real learning happens.

This is **rejection sampling with a learned verifier** — the same pattern behind RLHF reward-model sampling, Constitutional AI, AlphaGo's policy-value networks, and Best-of-N inference. Not novel. I am applying a well-studied pattern to a domain (hiring) where it hasn't been applied much. Useful to name the lineage so anyone familiar with the pattern can reason about it; useful for everyone else to know there's literature backing the shape.

## Why this actually works

I ran this against the GitHub population at the Claude + ERP intersection. The numbers, as of this week's snapshot of the Sniperscope SQLite database:

- **138 seed repos** tracked (started from 7 known repos plus 8 search queries; the live search layer keeps finding more)
- **772 candidates** extracted and stored
- **29,792 evidence facts** recorded across all candidates
- Most candidates had no active recent code; the practical working set reduced to a few dozen after filtering for repos pushed in the last 180 days
- A smaller set got manual deep-code review (reading actual source) plus a web-based due-diligence pass (LinkedIn, company registry, conference speaker lists, third-party citations)
- **Three people survived all filters** and became candidates for direct outreach

That is the signal-to-noise ratio of the real world. The long tail of dropped candidates were demo repos, templates, student portfolios, commercial products doing open-source marketing, or developers in an adjacent domain that didn't match what I was looking for.

The three survivors each passed independent verification across five dimensions: LinkedIn matches GitHub, claimed company exists with other verifiable employees, conference or community presence with named co-speakers, third-party citations from people I could verify, and consistent story across all sources.

Broad strokes: one is an SAP developer in the EU with a formal community-leadership role in the largest regional SAP user group. One runs a NetSuite consultancy in Latin America with 150+ projects and also teaches AI at a university. One maintains NetSuite developer tools with tens of thousands of installs and runs the NetSuite function inside a publicly-traded company.

I'm keeping their names out of this post — getting publicity consent takes a separate conversation and none of them signed up to be content. The point isn't who they are. The point is that the three of them exist, they never came up on LinkedIn search, and the only reason I found them is that the extraction step went directly to the artifacts they produced.

## What you're actually sitting on

Here is where this stops being a hiring tool and starts being something that will eventually matter more than the hiring itself.

Every candidate the system processes produces a structured record:

- The raw evidence facts extracted from GitHub (input)
- The critic-validated analysis output (model output)
- The decision I made about them (human judgment)
- The outcome over time — hired, responded, partnered, faded (ground truth)

In machine learning this shape of record is called a **preference pair** — or more precisely, an expert-domain preference record. It's the same shape of data the big labs pay premium prices to acquire. Not literally the same artifact — you'd need explicit consent, IP clearance, and a scrubbing step before anything becomes licensable — but the same underlying structure.

Precise category: the outcomes table schema is `(evidence_json, analysis_json, human_decision, outcome_at_6mo)`. That's a preference pair with an outcome label, which is specifically **reward-model training data** (used to train the model that scores RLHF candidate responses). It is not vanilla SFT data (which would be (input → ideal output) without a comparison), and it is not a demonstration trajectory. The distinction matters because reward-model data is the expensive category — the one market rates go highest for.

Here's what the market actually pays for this stuff in 2026:

| Data type | Going rate |
|-----------|-----------|
| Simple text classification | cents per example |
| Generic human preference annotation | low single-digit dollars per prompt at entry tiers; ~$10 at enterprise-blended rates |
| **Expert RLHF preference pair** (complex domain) | **~$100 per pair** |
| Specialist-domain preference data (medical, legal, finance) | $50–$200 per pair |
| Production-grade RLHF annotation pipeline (total spend) | $1M–$5M |

That's the per-pair economics. The strategic value is an order of magnitude higher.

Meta paid **$14.3 billion** in June 2025 for a 49% non-voting stake in Scale AI — valuing a company whose entire business is producing labeled data at $29 billion. That's the single biggest validation of the thesis: labeled data, especially expert data, is a primary moat in the current AI economy.

The publisher deals tell the same story:

- **Reddit** — $203M in total disclosed licensing deals across 2–3 year terms (OpenAI reportedly ~$70M/year and Google reportedly ~$60M, both per press reports; neither is officially confirmed).
- **News Corp / Meta** — up to $50M/year for three years.
- **Shutterstock** — $104M in AI licensing revenue in 2023; its full data-licensing business unit (of which AI licensing is a part) projected at $138M in 2024 with a $250M target by 2027.
- **Stack Overflow** — licensing deal with OpenAI (May 2024) and a separate Google Cloud / Gemini content partnership (March 2024). Different shape from the Reddit deal; not a clean apples-to-apples comparison.

Nieman Lab's reporting late in 2025 pushed back on this narrative: most publishers will see negligible per-quarter revenue from these deals relative to their overall business. That's actually the point in favor of the thesis here. The deals are worth billions in aggregate precisely because the demand is concentrated where supply is scarce — and your specific professional domain is scarcer than any publisher's general archive.

These companies also all sell *archives*, not *ongoing expert judgment*. Archives commoditize. What doesn't commoditize is continuously-produced expert decision records in a specific domain — because no one else has your domain, no one else has your specific taste in hires, vendors, or forensic patterns, and nobody can go back in time to start recording their past decisions.

## Prove me wrong: does every interaction produce training data?

My claim, which you challenged me to test: if you do real evaluation work with structured inputs, structured outputs, and recorded judgments, *every interaction produces training data automatically*.

Let me test it honestly. The claim is mostly right. Here's where it breaks.

**It's right:** Any decision with a recorded input, a recorded output, and an eventual ground-truth outcome *is* a training row. You don't need to buy data — you're generating it every workday. Your CRM pipeline does this. Your customer support tickets do this. Your hiring funnel does this. Your legal review of contracts does this. Your invoice approval workflow does this. Every one of those flows has the shape `(evidence → decision → outcome)`. If you record all three, you have labeled data.

**Where it breaks:** Most organizations record one or two of the three, never all three in a way that can be joined back together.

- You record the candidate's resume but not the structured evidence behind your decision. "Alice seemed sharp" is not a label.
- You record who got hired but not *why* — no counterfactual, no rejected candidates with their features preserved.
- You record the decision but never measure the outcome — no six-month performance link back to the original evidence.

The fix is architectural, not cultural. If the system *forces* structured inputs (extraction), structured outputs (critic-validated analysis), and structured outcomes (a labeled table), you get training data automatically. If the system lets decisions happen in email threads and Slack DMs, you get nothing — even if the work itself was excellent.

So the accurate version of the claim: **every interaction *in a system designed to capture the three-part record* produces training data.** Email threads don't. Spreadsheets mostly don't. A SQLite database with append-only evidence, a critic-validated analysis table, and an outcomes table does.

## Why your dataset is more valuable than the big public deals

The Reddit/OpenAI deal and its peers sold archives — static snapshots of general internet text. Those commoditize. Anyone with enough money can license them. What doesn't commoditize:

**Domain-specific expert judgment.** The generic LLM has read the entire internet. It has not watched *you* reject ten candidates and love three in *your specific domain*. It doesn't know which signals predict success for *your specific work*. That knowledge exists nowhere else and cannot be bought. You're the only one producing it, and only from the decisions you're already making.

**Paired preference data at expert rates.** Market rates put expert preference pairs at $50–$200 each in commodity domains. In a specialist vertical — ERP + AI hiring, forensic pattern validation in SAP transactions, medical device regulatory review — there's no market rate because there's no market supply. The rate is "whatever someone will pay to not build it from scratch." Spoiler: that's a lot.

**The reasoning chain, not just the label.** When a critic catches an analysis that used the word "impressive" and forces a revision, that rejection-plus-correction pair is more valuable than the final approved analysis on its own. It teaches a future model how to *not* produce that failure. Constitutional AI — Anthropic's core training method — uses exactly this shape of data. You generate it every time the critic loop fires.

This is why I put the critic loop in the architecture in the first place. Even if I never sell or license this data, even if I never train a model on it, forcing every analysis through a Worker/Critic/Ralph cycle means every run produces a structured, labeled, validated triple. The moment I decide I want to fine-tune a model on my specific hiring patterns, the dataset is already there — already cleaned, already labeled, already aligned with my actual decisions.

## Why companies pay so much for this while you're sitting on top of yours

Reddit got $203M for eight years of general-purpose text. Roughly $25M/year for data *any* public forum could generate given enough time.

A mid-size ERP consultancy generates more strategic-value-per-row than Reddit — one forensic finding report contains expert reasoning no Reddit thread has — but 99.9% of that value evaporates because it's never captured in a structured format that could ever be replayed or labeled.

The reason is simple: building the *capture architecture* is boring infrastructure work. Nobody gets promoted for it. Meanwhile Scale AI is worth $29 billion specifically because they figured out the capture problem and industrialized it for others.

You can do the same thing for yourself, in miniature, inside whatever evaluation work you already do. The three ingredients are not negotiable:

1. **Structured inputs.** If your evaluation starts from unstructured data (a resume, a PDF, a call transcript), extract it mechanically first. Evidence gathering done by code, not by you. Deterministic. Reproducible. Immune to the LLM inventing facts.

2. **Structured outputs.** Whatever judgment you make — hire, reject, flag, confirm — has to land in a row with named fields. Not "I liked them." A row with `domain_expertise`, `test_discipline`, `communication_style`, `specific_concerns[]`.

3. **Structured outcomes.** Six months later, someone has to answer the question: was the decision right? This is the step everyone skips. Without the ground-truth column, the other two columns are just telemetry, not training data.

All three. In a table. Joined by `candidate_id` (or `vendor_id`, or `finding_id`, or whatever the unit of work is). That's the whole discipline.

## "Side effect," not "side project"

Critical nuance I want to land: this cannot be the goal. The moment you optimize for producing training data, you corrupt the labels. You'll rate candidates to generate a balanced dataset instead of to make good hires. You'll engineer judgments instead of recording them.

The training data is valuable *precisely because* the hiring decisions came first. Build the tool to solve the real problem. Record the evidence, the judgments, and the outcomes because those three are what it takes to do the evaluation work well. The dataset accumulates in the background.

In a year you have something you couldn't have bought. In two years you have something Scale AI would quote you six figures to build. In five years you have the only proprietary dataset for your specific vertical — and no one else will have caught up, because the only way to build it is to have been recording the decisions the whole time.

That's the side effect. It's not a side project.

## The meta-insight: same pattern, different problem

Here's what surprised me. The architecture I built for hiring — extraction separated from analysis, critic-loop validation, append-only evidence, training data as byproduct — is the same architecture I need for the adjacent problem: forensic pattern discovery in ERP transaction data.

Existing forensic tools ship with hardcoded rules. "Flag invoices over $X." "Alert on vendor master changes." These rules miss new patterns and fire on irrelevant ones. Every company's fraud signature is different. The rules are someone else's opinion about what matters.

A critic-loop changes the shape. A Worker examines transaction data and proposes candidate patterns. A Critic validates each candidate against the evidence — does the detection signature actually match the cited transactions? Is the description speculating about intent the data doesn't show? Is there enough evidence or is this thin?

Confirmed patterns enter a persistent library. Rejected candidates become negative training examples. The next run, the Worker sees prior patterns as baselines and can flag "new instances of known pattern" versus "novel candidate." The library grows with every engagement.

I ran this against synthetic SAP opportunity data: 14 candidates proposed by the worker, 4 confirmed by the critic, 8 rejected, 2 uncertain. The critic caught real failures — one candidate cited a transaction as evidence that didn't actually match the detection signature when you checked the data; another used language implying intent ("revenue recognition record exists without corresponding ERP order") when the data only showed a missing link, not a motive. Both were rejected.

One run on synthetic data isn't proof the pattern generalizes. It's proof the loop fires and the critic rejects specific failure modes — evidence mismatches and narrative overreach. Real validation requires production transaction data and outcomes tracked across engagements over months. That's the next step.

Same architecture. Different domain. Same training-data compounding effect: each engagement teaches the system which patterns are real and which are noise, specific to that client. And each engagement produces expert-labeled forensic pairs worth $100+ each on the open market, for free, because you were doing the forensic work anyway.

## What surprised me

Three things I didn't predict when I started this:

**The niche was 3x bigger than I thought.** I estimated 200 GitHub accounts at the ERP + Claude intersection. The crawl returned 772. Most were dormant or adjacent, but the long tail of "someone once starred an MCP repo" was way denser than my prior.

**The critic did real work on the first synthetic forensic run.** I expected it to rubber-stamp most candidates. It rejected 8 out of 14, including one where the worker cited a transaction ID that — when you actually checked the data — didn't match the detection signature it claimed. That's the exact failure mode the critic was designed to catch, and it caught it on run one.

**The best candidate had essentially no LinkedIn presence.** When I cross-referenced the top three survivors against public profiles, one of them — the person whose production code was the strongest evidence in the entire dataset — had a LinkedIn that was years out of date and didn't mention any of the work his GitHub proved he was currently doing. Traditional hiring would never have found him. The evidence-first architecture surfaced him as a top-three match. That asymmetry is the whole point of the architecture, but seeing it happen on the first real run was still a surprise.

## Why I'm writing this

I'm not selling the tool. I open-sourced both pieces:

- **Sniperscope** — the hiring radar I built to find the three people ([github.com/chrbailey/sniperscope](https://github.com/chrbailey/sniperscope), Python, SQLite, 112 tests, MIT)
- **SAP Transaction Forensics** — the forensic discovery engine ([github.com/chrbailey/SAP-Transaction-Forensics](https://github.com/chrbailey/SAP-Transaction-Forensics), TypeScript + Python, 1,639 passing tests across 70 suites, MIT, with the critic-loop pattern discovery layer added this week)

I'm writing this because the pattern is bigger than either tool. If you're doing any kind of evaluation work in 2026 — hiring, vendor selection, investment due diligence, threat intelligence — the architecture that survives the LLM-noise era has three properties:

1. **Extraction is deterministic.** No LLM in the evidence-gathering path. Python, APIs, parsers. Reproducible. Auditable. Immune to the model hallucinating convenient facts.

2. **Analysis is constrained.** The LLM can only reference what extraction gave it. No web access, no additional context, no narrative invention. If the evidence is thin, the analysis must say so.

3. **A critic validates every conclusion.** Not the same model generating the conclusion. A separate call with an explicit violation checklist. The critic's findings become feedback for the next worker attempt.

Do those three things and you get a fourth for free: every run produces a labeled training row. Over time, your system learns your specific judgment, not a generic model's best guess.

## What doesn't work

Two things I tried that failed.

**A React frontend where candidates apply.** I designed this early. The candidate would click "Apply with GitHub," OAuth into their account, and the system would extract their evidence automatically. One step for them. Living profile updated via webhooks.

The flaw: I have no brand driving inbound traffic. I'd have built a beautiful applicant funnel with zero applicants. The fix was the inverse — instead of waiting for candidates to apply, crawl GitHub and find the population. The React app is still the right architecture for someone who has applicant flow. It's wrong for someone who doesn't.

**Role-configurable scoring rubrics.** I almost built a system where you define roles with weighted criteria and the tool scores candidates against each role. This is what every hiring platform does. For a personal-scale or niche-consulting tool, skip it — you're just encoding your current guess about what matters, then calling the encoded guess "data." Start with rubric-free extraction, record your decisions, let the labeled outcomes tell you what matters. That's actual learning. For a regulated org (EEOC documentation requirements, for example), you'll need rubrics by law — but even then, record the per-criterion *evidence*, not just the score, so the learning loop can still run underneath.

## The practical takeaway

If you're hiring in 2026:

1. **Stop reading resumes first.** Read GitHub first. If the person has a public profile, it's a stronger signal than anything they wrote about themselves.

2. **Physically separate evidence gathering from judgment.** Two scripts. Two databases. Not two functions in the same program.

3. **Use a critic-loop on the judgment step.** Worker proposes, Critic challenges, Ralph decides. Take the token cost — LLM calls are cheap compared to a bad hire.

4. **Record the outcomes.** Even a minimal table: `(candidate_id, decision, quality_rating, date)`. The training data is worthless without the labels.

5. **Don't optimize for the training data.** Optimize for good hires. The data accumulates because you're doing the real thing. The moment you optimize for the dataset, you corrupt the labels.

This isn't automation. It's not AI replacing hiring managers. It's a different division of labor: the machine does mechanical evidence gathering and pattern checking; the human does judgment; the judgment gets recorded; over time the judgments teach the system what signals matter for your specific context.

That last part — the system learning what signals matter for YOUR specific context — is the thing no generic LLM can do for you. It's the moat.

---

## Sources

Every dollar figure and deal in this article is from a primary or reputable secondary source. I'm putting them here because you're going to drop this into an LLM and ask it to verify the claims. Save yourself a step.

**Training data and annotation economics:**
- Vendr — [Scale AI pricing and contract data](https://www.vendr.com/buyer-guides/scale-ai)
- Time — [How Meta's $14B Scale deal upended the data-labeling industry](https://time.com/7294699/meta-scale-ai-data-industry/)
- CNBC — [Zuckerberg's $14B bet on Scale AI, June 2025](https://www.cnbc.com/2025/06/10/zuckerberg-makes-metas-biggest-bet-on-ai-14-billion-scale-ai-deal.html)
- TechCrunch — [Cracks in the Meta-Scale partnership, August 2025](https://techcrunch.com/2025/08/29/cracks-are-forming-in-metas-partnership-with-scale-ai/)
- Sacra — [Surge AI revenue and business model](https://sacra.com/c/surge-ai/)
- West Operators — [Surge AI premium-pricing case study](https://westoperators.com/blog/surge-ai-case-study)
- Second Talent — [Annotation cost guide for LLM fine-tuning and RLHF](https://www.secondtalent.com/resources/data-annotation-for-llm-fine-tuning-rlhf-and-instruction-tuning-guide/)
- BasicAI — [2025 data annotation services cost guide](https://www.basic.ai/blog-post/how-much-do-data-annotation-services-cost-complete-guide-2025)
- Nathan Lambert — [The RLHF Book: Preference Data chapter](https://rlhfbook.com/c/11-preference-data)
- Nathan Lambert — [The RLHF Book: Constitutional AI chapter](https://rlhfbook.com/c/13-cai)
- IntuitionLabs — [RLHF platforms comparison in regulated verticals](https://intuitionlabs.ai/articles/rlhf-platforms-biotech-comparison)

**Publisher licensing deals:**
- TechCrunch — [Reddit: $203M licensed so far, Feb 2024](https://techcrunch.com/2024/02/22/reddit-says-its-made-203m-so-far-licensing-its-data/)
- Search Engine Land — [OpenAI may pay Reddit $70M](https://searchengineland.com/openai-may-pay-reddit-70m-for-licensing-deal-451882)
- TechCrunch — [Stack Overflow signs deal with OpenAI, May 2024](https://techcrunch.com/2024/05/06/stack-overflow-signs-deal-with-openai-to-supply-data-to-its-models/)
- Yahoo Finance — [Shutterstock's $104M AI licensing business](https://finance.yahoo.com/news/shutterstock-ai-licensing-business-generated-120000890.html)
- Shutterstock investor relations — [Six-year OpenAI partnership extension](https://investor.shutterstock.com/news-releases/news-release-details/shutterstock-expands-partnership-openai-signs-new-six-year)
- Press Gazette — [News Corp / Meta up to $50M/yr for three years](https://pressgazette.co.uk/platforms/news-publisher-ai-deals-lawsuits-openai-google/)
- Digiday — [2025 publisher AI deal timeline](https://digiday.com/media/a-timeline-of-the-major-deals-between-publishers-and-ai-tech-companies-in-2025/)
- Nieman Lab — [Publishers will see no meaningful AI licensing revenue in 2026, Dec 2025](https://www.niemanlab.org/2025/12/publishers-will-see-no-meaningful-ai-licensing-revenue/)

**Regulatory and legal context:**
- Cybersecurity News — [Judge orders OpenAI to produce 20M ChatGPT logs, Jan 2026](https://cybersecuritynews.com/openai-20-million-chatgpt-chats/)
- Lawyer Monthly — [NYT v. OpenAI SDNY discovery analysis, Jan 2026](https://www.lawyer-monthly.com/2026/01/openai-sdny-discovery-20m-chat-logs-legal-impact/)
- PYMNTS — [Fintechs building foundation models on proprietary data, 2026](https://www.pymnts.com/artificial-intelligence-2/2026/fintechs-race-to-build-foundation-models-on-proprietary-data/)

**Code referenced:**
- [github.com/chrbailey/sniperscope](https://github.com/chrbailey/sniperscope) — the hiring radar (Python, 112 tests, MIT)
- [github.com/chrbailey/SAP-Transaction-Forensics](https://github.com/chrbailey/SAP-Transaction-Forensics) — the forensic discovery engine with critic-loop pattern learning (TypeScript + Python, 1,639 passing tests across 70 suites, MIT)

---

*I'm Christopher Bailey. Twenty-nine years in ERP — SAP, NetSuite, Oracle, Workday. Currently building AI tooling for the intersection of enterprise systems and Claude. Cleared initial review in the Anthropic Partner Network (April 2026). Based in Redwood City. Newsletter archive at https://baileyai.substack.com/p/hiring-in-2026-the-interview-process. Code at [github.com/chrbailey](https://github.com/chrbailey).*
