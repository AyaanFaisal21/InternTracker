from .ashby import AshbyDetector
from .base import Detector, looks_like_swe_internship
from .github_lists import GithubListDetector
from .greenhouse import GreenhouseDetector
from .lever import LeverDetector
from .custom_sites import CUSTOM_DETECTORS
from .workday import WorkdayDetector

__all__ = [
    "Detector",
    "looks_like_swe_internship",
    "GreenhouseDetector",
    "LeverDetector",
    "WorkdayDetector",
    "CUSTOM_DETECTORS",
    "AshbyDetector",
    "GithubListDetector",
]
