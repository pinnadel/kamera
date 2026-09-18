"""Tests for how the auto-cull burst grouping is parameterised.

History:
- 2026-07-26 (bug): both auto-cull endpoints called _group_undecided_into_bursts
  with NO arguments, so grouping was hardwired to 60s / 0.90 and the GroupLoupe
  clustering slider had zero effect on auto-cull.
- 2026-07-26 (redesign): auto-cull dedup was DECOUPLED from the grouping
  time-gap entirely. The time-gap could only split bursts coarser than the
  camera's inter-frame spacing, and continuous bursts share an EXIF second, so
  no gap could separate them — the slider was inert for auto-cull on fast
  bursts. Auto-cull now groups by VISUAL SIMILARITY only: _group_undecided_into
  _bursts defaults time_gap_seconds=None, and the endpoints expose `threshold`
  (Similarity) but NOT time_gap_seconds. The per-scene split in _cap_burst_keeps
  guards against distinct moments collapsing into one giant burst.

These tests pin (a) that the grouping helper STILL honours an explicit time gap
when a caller passes one (the mechanism is intact), (b) that with the default it
groups by similarity only, and (c) that the auto-cull endpoints expose
`threshold` and no longer take `time_gap_seconds`.
"""
from __future__ import annotations

import json
import inspect

import numpy as np

from backend.routers.decisions import (
    _group_undecided_into_bursts,
    auto_cull_preview,
    run_auto_cull,
)


def _emb_json(vec) -> str:
    v = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(v)
    if n > 0:
        v = v / n
    return json.dumps(v.tolist())


def _row(id: int, shot_at: str) -> dict:
    # Identical embedding for every row → similarity always unites them; only
    # an explicit TIME GAP decides whether they split.
    return {
        "id": id,
        "embedding": _emb_json([1.0, 0.0, 0.0, 0.0]),
        "shot_at": shot_at,
        "manual_group_id": None,
    }


def _multi_member_groups(groups: list[list[dict]]) -> list[list[dict]]:
    return [g for g in groups if len(g) >= 2]


def test_default_grouping_is_similarity_only_ignores_time():
    """With the default (time_gap_seconds=None), visually-identical frames group
    together no matter how far apart in time — auto-cull's decoupled behaviour."""
    rows = [
        _row(1, "2026-07-26 12:00:00"),
        _row(2, "2026-07-26 13:30:00"),  # +90 min — would split under any time gap
    ]
    groups = _multi_member_groups(_group_undecided_into_bursts(rows))
    assert len(groups) == 1
    assert {m["id"] for m in groups[0]} == {1, 2}


def test_explicit_time_gap_still_splits_when_a_caller_passes_one():
    """The mechanism is intact: a caller (e.g. a test, or a future feature) that
    explicitly passes time_gap_seconds still gets a time split."""
    rows = [
        _row(1, "2026-07-26 12:00:00"),
        _row(2, "2026-07-26 12:01:00"),  # +60s
    ]
    tight = _multi_member_groups(_group_undecided_into_bursts(rows, time_gap_seconds=1))
    loose = _multi_member_groups(_group_undecided_into_bursts(rows, time_gap_seconds=300))
    assert tight == []          # 1s gap: 60s apart → split
    assert len(loose) == 1      # 300s gap: within window → one burst


def test_auto_cull_endpoints_expose_threshold_not_time_gap():
    """Auto-cull dedup groups by similarity only. Both endpoints must expose
    `threshold` (the Similarity knob) and must NOT take time_gap_seconds — the
    grouping time-gap was decoupled from auto-cull. Guards the contract cheaply."""
    for fn in (auto_cull_preview, run_auto_cull):
        params = inspect.signature(fn).parameters
        assert "threshold" in params, f"{fn.__name__} lost threshold"
        assert "time_gap_seconds" not in params, (
            f"{fn.__name__} re-coupled to the grouping time-gap — auto-cull dedup "
            f"should group by similarity only"
        )
