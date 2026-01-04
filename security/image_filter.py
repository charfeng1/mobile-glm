"""
Image filters to mitigate prompt injection attacks.

Usage:
    from security.image_filter import flatten_low_contrast

    # Before sending screenshot to vision model
    safe_img = flatten_low_contrast(screenshot, threshold=15)
"""

import numpy as np
from PIL import Image


def flatten_low_contrast(img: Image.Image, threshold: int = 15) -> Image.Image:
    """
    Remove low-contrast hidden text by quantizing colors.

    This merges similar colors together, eliminating subtle variations
    that attackers use to hide injection text (typically 5% contrast).

    Args:
        img: PIL Image to process
        threshold: Max RGB difference to merge (15-25 works well)

    Returns:
        Processed image with low-contrast text removed

    Example:
        >>> img = Image.open("screenshot.png")
        >>> safe = flatten_low_contrast(img, threshold=15)
        >>> # Injection text at 5% contrast is now gone
    """
    arr = np.array(img)
    quantized = (arr // (threshold + 1)) * (threshold + 1)
    return Image.fromarray(quantized.astype(np.uint8))


def preprocess_screenshot(img: Image.Image) -> Image.Image:
    """
    Full preprocessing pipeline for screenshots before vision model.

    Currently applies:
    - Color flattening to remove hidden injection text

    Args:
        img: Raw screenshot

    Returns:
        Safe screenshot ready for vision model
    """
    return flatten_low_contrast(img, threshold=15)
