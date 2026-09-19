"""
Classification of KaMeRa ↔ Photos drift.

classify() decides which rows get their keyword rewritten and which get
DELETED, so every branch is pinned here. It is a pure function precisely so
these tests run on a machine with no Photos library — the dangerous half of
reconcile must not be testable only on the developer's Mac.
"""
from __future__ import annotations

import pytest

from backend.photos_reconcile import (
    DEFAULT_ACTIONS,
    classify,
    has_multiple_markers,
    marker_of,
    validate_actions,
)

KEEP = "KaMeRa:Keep"
MAYBE = "KaMeRa:Maybe"
REJECT = "KaMeRa:Reject"


def row(image_id: int, uuid: str | None, decision: str | None = None) -> dict:
    return {
        "id": image_id,
        "filename": f"IMG_{image_id}.HEIC",
        "photos_uuid": uuid,
        "decision": decision,
    }


# ── the agreeing cases ──────────────────────────────────────────────────────

def test_matching_keyword_is_in_sync() -> None:
    d = classify([row(1, "a", "keep")], {"a": [KEEP]}, {"a"})
    assert d.in_sync == 1
    assert d.total_drift == 0


def test_undecided_and_unmarked_is_in_sync() -> None:
    # The common case by far: analysed, not yet culled. Must not be drift.
    d = classify([row(1, "a", None)], {"a": []}, {"a"})
    assert d.in_sync == 1
    assert d.total_drift == 0


def test_the_users_own_keywords_are_ignored() -> None:
    d = classify([row(1, "a", "keep")], {"a": ["Holiday", KEEP, "Greece"]}, {"a"})
    assert d.in_sync == 1


# ── the drift classes ───────────────────────────────────────────────────────

def test_decided_here_but_unmarked_there_is_keyword_missing() -> None:
    """The 2026-09-18 case: keywords deleted by hand in Photos."""
    d = classify([row(1, "a", "keep")], {"a": []}, {"a"})
    assert [e["image_id"] for e in d.classes["keyword_missing"]] == [1]
    assert d.classes["keyword_missing"][0]["keyword"] is None


def test_disagreeing_keyword_is_foreign() -> None:
    d = classify([row(1, "a", "keep")], {"a": [REJECT]}, {"a"})
    entry = d.classes["keyword_foreign"][0]
    assert entry["decision"] == "keep"
    assert entry["keyword"] == REJECT


def test_marked_there_but_undecided_here_is_decision_missing() -> None:
    d = classify([row(1, "a", None)], {"a": [MAYBE]}, {"a"})
    assert d.classes["decision_missing"][0]["keyword"] == MAYBE


def test_asset_left_the_album() -> None:
    d = classify([row(1, "a", "keep")], {}, {"a"})
    assert [e["image_id"] for e in d.classes["not_in_album"]] == [1]
    assert not d.classes["asset_gone"], "still in the library — not gone"


def test_asset_deleted_from_the_library() -> None:
    d = classify([row(1, "a", "keep")], {}, set())
    assert [e["image_id"] for e in d.classes["asset_gone"]] == [1]
    assert not d.classes["not_in_album"]


def test_gone_wins_over_not_in_album() -> None:
    """Both predicates are true for a deleted asset; the stronger fact wins.

    Reporting it as not_in_album would offer 'drop the row' under a heading
    that implies the photo is still safe in the library.
    """
    d = classify([row(1, "a", "keep")], {}, set())
    assert d.classes["asset_gone"] and not d.classes["not_in_album"]


def test_row_without_a_uuid_is_gone_not_in_sync() -> None:
    d = classify([row(1, None, "keep")], {}, set())
    assert d.classes["asset_gone"][0]["image_id"] == 1
    assert d.in_sync == 0


# ── ambiguity is surfaced, not resolved ─────────────────────────────────────

def test_two_markers_never_count_as_in_sync() -> None:
    """A hand-edited asset can carry Keep AND Reject. It cannot agree."""
    d = classify([row(1, "a", "keep")], {"a": [KEEP, REJECT]}, {"a"})
    assert d.in_sync == 0
    entry = d.classes["keyword_foreign"][0]
    assert entry["ambiguous"] is True


def test_marker_of_is_deterministic_under_ambiguity() -> None:
    assert marker_of([REJECT, KEEP]) == marker_of([KEEP, REJECT])
    assert has_multiple_markers([KEEP, REJECT]) is True
    assert has_multiple_markers([KEEP]) is False


def test_marker_of_returns_none_without_a_kamera_keyword() -> None:
    assert marker_of(["Holiday", "Greece"]) is None


# ── assets outside the managed set ──────────────────────────────────────────

def test_marked_album_asset_we_never_analysed_is_only_counted() -> None:
    """No images row means nothing to reconcile — report it, don't invent one."""
    d = classify([row(1, "a", "keep")], {"a": [KEEP], "b": [REJECT]}, {"a", "b"})
    assert d.unmanaged_keyword == 1
    assert d.total_drift == 0


def test_unmarked_album_asset_we_never_analysed_is_not_counted() -> None:
    d = classify([row(1, "a", "keep")], {"a": [KEEP], "b": []}, {"a", "b"})
    assert d.unmanaged_keyword == 0


# ── not_analyzed: photos that joined the album ──────────────────────────────
#
# A Smart Album is a stored query, so it gains members on its own — this is
# the only drift class that appears without the user touching KaMeRa at all.


def meta(filename: str = "IMG_NEW.HEIC", *, movie: bool = False,
         original: bool = True, supported: bool = True) -> dict:
    return {"filename": filename, "is_movie": movie,
            "has_original": original, "supported": supported}


def test_a_new_album_photo_is_not_analyzed() -> None:
    d = classify(
        [row(1, "a", "keep")], {"a": [KEEP], "b": []}, {"a", "b"},
        {"a": meta("IMG_1.HEIC"), "b": meta("IMG_3951.HEIC")},
    )
    assert [e["filename"] for e in d.classes["not_analyzed"]] == ["IMG_3951.HEIC"]
    assert d.classes["not_analyzed"][0]["image_id"] is None
    assert d.classes["not_analyzed"][0]["has_original"] is True


def test_videos_are_not_drift() -> None:
    """An album of 2116 holds 243 movies. Reporting them as actionable would
    bury the two photos that actually need analysing."""
    d = classify(
        [row(1, "a", "keep")], {"a": [KEEP], "b": []}, {"a", "b"},
        {"a": meta(), "b": meta("IMG_4000.MOV", movie=True)},
    )
    assert d.classes["not_analyzed"] == []


def test_an_undecodable_format_is_not_drift() -> None:
    d = classify(
        [row(1, "a", "keep")], {"a": [KEEP], "b": []}, {"a", "b"},
        {"a": meta(), "b": meta("scan.psd", supported=False)},
    )
    assert d.classes["not_analyzed"] == []


def test_a_photo_whose_original_is_only_in_icloud_is_still_reported() -> None:
    """It is exactly the case the user hits: new photos land in the album
    before their originals download. Silently omitting them would look like
    KaMeRa had already dealt with them."""
    d = classify(
        [row(1, "a", "keep")], {"a": [KEEP], "b": []}, {"a", "b"},
        {"a": meta(), "b": meta("IMG_3951.HEIC", original=False)},
    )
    assert len(d.classes["not_analyzed"]) == 1
    assert d.classes["not_analyzed"][0]["has_original"] is False


def test_a_marked_asset_stays_a_bare_count() -> None:
    """Marked-but-rowless means "analysed elsewhere". Promoting it to
    not_analyzed would re-analyse something already judged."""
    d = classify(
        [row(1, "a", "keep")], {"a": [KEEP], "b": [REJECT]}, {"a", "b"},
        {"a": meta(), "b": meta("IMG_9.HEIC")},
    )
    assert d.unmanaged_keyword == 1
    assert d.classes["not_analyzed"] == []


def test_an_asset_we_already_have_a_row_for_is_never_not_analyzed() -> None:
    d = classify(
        [row(1, "a", "keep")], {"a": [KEEP]}, {"a"}, {"a": meta()},
    )
    assert d.classes["not_analyzed"] == []


def test_without_member_metadata_the_class_stays_empty() -> None:
    """classify() is still callable with three arguments; a caller that
    cannot supply metadata gets the pre-existing behaviour rather than a
    crash or a silently wrong count."""
    d = classify([row(1, "a", "keep")], {"a": [KEEP], "b": []}, {"a", "b"})
    assert d.classes["not_analyzed"] == []


def test_analyze_is_a_valid_action_and_skip_is_the_default() -> None:
    assert DEFAULT_ACTIONS["not_analyzed"] == "skip"
    assert validate_actions({"not_analyzed": "analyze"})["not_analyzed"] == "analyze"
    with pytest.raises(ValueError):
        validate_actions({"not_analyzed": "drop"})


# ── counting ────────────────────────────────────────────────────────────────

def test_checked_counts_rows_not_album_assets() -> None:
    d = classify(
        [row(1, "a", "keep"), row(2, "b", "reject")],
        {"a": [KEEP], "b": [REJECT], "c": []},
        {"a", "b", "c"},
    )
    assert d.checked == 2
    assert d.in_sync == 2


def test_as_dict_carries_every_class_even_when_empty() -> None:
    payload = classify([], {}, set()).as_dict()
    for name in ("keyword_missing", "keyword_foreign", "decision_missing",
                 "not_in_album", "asset_gone"):
        assert name in payload["classes"]


# ── action validation ───────────────────────────────────────────────────────

def test_only_keyword_missing_defaults_to_acting() -> None:
    """The plan's rule: auto-fix only where the two sides do not disagree."""
    assert DEFAULT_ACTIONS["keyword_missing"] == "apply"
    for name in ("keyword_foreign", "decision_missing", "not_in_album", "asset_gone"):
        assert DEFAULT_ACTIONS[name] == "skip"


def test_missing_classes_fall_back_to_defaults() -> None:
    assert validate_actions({"asset_gone": "drop"}) == {
        **DEFAULT_ACTIONS, "asset_gone": "drop"
    }


def test_an_unknown_class_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown mismatch class"):
        validate_actions({"keyword_missng": "apply"})


def test_an_action_from_the_wrong_class_is_rejected() -> None:
    # 'drop' is valid for asset_gone but must not delete a keyword_missing row.
    with pytest.raises(ValueError, match="not valid"):
        validate_actions({"keyword_missing": "drop"})


def test_validate_does_not_mutate_the_defaults() -> None:
    validate_actions({"asset_gone": "drop"})
    assert DEFAULT_ACTIONS["asset_gone"] == "skip"
