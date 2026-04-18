"""Configuration from environment variables."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API_BASE = "https://api.github.com"
GITHUB_RATE_LIMIT_BUFFER = 100  # stop when this many requests remain
GITHUB_RATE_LIMIT_MAX_WAIT = int(os.getenv("GITHUB_RATE_LIMIT_MAX_WAIT", "60"))
GITHUB_SEARCH_API_SLEEP_SECONDS = int(os.getenv("GITHUB_SEARCH_API_SLEEP_SECONDS", "2"))

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "claude-sonnet-4-6")
ANALYSIS_MAX_TOKENS = int(os.getenv("ANALYSIS_MAX_TOKENS", "4096"))
MAX_CRITIC_ATTEMPTS = int(os.getenv("MAX_CRITIC_ATTEMPTS", "3"))

# Database
DB_MODE = os.getenv("DB_MODE", "sqlite")  # "sqlite" or "supabase"
SQLITE_PATH = os.getenv("SQLITE_PATH", str(Path(__file__).parent / "sniperscope.db"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Extraction
COMMIT_LOOKBACK_DAYS = int(os.getenv("COMMIT_LOOKBACK_DAYS", "180"))
MAX_REPOS_PER_USER = int(os.getenv("MAX_REPOS_PER_USER", "100"))
MAX_COMMITS_PER_REPO = int(os.getenv("MAX_COMMITS_PER_REPO", "500"))

# Crawl
RE_EXTRACT_DAYS = int(os.getenv("RE_EXTRACT_DAYS", "7"))

# Seed file
SEEDS_PATH = os.getenv("SEEDS_PATH", str(Path(__file__).parent / "seeds.json"))
