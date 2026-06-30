import cv2
import numpy as np
import rawpy
from pathlib import Path


def load_as_gray(image_path: str) -> np.ndarray:
    """Load a JPEG or RAW image as a grayscale numpy array."""
    path = Path(image_path)
    if path.suffix.upper() in ('.RAF', '.NEF', '.3FR'):
        with rawpy.imread(image_path) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    buf = np.frombuffer(thumb.data, dtype=np.uint8)
                    rgb = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                else:
                    rgb = thumb.data
            except rawpy.LibRawNoThumbnailError:
                rgb = raw.postprocess()
        return cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)
    else:
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"Could not load image: {image_path}")
        return img
