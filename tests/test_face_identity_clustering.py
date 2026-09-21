"""People-mode clustering must not chain two people into one group.

The bug this file exists for (measured 2026-09-21): `/face-groups` reused the
burst grouper, whose single-linkage union merges two clusters as soon as ANY
pair across them clears the cutoff. On a 977-face library that produced one
963-face group holding every adult and every child. Average linkage, same
cutoff and same embeddings, dropped the largest group to 271 and separated all
143 hand-labelled adult/child pairs.

The fixtures below are synthetic identities, so "same person" is known exactly
rather than judged.
"""
import numpy as np
import pytest

from phase2_quality import face_identity
from phase2_quality.face_identity import group_faces_by_identity


def _person(seed: int, n: int, tightness: float = 0.8, dims: int = 512):
    """n unit vectors around one identity direction, each ≈`tightness` cosine
    from it. Noise has to be scaled by 1/sqrt(dims): at 512 dimensions a
    per-component sigma of 0.25 already has norm 5.7 and drowns the identity.
    """
    rng = np.random.default_rng(seed)
    centre = rng.normal(size=dims)
    centre /= np.linalg.norm(centre)
    sigma = np.sqrt(1.0 / tightness**2 - 1.0) / np.sqrt(dims)
    out = []
    for _ in range(n):
        v = centre + rng.normal(scale=sigma, size=dims)
        out.append((v / np.linalg.norm(v)).tolist())
    return out


def test_the_fixture_itself_is_what_it_claims():
    """Guard the instrument: same-person pairs must sit well above the cutoff
    and different-person pairs well below, or every assertion below is noise."""
    a, b = _person(1, 12), _person(2, 12)
    A, B = np.array(a), np.array(b)
    same = (A @ A.T)[np.triu_indices(len(A), 1)]
    cross = (A @ B.T).ravel()
    assert same.min() > 0.5, same.min()
    assert cross.max() < 0.5, cross.max()


def _items(*groups):
    items, next_id = [], 0
    for vecs in groups:
        for v in vecs:
            items.append((next_id, v))
            next_id += 1
    return items


def _group_of(groups, image_id):
    for i, g in enumerate(groups):
        if image_id in g:
            return i
    return None


def test_two_distinct_people_stay_apart():
    items = _items(_person(1, 8), _person(2, 8))
    groups = group_faces_by_identity(items, threshold=0.5)
    a_ids, b_ids = range(0, 8), range(8, 16)
    for a in a_ids:
        for b in b_ids:
            ga, gb = _group_of(groups, a), _group_of(groups, b)
            assert ga is None or gb is None or ga != gb, (a, b)


def test_one_person_stays_together():
    items = _items(_person(3, 10))
    groups = group_faces_by_identity(items, threshold=0.5)
    assert len(groups) == 1
    assert len(groups[0]) == 10


def test_a_bridging_face_does_not_merge_two_people():
    """The chaining failure itself: one ambiguous face between two identities.

    Single linkage merges everything through it; average linkage must not.
    """
    rng = np.random.default_rng(9)
    a = _person(4, 10, tightness=0.85)
    b = _person(5, 10, tightness=0.85)
    # A face sitting exactly between the two identity directions.
    bridge = (np.array(a[0]) + np.array(b[0]))
    bridge = (bridge / np.linalg.norm(bridge)).tolist()
    items = _items(a, b, [bridge])
    groups = group_faces_by_identity(items, threshold=0.5)
    a_group = _group_of(groups, 0)
    b_group = _group_of(groups, 10)
    assert a_group is not None and b_group is not None
    assert a_group != b_group, "the bridging face merged two identities"


def test_groups_come_back_largest_first_and_drop_singletons():
    items = _items(_person(6, 9), _person(7, 3), _person(8, 1))
    groups = group_faces_by_identity(items, threshold=0.5)
    sizes = [len(g) for g in groups]
    assert sizes == sorted(sizes, reverse=True)
    assert 1 not in sizes


def test_too_few_items_returns_empty():
    assert group_faces_by_identity([], threshold=0.5) == []
    assert group_faces_by_identity([(1, _person(1, 1)[0])], threshold=0.5) == []


def test_a_higher_threshold_never_produces_bigger_groups():
    items = _items(_person(11, 12), _person(12, 12))
    loose = group_faces_by_identity(items, threshold=0.4)
    tight = group_faces_by_identity(items, threshold=0.8)
    assert max(len(g) for g in loose) >= max((len(g) for g in tight), default=0)


def test_oversized_input_falls_back_instead_of_building_the_matrix(monkeypatch, caplog):
    """Honest degradation: the old grouper, logged — not a dead request."""
    monkeypatch.setattr(face_identity, "_MAX_MATRIX_FACES", 4)
    called = {}

    def _fake(items, threshold, time_gap_seconds):
        called["n"] = len(items)
        called["time_gap_seconds"] = time_gap_seconds
        return [[it[0] for it in items]]

    import phase2_quality.similarity_scorer as sim
    monkeypatch.setattr(sim, "group_by_similarity", _fake)
    items = _items(_person(13, 6))
    with caplog.at_level("WARNING"):
        groups = group_faces_by_identity(items, threshold=0.5)
    assert called["n"] == 6
    assert called["time_gap_seconds"] is None
    assert groups == [[it[0] for it in items]]
    assert "falling back" in caplog.text


def test_endpoint_uses_the_identity_clusterer_not_the_burst_grouper():
    """Call-site guard — the whole fix is which function /face-groups calls."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "backend/routers/model.py").read_text()
    body = src.split("def face_groups(")[1].split("\n@router")[0]
    assert "_group_faces_by_identity(" in body
    assert "_group_by_similarity(" not in body
