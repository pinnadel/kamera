"""
Sharpness Threshold Calibration Tool

Point this at a folder of photos you've already eyeballed as SHARP.
It prints the score distribution so you can read off where the threshold
should sit for your camera/lens combination.

Usage:
    python calibrate_sharpness.py <folder_path>
    python calibrate_sharpness.py <folder_path> --format JPG
    python calibrate_sharpness.py <folder_path> --format RAF
"""

import sys
import os
import argparse
import numpy as np
from pathlib import Path

from phase1_technical.batch_sharpness_analyzer import detect_sharpness, THRESHOLDS


def calibrate(folder_path: str, fmt_filter: str = None):
    folder = Path(folder_path)

    if not folder.exists():
        print(f"❌ Folder not found: {folder_path}")
        sys.exit(1)

    # Collect files
    all_formats = ['.jpg', '.jpeg', '.JPG', '.JPEG', '.RAF', '.NEF', '.nef']
    photos = []
    for ext in all_formats:
        photos.extend(folder.glob(f'*{ext}'))

    if fmt_filter:
        photos = [p for p in photos if p.suffix.lstrip('.').upper() == fmt_filter.upper()]

    if not photos:
        print(f"❌ No matching photos found in {folder}")
        sys.exit(1)

    print("=" * 60)
    print("🔬 SHARPNESS CALIBRATION")
    print("   (Run on photos YOU know are sharp)")
    print("=" * 60)
    print(f"📁 Folder: {folder}")
    print(f"📸 Photos: {len(photos)}{f'  (filtered to {fmt_filter})' if fmt_filter else ''}")
    print()

    results = []
    failed = []

    for i, photo in enumerate(sorted(photos), 1):
        print(f"[{i}/{len(photos)}] {photo.name}...", end=" ", flush=True)
        result = detect_sharpness(photo)
        if result is None:
            print("SKIP")
            failed.append(photo.name)
            continue
        score = result['normalized_score']
        results.append((photo.name, score, result['raw_score'], result['width'], result['height']))
        print(f"{score:.1f}")

    if not results:
        print("❌ No photos could be analyzed.")
        sys.exit(1)

    scores = [r[1] for r in results]
    fmt = results[0][0].split('.')[-1].upper()
    current_threshold = THRESHOLDS.get(fmt, THRESHOLDS['JPG'])

    print()
    print("=" * 60)
    print("📊 SCORE DISTRIBUTION (normalized, known-sharp photos)")
    print("=" * 60)
    print(f"  Count:      {len(scores)}")
    print(f"  Min:        {np.min(scores):.1f}")
    print(f"  5th pct:    {np.percentile(scores, 5):.1f}   ← safe lower bound")
    print(f"  25th pct:   {np.percentile(scores, 25):.1f}")
    print(f"  Median:     {np.median(scores):.1f}")
    print(f"  Mean:       {np.mean(scores):.1f}")
    print(f"  75th pct:   {np.percentile(scores, 75):.1f}")
    print(f"  Max:        {np.max(scores):.1f}")
    print()
    print(f"  Current threshold ({fmt}): {current_threshold}")
    suggested = round(np.percentile(scores, 5) * 0.8, 1)
    print(f"  Suggested threshold:      {suggested}")
    print(f"  (80% of 5th percentile — catches soft shots, allows artistic blur)")
    print()

    # Show which photos would be rejected at current vs suggested threshold
    rejected_current = [r for r in results if r[1] < current_threshold]
    rejected_suggested = [r for r in results if r[1] < suggested]

    print("=" * 60)
    print("🎯 THRESHOLD IMPACT")
    print("=" * 60)
    print(f"  At current threshold  ({current_threshold:5.1f}): "
          f"{len(rejected_current)}/{len(results)} known-sharp photos would be REJECTED ❌")
    print(f"  At suggested threshold ({suggested:5.1f}): "
          f"{len(rejected_suggested)}/{len(results)} known-sharp photos would be REJECTED ❌")

    if rejected_current:
        print(f"\n  False rejects at current threshold:")
        for name, score, *_ in sorted(rejected_current, key=lambda x: x[1]):
            print(f"    {name}: {score:.1f}")

    if failed:
        print(f"\n  ⚠️  Skipped ({len(failed)} files could not be read):")
        for f in failed:
            print(f"    {f}")

    print()
    print("=" * 60)
    print("📝 NEXT STEP")
    print("=" * 60)
    print(f"  Update THRESHOLDS in phase1_technical/batch_sharpness_analyzer.py:")
    print(f"  '{fmt}': {suggested}  # was {current_threshold}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calibrate sharpness threshold from known-sharp photos."
    )
    parser.add_argument("folder", help="Path to folder of known-sharp photos")
    parser.add_argument("--format", help="Filter to one format: JPG, RAF, NEF", default=None)
    args = parser.parse_args()

    calibrate(args.folder, args.format)
