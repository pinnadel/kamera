"""Group-scoring helpers shared by similarity-group hero selection and the
burst-rank top-N pre-filter. The priority order is the canonical
"why is this photo the best of its group" sequence used everywhere in the
app: face_sharpness (when a face is detected) → eyes_open → frame
sharpness → IQA → aesthetic → overall_score.

`compute_best_reason` returns the short copy shown next to the warm-amber
ring; `score_candidate` exposes the same priority as a sort key so the
burst-rank pre-filter can pick the top-N candidates without re-deriving
the formula.
"""

from typing import Any


def compute_best_reason(best: dict, group: list[dict], user_override: bool) -> str:
    """Return a one-line explanation for why `best` was chosen as the group hero."""
    if user_override:
        return "Your pick"

    others = [img for img in group if img["id"] != best["id"]]
    if not others:
        return "Only photo in group"

    # Face-based reasons (highest priority — face quality is the #1 culling signal)
    if best.get("face_detected"):
        best_fs = best.get("face_sharpness_score") or 0
        other_fs_vals = [img.get("face_sharpness_score") or 0 for img in others if img.get("face_detected")]
        if best_fs > 0 and (not other_fs_vals or best_fs > max(other_fs_vals)):
            avg = int(sum(other_fs_vals) / len(other_fs_vals)) if other_fs_vals else 0
            return f"Sharpest face ({int(best_fs)} vs avg {avg})"

        best_eyes = best.get("eyes_open")
        if best_eyes == 1 and any(img.get("eyes_open") == 0 for img in others):
            return "Eyes open — others blinked"

    # Frame sharpness
    best_sh = best.get("sharpness_score") or 0
    other_sh_vals = [img.get("sharpness_score") or 0 for img in others]
    if best_sh > 0 and best_sh > max(other_sh_vals, default=0):
        avg = int(sum(other_sh_vals) / len(other_sh_vals)) if other_sh_vals else 0
        return f"Sharpest frame ({int(best_sh)} vs avg {avg})"

    # Perceptual IQA
    best_iqa = best.get("iqa_score")
    if best_iqa is not None:
        other_iqa = [img["iqa_score"] for img in others if img.get("iqa_score") is not None]
        if other_iqa and best_iqa > max(other_iqa):
            return "Best perceptual quality (TOPIQ)"

    # Aesthetic
    best_aes = best.get("aesthetic_score")
    if best_aes is not None:
        other_aes = [img["aesthetic_score"] for img in others if img.get("aesthetic_score") is not None]
        if other_aes and best_aes > max(other_aes):
            return "Best aesthetic score"

    return "Highest overall score"


def _f(val: Any) -> float:
    """Coerce SQLite NULL / missing keys to 0.0 for sort-key arithmetic."""
    return float(val) if val is not None else 0.0


def score_candidate(img: dict) -> tuple:
    """Sort key mirroring `compute_best_reason` priority.

    Use with `sorted(images, key=score_candidate, reverse=True)` — higher
    tuples win. Missing or NULL scores coerce to 0 so a partially-analysed
    photo sorts to the bottom rather than crashing the call site.

    Tuple slots (descending priority):
      0: face_sharpness_score IF face_detected else 0  — face quality dominates
      1: eyes_open (0 or 1)                            — blink-free wins
      2: sharpness_score                                — frame sharpness
      3: iqa_score                                      — perceptual quality
      4: aesthetic_score                                — composition / aesthetic
      5: overall_score                                  — final tiebreak
    """
    face_sh = _f(img.get("face_sharpness_score")) if img.get("face_detected") else 0.0
    return (
        face_sh,
        int(img.get("eyes_open") or 0),
        _f(img.get("sharpness_score")),
        _f(img.get("iqa_score")),
        _f(img.get("aesthetic_score")),
        _f(img.get("overall_score")),
    )


def top_n_candidates(images: list[dict], n: int) -> list[dict]:
    """Return the top-n photos by `score_candidate`, descending. Stable on
    ties (preserves input order)."""
    if n >= len(images):
        return list(images)
    return sorted(images, key=score_candidate, reverse=True)[:n]
