"""Load runtime configuration and the company watchlist."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_WATCHLIST = Path(__file__).resolve().parents[2] / "config" / "watchlist.yaml"
DEFAULT_DB = Path(__file__).resolve().parents[2] / "intake.db"


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
    custom: list[str] = Field(default_factory=list)  # keys of CUSTOM_DETECTORS
    github_lists: list[str] = Field(default_factory=list)  # raw listings.json URLs


class Settings(BaseModel):
    watchlist: Watchlist
    db_path: Path = DEFAULT_DB
    verifier_model: str = "claude-opus-5"
    list_max_age_days: int = 14  # backstop lists: ignore entries older than this
    page_fetch_timeout: float = 15.0
    max_page_chars: int = 20_000  # cap on posting-page text sent to the verifier


def load_settings(watchlist_path: Path | None = None) -> Settings:
    path = watchlist_path or DEFAULT_WATCHLIST
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    return Settings(watchlist=Watchlist(**(data or {})))
