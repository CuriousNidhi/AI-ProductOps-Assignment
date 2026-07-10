from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CASE_STUDY_DIR = PROJECT_ROOT / "case_study"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"

INPUT_CSV = DATA_DIR / "apps.csv"
RESULTS_CSV = DATA_DIR / "results.csv"
VERIFIED_RESULTS_CSV = DATA_DIR / "verified_results.csv"
ANALYTICS_JSON = CASE_STUDY_DIR / "analytics.json"
ANALYTICS_SUMMARY_MD = CASE_STUDY_DIR / "analytics_summary.md"

RESULT_COLUMNS = [
    "App Name",
    "Category",
    "One-line description",
    "Authentication method",
    "Self Serve or Gated",
    "API Type",
    "API Surface",
    "Existing MCP",
    "Buildability Verdict",
    "Main Blocker",
    "Evidence URL",
]

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
DEFAULT_USER_AGENT = os.getenv(
    "USER_AGENT",
    "AI-ProductOps-Assignment/1.0 (+https://openai.com)",
)
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "5"))
MAX_PAGES_PER_APP = int(os.getenv("MAX_PAGES_PER_APP", "4"))
MAX_EVIDENCE_CHARS = int(os.getenv("MAX_EVIDENCE_CHARS", "12000"))
DEFAULT_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


@dataclass(frozen=True)
class AppRecord:
    app_name: str
    category: str = ""
    homepage_url: str = ""


@dataclass(frozen=True)
class ResearchConfig:
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    user_agent: str = DEFAULT_USER_AGENT
    openai_model: str = DEFAULT_OPENAI_MODEL
    max_search_results: int = MAX_SEARCH_RESULTS
    max_pages_per_app: int = MAX_PAGES_PER_APP
    max_evidence_chars: int = MAX_EVIDENCE_CHARS
    log_level: str = DEFAULT_LOG_LEVEL
