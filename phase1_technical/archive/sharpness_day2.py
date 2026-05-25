# sharpness_detector.py
# Day 2: Building sharpness detection for KaMeRa

import cv2
import numpy as np
import rawpy
from pathlib import Path

def detect_sharpness(image_path):
    """
    Analyze image sharpness using Laplacian variance.

    Args:
        image_path: Path to image file (JPG, RAF, NEF, etc.)

    Returns:
        float: Sharpness score (higher = sharper)
    """
    print(f"\n📸 Analyzing: {Path(image_path).name}")

    # Load image (handle RAW or regular)
    if image_path.endswith(('.RAF', '.NEF', '.nef')):
        # RAW file - generate preview
        with rawpy.imread(image_path) as raw:
            rgb = raw.postprocess()
    else:
        # Regular image file
        rgb = cv2.imread(image_path)
        rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)

    # Convert to grayscale (remember: brightness map!)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    print(f"   Converted to grayscale: {gray.shape}")

    # Apply Laplacian operator (detects edges)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    print(f"   Laplacian calculated: {laplacian.shape}")

    # Calculate variance (how spread out are edge values?)
    variance = laplacian.var()
    print(f"   ✅ Sharpness score: {variance:.2f}")

    return variance


def classify_sharpness(score, threshold=200):
    """
    Classify image as sharp or blurry based on score.

    Args:
        score: Sharpness variance score
        threshold: Minimum score for "sharp" (calibrate per camera)

    Returns:
        str: "SHARP" or "BLURRY"
    """
    if score >= threshold:
        return "✅ SHARP"
    else:
        return "⚠️ BLURRY"


# Test with YOUR photos
if __name__ == "__main__":
    print("=" * 70)
    print("🎓 DAY 2: Sharpness Detection Test")
    print("=" * 70)

    # Test images (update these paths to YOUR photos)
    import sys as _sys
    if len(_sys.argv) < 2:
        print("Usage: python phase1_technical/sharpness.py <file_or_folder> [file2 ...]")
        _sys.exit(1)

    test_images = []
    for _arg in _sys.argv[1:]:
        if os.path.isdir(_arg):
            exts = {'.jpg', '.jpeg', '.raf', '.nef', '.RAF', '.NEF'}
            test_images.extend(
                os.path.join(_arg, f) for f in sorted(os.listdir(_arg))
                if os.path.splitext(f)[1] in exts
            )
        else:
            test_images.append(_arg)

    print("\n📊 Testing sharpness detection:\n")

    for img_path in test_images:
        try:
            score = detect_sharpness(img_path)
            classification = classify_sharpness(score)
            print(f"   Result: {classification}")
            print("-" * 70)
        except Exception as e:
            print(f"   ❌ Error: {e}\n")

    print("\n" + "=" * 70)
    print("🎓 WHAT YOU JUST BUILT:")
    print("=" * 70)
    print("""
✅ Sharpness Detection Algorithm
   - Loads RAW or regular images
   - Converts to grayscale (brightness map)
   - Applies Laplacian (edge detector)
   - Calculates variance (spread of edge values)
   - Returns sharpness score

📊 Next Steps:
   - Test with YOUR sharp and blurry photos
   - Find the right threshold for each camera
   - This becomes the foundation for auto-culling!
""")
