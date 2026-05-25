#!/usr/bin/env python3
"""
Quality Analyzer Demo
Shows how the 65/35 weighted scoring works with real photos.

This is your "wow, it works!" moment script.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from phase1_technical.quality_analyzer import get_quality_breakdown


def demo_single_photo(image_path: str):
    """
    Analyze a single photo and show the full quality breakdown.
    This is what you'll see in the app's UI eventually.
    """
    
    if not os.path.exists(image_path):
        print(f"❌ Photo not found: {image_path}")
        print(f"   Pass a valid path as an argument.")
        return
    
    print(f"\n📸 Analyzing: {os.path.basename(image_path)}")
    print(f"   Full path: {image_path}\n")
    
    try:
        # This is the main function you'll use in your app
        breakdown = get_quality_breakdown(image_path)
        print(breakdown)
        
        print("\n💡 What this means:")
        print("   - Sharpness/exposure weighting is configurable in Settings → Model")
        print("   - Default split is 65% sharpness / 35% exposure")
        print()
        
    except Exception as e:
        print(f"\n❌ Error analyzing photo:")
        print(f"   {e}")
        print()
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/demo_quality_analyzer.py <file_or_folder> [file2 ...]")
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

    for photo in paths:
        demo_single_photo(photo)
        print("\n" + "=" * 70 + "\n")

