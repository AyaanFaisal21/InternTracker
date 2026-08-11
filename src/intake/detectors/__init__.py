from .ashby import AshbyDetector
from .base import Detector, looks_like_swe_internship
from .github_lists import GithubListDetector, OpportunityListDetector
from .greenhouse import GreenhouseDetector
from .lever import LeverDetector
from .browser import BROWSER_DETECTORS
from .custom_sites import CUSTOM_DETECTORS
from .suggestions import SuggestionDetector
from .workday import WorkdayDetector

__all__ = [
    "Detector",
    "looks_like_swe_internship",
    "GreenhouseDetector",
    "LeverDetector",
    "WorkdayDetector",
    "CUSTOM_DETECTORS",
    "BROWSER_DETECTORS",
    "SuggestionDetector",
    "AshbyDetector",
    "GithubListDetector",
    "OpportunityListDetector",
]
