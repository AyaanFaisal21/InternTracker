"""Load runtime configuration and the company watchlist."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# Env overrides make the same code run on a laptop and in a container.
DEFAULT_WATCHLIST = Path(
    os.environ.get("INTAKE_WATCHLIST")
    or Path(__file__).resolve().parents[2] / "config" / "watchlist.yaml"
)
DEFAULT_DB = Path(
    os.environ.get("INTAKE_DB")
    or Path(__file__).resolve().parents[2] / "intake.db"
)


def _env_int(name: str, default: int) -> int:
    """Env override for a spend knob. A malformed value falls back to the
    default rather than failing open."""
    try:
        return max(0, int(os.environ[name]))
    except (KeyError, ValueError):
        return default


# Resolver spend ceilings. Env-overridable because they are the numbers an
# operator reaches for first when a bill surprises them.
DEFAULT_RESOLVER_BUDGET = _env_int("INTAKE_RESOLVER_BUDGET", 25)      # calls per UTC day
DEFAULT_RESOLVER_PER_CYCLE = _env_int("INTAKE_RESOLVER_PER_CYCLE", 5)  # calls per poll cycle
DEFAULT_RESOLVER_CACHE_DAYS = _env_int("INTAKE_RESOLVER_CACHE_DAYS", 30)


class WorkdayBoard(BaseModel):
    company: str  # display name, e.g. "NVIDIA"
    host: str     # e.g. "nvidia.wd5.myworkdayjobs.com"
    tenant: str   # e.g. "nvidia"
    site: str     # e.g. "NVIDIAExternalCareerSite"


class Watchlist(BaseModel):
    greenhouse: list[str] = Field(default_factory=list)  # board tokens
    lever: list[str] = Field(default_factory=list)       # company slugs
    ashby: list[str] = Field(default_factory=list)       # job board names
    workday: list[WorkdayBoard] = Field(default_factory=list)
    custom: list[str] = Field(default_factory=list)   # keys of CUSTOM_DETECTORS
    browser: list[str] = Field(default_factory=list)  # keys of BROWSER_DETECTORS
    github_lists: list[str] = Field(default_factory=list)  # raw listings.json URLs
    opportunity_lists: list[str] = Field(default_factory=list)  # underclassmen-style listings.json
    markdown_lists: list[str] = Field(default_factory=list)     # zapply-style README tables


class Settings(BaseModel):
    watchlist: Watchlist
    db_path: Path = DEFAULT_DB
    verifier_model: str = "claude-opus-5"
    list_max_age_days: int = 0   # backstop lists: 0 = no age cutoff (an active
    # posting is applyable regardless of age; lists also backfill old dates)
    page_fetch_timeout: float = 15.0
    max_page_chars: int = 20_000  # cap on posting-page text sent to the verifier

    # Company resolver (resolve.py). Triggered by a public endpoint, so every
    # knob here is a spend control as much as a tuning control.
    resolver_model: str = "claude-opus-5"
    resolver_effort: str = "medium"        # output_config.effort: low | medium
    resolver_max_tokens: int = 8192        # caps thinking + response per call
    resolver_max_searches: int = 5         # web_search max_uses per resolution
    resolver_max_continuations: int = 5    # pause_turn resumes before giving up
    resolver_cache_days: int = DEFAULT_RESOLVER_CACHE_DAYS
    resolver_daily_budget: int = DEFAULT_RESOLVER_BUDGET
    resolver_per_cycle: int = DEFAULT_RESOLVER_PER_CYCLE


def load_settings(watchlist_path: Path | None = None) -> Settings:
    path = watchlist_path or DEFAULT_WATCHLIST
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    return Settings(watchlist=Watchlist(**(data or {})))
