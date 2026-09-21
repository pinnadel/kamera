"""
Re-score sharpness for existing Photos-corpus rows with the edge-width metric.

New analyses pick it up automatically (analyze_photo_quality(corpus="photos"));
this brings the rows analysed before 2026-09-19 onto the same scale, so a
library isn't split between two incompatible definitions of "sharp".

Follows the pipeline's own path per row — same decode, same face bbox, same
_photos_sharpness — so a backfilled score equals what a fresh analysis gives.
Rewrites images.sharpness_score and images.overall_score only.

    venv/bin/python scripts/backfill_edge_sharpness.py            # dry run: measure, report, write nothing
    venv/bin/python scripts/backfill_edge_sharpness.py --apply    # backup DB, then write
    venv/bin/python scripts/backfill_edge_sharpness.py --apply --reuse-results   # skip re-measuring, write the last dry run
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "pca.db"


def _measure(row):
    """(id, path) → measurement. Runs in a worker; imports are per-process."""
    import numpy as np
    import pillow_heif
    from PIL import Image, ImageOps

    from backend.constants import RAW_DECODE_SUFFIXES
    from phase1_technical.quality_analyzer import (
        _apply_edge_sharpness, decode_raw, measure_edge_sharpness,
    )
    from phase2_quality.face_analyzer import analyze_faces, analyze_faces_array

    pillow_heif.register_heif_opener()
    image_id, path = row
    try:
        # Mirror the pipeline's own branch so a backfilled score equals what a
        # fresh analysis would write.
        if Path(path).suffix.lower() in RAW_DECODE_SUFFIXES:
            rgb, _ = decode_raw(path)
            face = analyze_faces_array(rgb)
        else:
            rgb = np.asarray(ImageOps.exif_transpose(Image.open(path).convert("RGB")))
            face = analyze_faces(path)
        edge = measure_edge_sharpness(rgb, face)
        result = _apply_edge_sharpness(edge, "photos", face, None, path, rgb.shape[:2])
        return {
            "id": image_id,
            "score": result["sharpness_score"],
            "metric": result.get("metric", "legacy"),
            "face_score": face.get("face_sharpness_score") if edge else None,
            "edge_width_px": (edge or {}).get("frame_width_px"),
            "face_edge_width_px": (edge or {}).get("face_width_px"),
        }
    except Exception as exc:  # one bad file must not sink a 1900-row backfill
        return {"id": image_id, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write to the DB (default: dry run)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows (testing)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--reuse-results", action="store_true",
                    help="load data/edge_sharpness_backfill.json from the last run instead of re-measuring")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, file_path, sharpness_score, exposure_score, face_sharpness_score FROM images "
        "WHERE source_type = 'photos' AND sharpness_score IS NOT NULL ORDER BY id"
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    old = {r[0]: r[2] for r in rows}
    exposure = {r[0]: r[3] for r in rows}
    old_face = {r[0]: r[4] for r in rows}
    print(f"{len(rows)} Photos rows; measuring with {args.workers} workers…", flush=True)

    out = ROOT / "data" / "edge_sharpness_backfill.json"
    if args.reuse_results:
        wanted = set(old)
        results = [r for r in json.loads(out.read_text()) if r["id"] in wanted]
        print(f"reusing {len(results)} measurements from {out.name}")
    else:
        started = time.time()
        with Pool(args.workers) as pool:
            results = []
            for i, res in enumerate(pool.imap_unordered(_measure, [(r[0], r[1]) for r in rows], chunksize=4), 1):
                results.append(res)
                if i % 200 == 0:
                    print(f"  {i}/{len(rows)}  ({time.time() - started:.0f}s)", flush=True)

    ok = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]
    fallback = [r for r in ok if r["metric"] == "legacy"]
    new = [r["score"] for r in ok]
    print(f"\nmeasured {len(ok)} · fell back to legacy on featureless frames: {len(fallback)} · errors: {len(errors)}")
    for e in errors[:5]:
        print("  error", e)
    import numpy as np
    o = np.array([old[r["id"]] for r in ok], float)
    n = np.array(new, float)
    print(f"share ≥95   old {np.mean(o >= 95):.0%}  →  new {np.mean(n >= 95):.0%}")
    print(f"Sharp ≥70   old {np.mean(o >= 70):.0%}  →  new {np.mean(n >= 70):.0%}")
    print(f"Blurry <40  old {np.mean(o < 40):.0%}  →  new {np.mean(n < 40):.0%}")
    print(f"new score p5/25/50/75/95: {np.percentile(n, [5, 25, 50, 75, 95]).round(0).tolist()}")
    faces = [r for r in ok if r.get("face_score") is not None]
    if faces:
        fo = np.array([old_face[r["id"]] for r in faces if old_face[r["id"]] is not None], float)
        fn = np.array([r["face_score"] for r in faces], float)
        print(f"\nface sharpness on {len(faces)} faces — was Laplacian variance "
              f"(p5/50/95 {np.percentile(fo, [5, 50, 95]).round(0).tolist()}), "
              f"now 0-100 (p5/50/95 {np.percentile(fn, [5, 50, 95]).round(0).tolist()})")
        print(f"faces below the default soft-face floor of 20: {int((fn < 20).sum())}")
    if not args.reuse_results:
        out.write_text(json.dumps(results))
        print(f"per-row results → {out}")

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return 0

    from phase1_technical.quality_analyzer import compute_overall_score, get_sharpness_weight

    backup = DB_PATH.with_name("pca.db.pre-edge-sharpness-backup")
    if backup.exists():
        backup.unlink()
    dst = sqlite3.connect(backup)
    conn.backup(dst)  # WAL-safe, unlike copying the file under a live backend
    dst.close()
    print(f"backup → {backup}")

    weight = get_sharpness_weight()
    with conn:
        for r in ok:
            overall = compute_overall_score(r["score"], exposure[r["id"]], sharpness_weight=weight)
            # face_sharpness_score is only overwritten when this run actually
            # measured a face — a row whose face has too few edges keeps its
            # legacy value rather than being blanked.
            if r.get("face_score") is not None:
                conn.execute(
                    """UPDATE images SET sharpness_score = ?, overall_score = ?,
                                         face_sharpness_score = ?,
                                         edge_width_px = ?, face_edge_width_px = ?
                       WHERE id = ?""",
                    (r["score"], round(overall, 1), r["face_score"],
                     r.get("edge_width_px"), r.get("face_edge_width_px"), r["id"]),
                )
            else:
                conn.execute(
                    """UPDATE images SET sharpness_score = ?, overall_score = ?,
                                         edge_width_px = ?, face_edge_width_px = ?
                       WHERE id = ?""",
                    (r["score"], round(overall, 1),
                     r.get("edge_width_px"), r.get("face_edge_width_px"), r["id"]),
                )
    print(f"updated {len(ok)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
