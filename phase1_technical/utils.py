import cv2
import numpy as np
import rawpy
from pathlib import Path

from backend.constants import RAW_DECODE_SUFFIXES


def load_as_gray(image_path: str) -> np.ndarray:
    """Load a JPEG or RAW image as a grayscale numpy array."""
    path = Path(image_path)
    if path.suffix.lower() in RAW_DECODE_SUFFIXES:
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
            # OpenCV has no built-in HEIF/HIF decoder — falls through here for
            # .hif (and any other format Pillow can read but cv2 can't).
            # pillow-heif's opener is registered globally in backend/main.py.
            from PIL import Image as _PILImage
            with _PILImage.open(image_path) as pil_img:
                return np.array(pil_img.convert("L"))
        return img
