"""
Detect prompt injection patterns in screenshots using Google Cloud Vision OCR.

Usage:
    from security.injection_detector import detect_injection

    if detect_injection("screenshot.png"):
        print("Injection detected - blocking image")
    else:
        # Safe to process
        ...

Setup:
    1. Enable Cloud Vision API in GCP Console
    2. Create service account, download JSON key
    3. Set GOOGLE_APPLICATION_CREDENTIALS env var or pass credentials path
"""

import os
import re
from pathlib import Path
from typing import Optional

# Injection patterns to detect
INJECTION_PATTERNS = [
    (r'do\s*\(', 'do() command'),
    (r'action\s*=', 'action parameter'),
    (r'element\s*=', 'element parameter'),
    (r'Tap\b', 'Tap action'),
    (r'Click\b', 'Click action'),
    (r'\[\s*\d{3}\s*,\s*\d{3,4}\s*\]', 'coordinate array'),
    (r'点击', 'Chinese click'),
    (r'execute|override|ignore', 'instruction keywords'),
]


def detect_injection(
    image_path: str,
    credentials_path: Optional[str] = None
) -> dict:
    """
    Detect injection patterns in image using Cloud Vision OCR.

    Args:
        image_path: Path to screenshot
        credentials_path: Path to GCP service account JSON (optional)

    Returns:
        dict with keys:
            - detected: bool, True if injection found
            - patterns: list of pattern names found
            - text: full OCR text (for debugging)
    """
    try:
        from google.cloud import vision
        from google.oauth2 import service_account
    except ImportError:
        raise ImportError("Install google-cloud-vision: pip install google-cloud-vision")

    # Set up credentials
    if credentials_path:
        credentials = service_account.Credentials.from_service_account_file(credentials_path)
        client = vision.ImageAnnotatorClient(credentials=credentials)
    else:
        # Use default credentials (GOOGLE_APPLICATION_CREDENTIALS env var)
        client = vision.ImageAnnotatorClient()

    # Read image
    with open(image_path, "rb") as f:
        content = f.read()

    # Run OCR
    image = vision.Image(content=content)
    response = client.text_detection(image=image)

    if response.error.message:
        raise Exception(f"Cloud Vision error: {response.error.message}")

    if not response.text_annotations:
        return {"detected": False, "patterns": [], "text": ""}

    text = response.text_annotations[0].description

    # Check for injection patterns
    found_patterns = []
    for pattern, name in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            found_patterns.append(name)

    return {
        "detected": len(found_patterns) > 0,
        "patterns": found_patterns,
        "text": text
    }


def is_safe(image_path: str, credentials_path: Optional[str] = None) -> bool:
    """
    Quick check if image is safe (no injection detected).

    Args:
        image_path: Path to screenshot
        credentials_path: Path to GCP credentials JSON

    Returns:
        True if safe, False if injection detected
    """
    result = detect_injection(image_path, credentials_path)
    return not result["detected"]
