"""
Security module for phone automation agent.

Provides defenses against prompt injection and other attacks.
"""

from .image_filter import flatten_low_contrast, preprocess_screenshot
from .injection_detector import detect_injection, is_safe

__all__ = [
    "flatten_low_contrast",
    "preprocess_screenshot",
    "detect_injection",
    "is_safe",
]
