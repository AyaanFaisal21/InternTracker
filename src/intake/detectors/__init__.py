from .ashby import AshbyDetector
from .base import Detector, looks_like_swe_internship
from .github_lists import GithubListDetector
from .greenhouse import GreenhouseDetector
from .lever import LeverDetector

__all__ = [
    "Detector",
    "looks_like_swe_internship",
    "GreenhouseDetector",
    "LeverDetector",
    "AshbyDetector",
    "GithubListDetector",
]
