"""
Test script for weighted quality scoring.
Compares old 50/50 weighting vs new 65/35 weighting.

Run this to see how the new weighting affects scores.
"""

import sys
import os

# Add parent directory to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from phase1_technical.quality_analyzer import analyze_photo_quality, get_quality_breakdown, calculate_sharpness
from phase1_technical.exposure import analyze_exposure


def compare_weightings(image_path: str):
    """
    Compare old 50/50 vs new 65/35 weighting on a single photo.
    Shows how the weighting change affects the final score.
    """
    
    print(f"\n{'='*70}")
    print(f"📸 Analyzing: {os.path.basename(image_path)}")
    print(f"{'='*70}\n")
    
    try:
        # Get individual scores
        sharpness = calculate_sharpness(image_path)
        exposure = analyze_exposure(image_path)
        
        print(f"Component Scores:")
        print(f"  Sharpness: {sharpness['sharpness_score']}/100")
        print(f"  Exposure:  {exposure['exposure_score']}/100")
        print()
        
        # Calculate old method (50/50)
        old_score = (
            sharpness['sharpness_score'] * 0.5 + 
            exposure['exposure_score'] * 0.5
        )
        
        # Calculate new method (65/35)
        new_score = (
            sharpness['sharpness_score'] * 0.65 + 
            exposure['exposure_score'] * 0.35
        )
        
        # Show comparison
        print(f"Scoring Comparison:")
        print(f"  Old method (50/50): {old_score:.1f}/100")
        print(f"  New method (65/35): {new_score:.1f}/100")
        
        difference = new_score - old_score
        print(f"  Difference: {difference:+.1f} points")
        
        # Interpret the change
        print()
        if abs(difference) < 1:
            print("  → Minimal change (scores are balanced)")
        elif difference > 0:
            print("  → Score IMPROVED (sharpness is good)")
        else:
            print("  → Score DECREASED (sharpness is weak)")
        
        print()
        
    except Exception as e:
        print(f"❌ Error analyzing photo: {e}")
        import traceback
        traceback.print_exc()


def test_multiple_photos():
    """
    Test the new weighting on different photo types.
    
    Replace these paths with actual photos from your cameras:
    - Sharp but dark
    - Blurry but well-exposed
    - Sharp and well-exposed
    - Blurry and dark
    """
    
    test_photos = [
        # TODO: Replace with your actual photo paths
        "/path/to/sharp_but_dark.RAF",
        "/path/to/blurry_but_exposed.NEF", 
        "/path/to/perfect_photo.RAF",
        "/path/to/bad_photo.NEF"
    ]
    
    print("\n" + "="*70)
    print("WEIGHTING COMPARISON TEST")
    print("Testing: Old 50/50 vs New 65/35 (Sharpness-prioritized)")
    print("="*70)
    
    for photo_path in test_photos:
        if not os.path.exists(photo_path):
            print(f"\n⚠️  Photo not found: {photo_path}")
            print("   Update test_photos list with actual file paths")
            continue
        
        compare_weightings(photo_path)


def show_detailed_breakdown(image_path: str):
    """
    Show the full quality breakdown for a single photo.
    Uses the new 65/35 weighting.
    """
    
    if not os.path.exists(image_path):
        print(f"❌ Photo not found: {image_path}")
        return
    
    try:
        breakdown = get_quality_breakdown(image_path)
        print(breakdown)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    """
    Usage:
    
    # Test weighting comparison on multiple photos:
    python test_weighted_scoring.py
    
    # Or test a single photo with detailed breakdown:
    # Uncomment the line below and add your photo path
    # show_detailed_breakdown("/path/to/your/photo.RAF")
    """
    
    # Run the multi-photo comparison test
    test_multiple_photos()
    
    # Uncomment to test a single photo with full breakdown:
    # show_detailed_breakdown("/path/to/your/test_photo.RAF")
