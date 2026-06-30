# batch_sharpness_analyzer.py
# Multi-measure sharpness analysis for batch photo sessions.
#
# Research basis: Pertuz et al. 2013 ("Analysis of focus measure operators in
# shape-from-focus", Pattern Recognition) and the OpenCV comparative study
# show that no single focus measure is universally best. We fuse three
# complementary operators that topped the rankings:
#   - Laplacian Variance (Pech-Pacheco 2000) — fast but noise-sensitive
#   - Tenengrad / Sobel energy (Krotkov 1987) — robust to moderate noise
#   - Modified Laplacian (Nayar & Nakagawa 1994) — best for fine-detail focus
#
# Tile-based p90 regional measurement (Day 9): Instead of averaging each
# measure over every pixel (which drowns a sharp subject in a sea of
# intentional bokeh), we split the frame into a 16×16 grid, compute each
# measure per tile, and take the 90th percentile of the tile distribution.
# This asks "how sharp is the sharpest 10% of the frame?" — the same question
# a camera's AF system answers. Portraits with bokeh no longer get punished
# for having a blurry background.

import cv2
import numpy as np
import rawpy
from pathlib import Path
import csv
from datetime import datetime

# Per-format normalized sharpness thresholds.
# Score = geometric mean of per-megapixel Laplacian variance, Tenengrad, and
# Modified Laplacian, each divided by its empirical scale constant so the three
# measures contribute equally.
#
# ⚠️ Recalibration recommended after switching from single-measure to fusion.
# Run `python phase1_technical/batch_sharpness_analyzer.py <folder>` on a set of
# known-sharp and known-blurry photos and pick a threshold around the split.
# Previous single-measure threshold was 2.3 (Laplacian-only); the new combined
# score lands in a similar 1–10 band, so 2.3 is a reasonable starting point.
THRESHOLDS = {
    'JPG':  2.3,
    'JPEG': 2.3,
    'RAF':  2.3,
    'NEF':  2.3,
    '3FR':  2.3,
}

# Tile grid and aggregation percentile for regional focus measurement.
# 16×16 = 256 tiles; p90 = "sharpest 10%" ≈ 25 tiles — a realistic floor for
# a sharp subject region. See module header for rationale.
_TILE_GRID = 16
_TILE_PERCENTILE = 90.0

# Scale constants calibrated on Photo culling playground pics/knwonSharpknownBlurry/
# (Day 9, 2026-04-23). Tuned so all 6 high-contrast user-labeled-sharp photos
# fuse to >= 2.3 and the strongly-mis-focused DSC_4203 fuses to ~0.3.
#
# Known false-label cases the p90 algorithm CANNOT separate (contrast-sensitive
# focus measures; real fix is Phase 2 face-region scoring):
#   - Low-contrast sharp subjects score below threshold. DSC_0879 (baby portrait,
#     soft backlight) fuses to ~1.1: the softly-lit face produces smaller
#     gradients than high-contrast blurry scenes — no monotonic scale transform
#     can reorder this.
#   - High-contrast blurry scenes score above threshold. DSC_4409 (~5.9) and
#     DSCF0011 (~3.9) both have enough scene detail that their p90 tiles rival
#     in-focus photos.
# Accept these edge cases for Phase 1; Phase 2 supplements with face-region focus.
_LAP_SCALE = 12.0      # p90 of per-tile Laplacian variance
_TEN_SCALE = 1200.0    # p90 of per-tile Tenengrad (Sobel² mean)
_MLAP_SCALE = 7.0      # p90 of per-tile Modified Laplacian


def _per_tile(arr: np.ndarray, reducer) -> np.ndarray:
    """
    Split a 2D array into a _TILE_GRID × _TILE_GRID grid and apply `reducer`
    (np.var or np.mean) to each tile. Returns a (_TILE_GRID, _TILE_GRID) array.

    Uses reshape + axis reduction instead of a Python loop — ~10× faster for
    large images. Crops a few edge pixels when dims aren't divisible by 16,
    which is the same behaviour as the previous np.array_split approach.
    """
    H, W = arr.shape
    h_t = H // _TILE_GRID
    w_t = W // _TILE_GRID
    tiles = arr[:h_t * _TILE_GRID, :w_t * _TILE_GRID].reshape(
        _TILE_GRID, h_t, _TILE_GRID, w_t
    )
    if reducer is np.mean:
        return tiles.mean(axis=(1, 3))
    else:
        return tiles.var(axis=(1, 3))


def _compute_focus_measures(gray: np.ndarray) -> dict:
    """
    Compute three complementary focus measures per tile on a grayscale image.

    Returns tile arrays (shape _TILE_GRID × _TILE_GRID), not scalars, so the
    caller can take a percentile of the tile distribution. This is what lets
    shallow-DoF portraits score correctly: 75–80% of tiles are intentional
    bokeh and will land low, but the 15–20% subject tiles stay high, so a
    90th percentile captures the in-focus region rather than averaging it
    away.

    All three measures are "higher = sharper".
    """
    gray64 = gray.astype(np.float64)

    # 1) Laplacian — full-image convolution, then per-tile variance.
    #    (Variance of a convolution can't be decomposed tile-wise without
    #    boundary artifacts, so we convolve once and slice.)
    laplacian = cv2.Laplacian(gray64, cv2.CV_64F)
    lap_var_tiles = _per_tile(laplacian, np.var)

    # 2) Tenengrad — Sobel² magnitude, then per-tile mean.
    gx = cv2.Sobel(gray64, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray64, cv2.CV_64F, 0, 1, ksize=3)
    tenengrad_field = gx * gx + gy * gy
    tenengrad_tiles = _per_tile(tenengrad_field, np.mean)

    # 3) Modified Laplacian — |Lx| + |Ly| with 1D [-1, 2, -1] kernels, then
    #    per-tile mean. Emphasizes fine detail without squaring.
    kernel = np.array([[-1.0, 2.0, -1.0]], dtype=np.float64)
    mlap_x = cv2.filter2D(gray64, cv2.CV_64F, kernel)
    mlap_y = cv2.filter2D(gray64, cv2.CV_64F, kernel.T)
    mlap_field = np.abs(mlap_x) + np.abs(mlap_y)
    mlap_tiles = _per_tile(mlap_field, np.mean)

    return {
        'laplacian_variance': lap_var_tiles,
        'tenengrad': tenengrad_tiles,
        'modified_laplacian': mlap_tiles,
    }


def get_threshold(image_path, thresholds=None):
    """Return the sharpness threshold for a given file format."""
    ext = image_path.suffix.lstrip('.').upper()
    thresholds = thresholds or THRESHOLDS
    return thresholds.get(ext, THRESHOLDS['JPG'])

def detect_sharpness(image_path):
    """
    Detect sharpness via tile-based p90 multi-measure fusion.

    For each of Laplacian variance, Tenengrad, and Modified Laplacian, compute
    a 16×16 tile grid of the measure, then take the 90th percentile of the
    tile distribution. The three p90 values are scale-normalized and fused by
    geometric mean. This isolates the sharpest region of the frame (as a
    camera AF system would), so shallow-DoF portraits score on their subject
    rather than being pulled down by intentional bokeh.

    Returns:
        dict with keys:
            'normalized_score'          — fused score (higher = sharper)
            'raw_score'                 — p90 Laplacian variance (diagnostic)
            'p90_laplacian_variance'    — p90 of per-tile Laplacian variance
            'p90_tenengrad'             — p90 of per-tile Tenengrad
            'p90_modified_laplacian'    — p90 of per-tile Modified Laplacian
            'width', 'height'           — image dimensions
        or None on error.
    """
    try:
        # Load image (raw via rawpy demosaic, with thumbnail fallback)
        if image_path.suffix.upper() in ['.RAF', '.NEF', '.3FR']:
            try:
                with rawpy.imread(str(image_path)) as raw:
                    rgb = raw.postprocess(
                        use_camera_wb=True,
                        half_size=True,
                        output_bps=8,
                    )
            except Exception:
                with rawpy.imread(str(image_path)) as raw:
                    thumb = raw.extract_thumb()
                    buf = np.frombuffer(thumb.data, dtype=np.uint8)
                    rgb = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                    rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)
        else:
            rgb = cv2.imread(str(image_path))
            if rgb is None:
                return None
            rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)

        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        height, width = gray.shape

        tile_measures = _compute_focus_measures(gray)

        # p90 across all 256 tiles for each measure. Percentile (not max)
        # because max is fooled by a single noisy tile; p90 needs ~25 tiles to
        # be in focus, matching the minimum realistic subject-in-focus area.
        p90_lap = float(np.percentile(tile_measures['laplacian_variance'], _TILE_PERCENTILE))
        p90_ten = float(np.percentile(tile_measures['tenengrad'], _TILE_PERCENTILE))
        p90_mlap = float(np.percentile(tile_measures['modified_laplacian'], _TILE_PERCENTILE))

        # Geometric mean of scale-normalized p90 values. Geometric mean still
        # penalizes any single weak measure, but since all three are now
        # computed on the same sharp-region tiles, a truly sharp subject
        # pushes all three up together — they no longer fight each other.
        normalized_score = float(np.cbrt(
            (p90_lap / _LAP_SCALE)
            * (p90_ten / _TEN_SCALE)
            * (p90_mlap / _MLAP_SCALE)
        ))

        return {
            'raw_score': p90_lap,
            'normalized_score': normalized_score,
            'p90_laplacian_variance': p90_lap,
            'p90_tenengrad': p90_ten,
            'p90_modified_laplacian': p90_mlap,
            'width': width,
            'height': height,
        }
    except Exception as e:
        print(f"   ⚠️  Error processing {image_path.name}: {e}")
        return None


def detect_sharpness_array(rgb: np.ndarray) -> dict | None:
    """
    Run sharpness detection on a pre-decoded RGB numpy array (H×W×3, uint8).

    Same logic as detect_sharpness() but skips the file I/O and rawpy decode —
    called by quality_analyzer when the RAW has already been decoded once and
    shared across all scorers.
    """
    try:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        height, width = gray.shape

        tile_measures = _compute_focus_measures(gray)

        p90_lap  = float(np.percentile(tile_measures['laplacian_variance'],   _TILE_PERCENTILE))
        p90_ten  = float(np.percentile(tile_measures['tenengrad'],            _TILE_PERCENTILE))
        p90_mlap = float(np.percentile(tile_measures['modified_laplacian'],   _TILE_PERCENTILE))

        normalized_score = float(np.cbrt(
            (p90_lap  / _LAP_SCALE)
            * (p90_ten  / _TEN_SCALE)
            * (p90_mlap / _MLAP_SCALE)
        ))

        return {
            'raw_score': p90_lap,
            'normalized_score': normalized_score,
            'p90_laplacian_variance': p90_lap,
            'p90_tenengrad': p90_ten,
            'p90_modified_laplacian': p90_mlap,
            'width': width,
            'height': height,
        }
    except Exception as e:
        print(f"   ⚠️  Sharpness array analysis error: {e}")
        return None


def analyze_folder(folder_path, thresholds=None, output_csv=True):
    """
    Analyze all photos in a folder using per-format sharpness thresholds.

    Args:
        folder_path: Path to folder with photos
        thresholds: dict of format → threshold overrides (defaults to THRESHOLDS)
        output_csv: Save results to CSV file

    Returns:
        dict: Analysis results
    """
    folder = Path(folder_path)
    thresholds = thresholds or THRESHOLDS

    # Supported formats
    formats = ['.jpg', '.jpeg', '.JPG', '.JPEG', '.RAF', '.NEF', '.nef']

    # Find all photos
    photos = []
    for fmt in formats:
        photos.extend(folder.glob(f'*{fmt}'))

    if not photos:
        print(f"❌ No photos found in {folder}")
        return None

    print("=" * 70)
    print(f"📊 BATCH SHARPNESS ANALYSIS")
    print("=" * 70)
    print(f"📁 Folder: {folder}")
    print(f"📸 Found {len(photos)} photos")
    print(f"🎯 Thresholds: JPG={thresholds.get('JPG', 15)}  RAF={thresholds.get('RAF', 100)}  NEF={thresholds.get('NEF', 100)}")
    print("=" * 70)

    results = []
    sharp_count = 0
    blurry_count = 0

    print("\n🔍 Analyzing photos...\n")

    for i, photo_path in enumerate(sorted(photos), 1):
        print(f"[{i}/{len(photos)}] {photo_path.name}...", end=" ")

        result = detect_sharpness(photo_path)

        if result is None:
            print("SKIP")
            continue

        score = result['normalized_score']
        fmt_threshold = get_threshold(photo_path, thresholds)
        is_sharp = score >= fmt_threshold
        classification = "SHARP" if is_sharp else "BLURRY"

        if is_sharp:
            sharp_count += 1
            print(f"✅ {score:.1f} norm ({result['raw_score']:.0f} raw, {result['width']}x{result['height']}) ({classification})")
        else:
            blurry_count += 1
            print(f"⚠️  {score:.1f} norm ({result['raw_score']:.0f} raw, {result['width']}x{result['height']}) ({classification})")

        results.append({
            'filename': photo_path.name,
            'path': str(photo_path),
            'normalized_score': score,
            'raw_score': result['raw_score'],
            'p90_laplacian_variance': result['p90_laplacian_variance'],
            'p90_tenengrad': result['p90_tenengrad'],
            'p90_modified_laplacian': result['p90_modified_laplacian'],
            'width': result['width'],
            'height': result['height'],
            'classification': classification,
            'camera': detect_camera(photo_path.name)
        })

    if not results:
        print("\n❌ No photos could be analyzed.")
        return None

    # Summary statistics (on normalized scores)
    scores = [r['normalized_score'] for r in results]

    print("\n" + "=" * 70)
    print("📈 ANALYSIS SUMMARY")
    print("=" * 70)
    print(f"Total photos analyzed: {len(results)}")
    print(f"✅ Sharp photos: {sharp_count} ({sharp_count/len(results)*100:.1f}%)")
    print(f"⚠️  Blurry photos: {blurry_count} ({blurry_count/len(results)*100:.1f}%)")
    print(f"\n📊 Normalized Score Statistics:")
    print(f"   Mean: {np.mean(scores):.1f}")
    print(f"   Median: {np.median(scores):.1f}")
    print(f"   Min: {np.min(scores):.1f}")
    print(f"   Max: {np.max(scores):.1f}")
    print(f"   Std Dev: {np.std(scores):.1f}")

    # Show blurry photos for review
    if blurry_count > 0:
        print(f"\n⚠️  BLURRY PHOTOS TO REVIEW:")
        blurry_photos = [r for r in results if r['classification'] == 'BLURRY']
        for photo in sorted(blurry_photos, key=lambda x: x['normalized_score']):
            print(f"   {photo['filename']}: {photo['normalized_score']:.1f}")

    print("=" * 70)

    # Save to CSV
    if output_csv and results:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = folder / f"sharpness_analysis_{timestamp}.csv"

        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'filename', 'normalized_score',
                'p90_laplacian_variance', 'p90_tenengrad', 'p90_modified_laplacian',
                'raw_score', 'width', 'height',
                'classification', 'camera', 'path'
            ])
            writer.writeheader()
            writer.writerows(results)

        print(f"\n💾 Results saved to: {csv_path.name}")

    return {
        'results': results,
        'sharp_count': sharp_count,
        'blurry_count': blurry_count,
        'stats': {
            'mean': np.mean(scores),
            'median': np.median(scores),
            'min': np.min(scores),
            'max': np.max(scores),
            'std': np.std(scores)
        }
    }


def detect_camera(filename):
    """Detect camera from filename pattern."""
    if filename.startswith('DSCF'):
        return 'Fujifilm'
    elif filename.startswith('DSC_'):
        return 'Nikon'
    else:
        return 'Unknown'


if __name__ == "__main__":
    import sys

    # Usage: python phase1_technical/batch_sharpness_analyzer.py /path/to/folder [threshold]

    if len(sys.argv) < 2:
        print("Usage: python phase1_technical/batch_sharpness_analyzer.py <folder_path> [jpg_threshold] [raw_threshold]")
        print("\nExample:")
        print("  python phase1_technical/batch_sharpness_analyzer.py ~/Pictures/session")
        print("  python phase1_technical/batch_sharpness_analyzer.py ~/Pictures/session 15 100")
        sys.exit(1)

    folder_path = sys.argv[1]
    jpg_threshold = float(sys.argv[2]) if len(sys.argv) > 2 else THRESHOLDS['JPG']
    raw_threshold = float(sys.argv[3]) if len(sys.argv) > 3 else THRESHOLDS['RAF']

    custom_thresholds = {**THRESHOLDS, 'JPG': jpg_threshold, 'JPEG': jpg_threshold,
                         'RAF': raw_threshold, 'NEF': raw_threshold}

    analyze_folder(folder_path, thresholds=custom_thresholds)
