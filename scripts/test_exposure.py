"""
Test exposure analysis on real photos.
Usage: python scripts/test_exposure.py <folder_or_file> [file2 ...]
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from phase1_technical.exposure import analyze_exposure


def test_exposure_analysis(paths):
    """Test exposure analyzer on the given file paths."""
    for photo_path in paths:
        if not os.path.exists(photo_path):
            print(f"⚠️  Photo not found: {photo_path}")
            continue

        print(f"\n📸 Analyzing: {os.path.basename(photo_path)}")
        print("=" * 60)

        try:
            results = analyze_exposure(photo_path)

            # Display results
            print(f"Mean Brightness:      {results['mean_brightness']:.1f} / 255")
            print(f"Contrast (Std Dev):   {results['std_brightness']:.1f}")
            print(f"Highlight Clipping:   {results['highlight_clip_pct']:.2f}%")
            print(f"Shadow Clipping:      {results['shadow_clip_pct']:.2f}%")
            print(f"Exposure Score:       {results['exposure_score']} / 100")

            # Interpret the score
            if results['exposure_score'] >= 80:
                print("✅ Excellent exposure!")
            elif results['exposure_score'] >= 60:
                print("👍 Good exposure")
            elif results['exposure_score'] >= 40:
                print("⚠️  Recoverable, but needs work")
            else:
                print("❌ Poor exposure, likely unusable")

        except Exception as e:
            print(f"❌ Error analyzing photo: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_exposure.py <folder_or_file> [file2 ...]")
        sys.exit(1)

    paths = []
    for arg in sys.argv[1:]:
        p = os.path.abspath(arg)
        if os.path.isdir(p):
            exts = {'.jpg', '.jpeg', '.raf', '.nef', '.RAF', '.NEF'}
            paths.extend(
                os.path.join(p, f) for f in sorted(os.listdir(p))
                if os.path.splitext(f)[1] in exts
            )
        else:
            paths.append(p)

    test_exposure_analysis(paths)
