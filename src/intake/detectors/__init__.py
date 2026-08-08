from .ashby import AshbyDetector
from .base import Detector, looks_like_swe_internship
from .github_lists import GithubListDetector
from .greenhouse import GreenhouseDetector
from .lever import LeverDetector
from .workday import WorkdayDetector

__all__ = [
    "Detector",
    "looks_like_swe_internship",
    "GreenhouseDetector",
    "LeverDetector",
    "WorkdayDetector",
    "AshbyDetector",
    "GithubListDetector",
]
