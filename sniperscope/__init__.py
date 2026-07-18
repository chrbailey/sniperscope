"""Sniperscope — evidence-based talent radar.

Three programs, three responsibilities, physically separated:

- ``sniperscope.extract`` / ``sniperscope.crawl`` — deterministic GitHub
  extraction into an append-only evidence store. No LLM.
- ``sniperscope.analyze`` — Claude analysis constrained to the evidence
  JSON, validated by a Worker/Critic loop.
- ``sniperscope.arxiv`` — arXiv paper discovery cross-referenced to GitHub.

They share a database, never a process.
"""

__version__ = "1.0.0"
