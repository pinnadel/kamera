"""
Burst Detection Module
Groups photos taken within a short time window and selects the best shot.

Logic:
1. Sort photos by timestamp (from EXIF)
2. Group photos less than 2 seconds apart → one burst group
3. Within each group: combine sharpness + exposure + shake scores
4. Mark the highest-scoring photo as "hero", others as "burst_alternative"

This module expects that sharpness, exposure, and shake have already
been analyzed — it works with their scores, not raw pixels.
"""

import exifread
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field


# Maximum time gap (seconds) between shots to be considered the same burst
BURST_GAP_SECONDS = 2.0

# Scoring weights — how much each signal contributes to "best shot" selection
WEIGHTS = {
    "sharpness": 0.5,    # Most important: is it sharp?
    "exposure":  0.3,    # Second: is it well exposed?
    "shake":     0.2,    # Third: is it steady?
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "WEIGHTS must sum to 1.0"


@dataclass
class PhotoScores:
    """
    Scores for a single photo — input to burst detection.
    All scores are 0-100 (higher = better).
    """
    file_path: str
    sharpness_score: float = 0.0
    exposure_score: float = 0.0
    shake_score: float = 100.0   # Default 100 = no shake detected


@dataclass
class BurstGroup:
    """
    A group of photos taken in rapid succession.
    Contains the hero (best) and alternatives.
    """
    group_id: int
    photos: List[PhotoScores]
    hero_path: str                    # Path to the best photo
    timestamps: List[datetime]
    combined_scores: Dict[str, float] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.photos)

    @property
    def duration_seconds(self) -> float:
        if len(self.timestamps) < 2:
            return 0.0
        delta = self.timestamps[-1] - self.timestamps[0]
        return delta.total_seconds()


def _extract_timestamp(image_path: str) -> Optional[datetime]:
    """
    Read the capture timestamp from EXIF.
    Returns None if no timestamp found.
    """
    with open(image_path, "rb") as f:
        tags = exifread.process_file(f)

    tag = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    if not tag:
        return None

    try:
        # EXIF format: "2026:04:14 17:32:01"
        return datetime.strptime(str(tag), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _combined_score(photo: PhotoScores) -> float:
    """
    Calculate a single weighted score from all three signals.
    Higher = better photo.
    """
    return (
        photo.sharpness_score * WEIGHTS["sharpness"] +
        photo.exposure_score  * WEIGHTS["exposure"]  +
        photo.shake_score     * WEIGHTS["shake"]
    )


def detect_bursts(photos: List[PhotoScores], gap_seconds: float = BURST_GAP_SECONDS) -> Dict:
    """
    Main function: group photos into bursts and select best shot per group.

    Args:
        photos: List of PhotoScores objects (with file paths + scores)
        gap_seconds: Maximum time gap between shots to be considered one burst.
                     Default is BURST_GAP_SECONDS (2.0s). Use a smaller value
                     for high-speed sports bursts, larger for casual sequences.

    Returns:
        Dictionary with:
        - groups: List of BurstGroup objects
        - singles: List of PhotoScores (photos not part of any burst)
        - summary: Stats about the session
    """

    # Step 1: Extract timestamps for all photos
    timed_photos = []
    for photo in photos:
        ts = _extract_timestamp(photo.file_path)
        timed_photos.append((ts, photo))

    # Step 2: Sort by timestamp (photos without timestamp go to end)
    timed_photos.sort(key=lambda x: (x[0] is None, x[0]))

    # Step 3: Group by time proximity
    groups = []
    singles = []
    current_group_photos = []
    current_group_times = []
    last_timestamp = None

    for ts, photo in timed_photos:

        if ts is None:
            # No timestamp → treat as single shot
            singles.append(photo)
            continue

        if last_timestamp is None:
            # First photo — start a new group
            current_group_photos = [photo]
            current_group_times = [ts]

        else:
            gap = (ts - last_timestamp).total_seconds()

            if gap <= gap_seconds:
                # Close enough → same burst
                current_group_photos.append(photo)
                current_group_times.append(ts)

            else:
                # Gap too large → close current group, start new one
                _finalize_group(
                    current_group_photos,
                    current_group_times,
                    groups,
                    singles,
                    group_id=len(groups) + 1
                )
                current_group_photos = [photo]
                current_group_times = [ts]

        last_timestamp = ts

    # Don't forget the last group
    if current_group_photos:
        _finalize_group(
            current_group_photos,
            current_group_times,
            groups,
            singles,
            group_id=len(groups) + 1
        )

    # Step 4: Build summary
    total_photos = len(photos)
    burst_photos = sum(g.size for g in groups)
    heroes = len(groups)
    redundant = burst_photos - heroes

    summary = {
        "total_photos": total_photos,
        "burst_groups": len(groups),
        "burst_photos": burst_photos,
        "single_photos": len(singles),
        "heroes_selected": heroes,
        "redundant_shots": redundant,
        "reduction_pct": round((redundant / total_photos * 100) if total_photos > 0 else 0, 1),
    }

    return {
        "groups": groups,
        "singles": singles,
        "summary": summary,
    }


def _finalize_group(
    photos: List[PhotoScores],
    timestamps: List[datetime],
    groups: List[BurstGroup],
    singles: List[PhotoScores],
    group_id: int,
) -> None:
    """
    Close a group: if only one photo → single shot.
    If multiple → find hero and create BurstGroup.
    """
    if len(photos) == 1:
        singles.append(photos[0])
        return

    # Score each photo
    scores = {p.file_path: _combined_score(p) for p in photos}

    # Hero = highest combined score
    hero_path = max(scores, key=scores.__getitem__)

    group = BurstGroup(
        group_id=group_id,
        photos=photos,
        hero_path=hero_path,
        timestamps=timestamps,
        combined_scores=scores,
    )
    groups.append(group)


def get_decisions(result: Dict) -> List[Dict]:
    """
    Convert burst detection result into a flat list of decisions.
    Each photo gets a role: "hero", "burst_alternative", or "single".

    This is what we'll eventually store in SQLite.
    """
    decisions = []

    for group in result["groups"]:
        for photo in group.photos:
            role = "hero" if photo.file_path == group.hero_path else "burst_alternative"
            decisions.append({
                "file_path": photo.file_path,
                "role": role,
                "group_id": group.group_id,
                "combined_score": round(group.combined_scores[photo.file_path], 1),
            })

    for photo in result["singles"]:
        decisions.append({
            "file_path": photo.file_path,
            "role": "single",
            "group_id": None,
            "combined_score": round(_combined_score(photo), 1),
        })

    return decisions


# ─────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python burst_detection.py <folder>")
        sys.exit(1)

    target = Path(sys.argv[1])
    extensions = {".jpg", ".jpeg", ".png", ".raf", ".nef"}
    image_paths = sorted(f for f in target.iterdir() if f.suffix.lower() in extensions)

    if not image_paths:
        print("No images found.")
        sys.exit(1)

    # For testing: create PhotoScores with dummy scores
    # In the real app, these scores come from sharpness.py / exposure.py / camera_shake.py
    import random
    random.seed(42)

    photos = [
        PhotoScores(
            file_path=str(p),
            sharpness_score=random.uniform(40, 100),
            exposure_score=random.uniform(50, 100),
            shake_score=random.uniform(60, 100),
        )
        for p in image_paths
    ]

    result = detect_bursts(photos)
    decisions = get_decisions(result)

    # Print groups
    print(f"\n{'═' * 55}")
    print(f"  BURST DETECTION RESULTS")
    print(f"{'═' * 55}")

    for group in result["groups"]:
        print(f"\n📸 Burst group {group.group_id}  ({group.size} photos, {group.duration_seconds:.1f}s)")
        for photo in group.photos:
            score = group.combined_scores[photo.file_path]
            is_hero = photo.file_path == group.hero_path
            icon = "⭐" if is_hero else "  "
            name = Path(photo.file_path).name
            print(f"   {icon} {name}  (Score: {score:.1f})")

    if result["singles"]:
        print(f"\n📷 Single shots ({len(result['singles'])})")
        for photo in result["singles"]:
            print(f"      {Path(photo.file_path).name}")

    # Print summary
    s = result["summary"]
    print(f"\n{'─' * 55}")
    print(f"  Total:         {s['total_photos']} photos")
    print(f"  Burst groups:  {s['burst_groups']}")
    print(f"  Heroes:        {s['heroes_selected']}")
    print(f"  Redundant:     {s['redundant_shots']} photos ({s['reduction_pct']}% of session)")
    print(f"{'─' * 55}")
