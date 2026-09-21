"""
Recompute FaceNet identity embeddings that were cropped from the wrong region.

Two bugs, both fixed 2026-09-20, both silent:

  * `analyze_faces_array` returned its face bbox in the detector's downscaled
    frame (<= 2000 px) while `_embed_face` cropped the full-resolution array —
    so every RAW file larger than that embedded the wrong region.
  * the JPEG branch decoded without `exif_transpose` while the bbox came from
    `analyze_faces()`, which decodes upright — so on orientation 2-8 files the
    crop was rotated away from the face.

Both feed `/face-groups` identity clustering, which is why people may have been
grouped by whatever sat in the top-left quadrant rather than by their face.

A control group of unaffected rows is recomputed too, but never written: its
cosine similarity against the stored vector must come back ~1.0. If it does not,
the recomputation itself is off and the affected numbers mean nothing.

    venv/bin/python scripts/backfill_face_embeddings.py          # measure, write nothing
    venv/bin/python scripts/backfill_face_embeddings.py --apply  # back up the DB, then write
"""
import argparse
import os
import sqlite3
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "pca.db"
CONTROL_SAMPLE = 30


def _classify(path: str) -> str:
    """Which of the two bugs (if either) touched this file's embedding."""
    from backend.constants import RAW_DECODE_SUFFIXES

    if os.path.splitext(path)[1].lower() in RAW_DECODE_SUFFIXES:
        return "raw_bbox_scale"
    try:
        import pillow_heif
        from PIL import Image

        pillow_heif.register_heif_opener()
        with Image.open(path) as im:
            orientation = im.getexif().get(0x0112, 1)
    except Exception:
        return "unreadable"
    return "exif_rotation" if orientation not in (1, None) else "unaffected"


def _recompute(row):
    """(id, path) → new embedding. Mirrors the pipeline's own branch exactly."""
    import numpy as np
    import pillow_heif
    from PIL import Image, ImageOps

    from backend.constants import RAW_DECODE_SUFFIXES
    from phase1_technical.quality_analyzer import decode_raw
    from phase2_quality.face_analyzer import analyze_faces, analyze_faces_array
    from phase2_quality.face_identity import embed_face

    pillow_heif.register_heif_opener()
    image_id, path = row
    try:
        if Path(path).suffix.lower() in RAW_DECODE_SUFFIXES:
            rgb, _ = decode_raw(path)
            face = analyze_faces_array(rgb)
        else:
            rgb = np.asarray(ImageOps.exif_transpose(Image.open(path).convert("RGB")))
            face = analyze_faces(path)
        if not (face.get("face_detected") and face.get("face_bbox")):
            return {"id": image_id, "skipped": "no face on re-detection"}
        emb = embed_face(rgb, face["face_bbox"])
        if emb is None:
            return {"id": image_id, "skipped": "embedder returned None"}
        return {"id": image_id, "embedding": emb}
    except Exception as exc:
        return {"id": image_id, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write to the DB (default: measure only)")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    import numpy as np

    from phase2_quality.face_identity import (
        face_embedding_to_json,
        json_to_face_embedding,
    )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, file_path, face_embedding FROM images "
        "WHERE face_detected = 1 AND face_embedding IS NOT NULL ORDER BY id"
    ).fetchall()
    print(f"{len(rows)} rows carry a face embedding; classifying…", flush=True)

    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(_classify(r["file_path"]), []).append(r)
    for name, items in sorted(buckets.items()):
        print(f"  {name}: {len(items)}")

    affected = buckets.get("raw_bbox_scale", []) + buckets.get("exif_rotation", [])
    control = buckets.get("unaffected", [])[:CONTROL_SAMPLE]
    if not affected:
        print("\nNothing to fix.")
        return 0

    todo = affected + control
    print(f"\nrecomputing {len(affected)} affected + {len(control)} control rows "
          f"with {args.workers} workers…", flush=True)
    with Pool(args.workers) as pool:
        results = pool.map(_recompute, [(r["id"], r["file_path"]) for r in todo], chunksize=2)

    stored = {r["id"]: json_to_face_embedding(r["face_embedding"]) for r in todo}
    by_id = {r["id"]: r for r in results}

    def _cosines(group):
        out = []
        for r in group:
            new = by_id.get(r["id"], {}).get("embedding")
            old = stored.get(r["id"])
            if new and old:
                a, b = np.array(old, float), np.array(new, float)
                out.append(float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b))))
        return np.array(out)

    control_cos = _cosines(control)
    affected_cos = _cosines(affected)

    print("\ncosine(stored, recomputed) — 1.0 means the vector did not move")
    if control_cos.size:
        print(f"  control  (n={control_cos.size}): median {np.median(control_cos):.3f}  "
              f"min {control_cos.min():.3f}")
        if np.median(control_cos) < 0.99:
            print("  ⚠️  control should be ~1.0 — the recomputation differs from the pipeline.")
            print("      Refusing to draw conclusions (or to write) from this run.")
            return 1
    if affected_cos.size:
        print(f"  affected (n={affected_cos.size}): median {np.median(affected_cos):.3f}  "
              f"max {affected_cos.max():.3f}")
        print(f"  affected rows whose identity vector genuinely moved (cos < 0.9): "
              f"{int((affected_cos < 0.9).sum())}")

    errors = [r for r in results if "error" in r]
    skipped = [r for r in results if "skipped" in r]
    for r in errors[:5]:
        print("  error", r)
    for r in skipped[:5]:
        print("  skipped", r)
    print(f"errors: {len(errors)} · skipped: {len(skipped)}")

    if not args.apply:
        print("\nMeasure-only run — nothing written. Re-run with --apply.")
        return 0

    backup = DB_PATH.with_name("pca.db.pre-face-embedding-backup")
    if backup.exists():
        backup.unlink()
    dst = sqlite3.connect(backup)
    conn.backup(dst)  # WAL-safe, unlike copying the file under a live backend
    dst.close()
    print(f"backup → {backup}")

    written = 0
    with conn:
        # Control rows are measurement only — they were never wrong.
        for r in affected:
            emb = by_id.get(r["id"], {}).get("embedding")
            if emb is None:
                continue
            conn.execute("UPDATE images SET face_embedding = ? WHERE id = ?",
                         (face_embedding_to_json(emb), r["id"]))
            written += 1
    print(f"updated {written} rows")
    print("Re-open People / face groups to see the regrouping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
