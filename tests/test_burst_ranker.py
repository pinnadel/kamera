"""Tests for phase2_quality/burst_ranker.py — the LLM-backed burst ranker.

These tests cover:
  - the new server-side pre-filter (bursts >12 trimmed to top-12 by the
    shared `top_n_candidates` priority)
  - the new `evaluated_ids` / `filtered_from` response fields
  - the cache key is computed on the FILTERED set (not the original input)
  - graceful degradation when no vision model is installed
  - bypass of the LLM call when a cache row already exists

Every test stubs `_pick_vision_model` and the OpenAI client — no live Ollama
call is ever made.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable

import pytest

from backend.database import get_db
from phase2_quality import burst_ranker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_image(conn, *, id: int, preview_path: str | None = "/nonexistent.jpg",
                  format: str | None = None,
                  file_path: str | None = None,
                  face_detected: int = 0, face_sharpness_score: float | None = None,
                  eyes_open: int | None = None, sharpness_score: float | None = None,
                  iqa_score: float | None = None, aesthetic_score: float | None = None,
                  overall_score: float | None = None) -> None:
    """Insert a minimal `images` row with the columns the pre-filter and the
    rank_burst preview-loader read. Other columns stay NULL/default."""
    if file_path is None:
        file_path = f"/photos/IMG{id:04d}.jpg"
    conn.execute(
        """
        INSERT INTO images (id, filename, file_path, preview_path, format,
                            face_detected, face_sharpness_score, eyes_open,
                            sharpness_score, iqa_score, aesthetic_score,
                            overall_score, analysis_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done')
        """,
        (id, f"IMG{id:04d}.jpg", file_path, preview_path, format,
         face_detected, face_sharpness_score, eyes_open,
         sharpness_score, iqa_score, aesthetic_score, overall_score),
    )


class _FakeHttpxResponse:
    """Minimal stand-in for httpx.Response that rank_burst's call site
    reads — status_code, .json(), .text."""

    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeHttpxPost:
    """Replaces `httpx.post` inside burst_ranker so tests can return canned
    /api/chat responses without hitting Ollama. Records the last payload it
    received so a test can assert no call was made (cache-hit case)."""

    last_payload = None
    canned_content = "[]"
    status_code = 200

    @classmethod
    def reset(cls):
        cls.last_payload = None
        cls.canned_content = "[]"
        cls.status_code = 200

    @classmethod
    def post(cls, url, *, json=None, timeout=None):  # noqa: A002 — match httpx signature
        cls.last_payload = json
        return _FakeHttpxResponse(
            {"message": {"role": "assistant", "content": cls.canned_content}},
            status_code=cls.status_code,
        )


@pytest.fixture()
def fake_openai(monkeypatch):
    """Despite the name, this fixture now patches httpx.post — the burst
    ranker switched from OpenAI's shim to Ollama's native /api/chat so
    num_ctx actually lands. Tests call it `fake_openai` for backward
    compatibility with the historical API; the surface (canned_content,
    last_payload) is what changed."""
    import httpx
    _FakeHttpxPost.reset()
    monkeypatch.setattr(httpx, "post", _FakeHttpxPost.post)
    return _FakeHttpxPost


@pytest.fixture()
def fake_picker(monkeypatch):
    """Make the vision-model picker return a deterministic name without
    touching the local Ollama installation."""
    monkeypatch.setattr(burst_ranker, "_pick_vision_model", lambda: "qwen2.5vl:7b")


@pytest.fixture()
def fake_no_picker(monkeypatch):
    """Picker returns None — simulates 'no vision model installed'."""
    monkeypatch.setattr(burst_ranker, "_pick_vision_model", lambda: None)


@pytest.fixture()
def fake_preview_read(monkeypatch):
    """Skip the real base64 read — we don't have preview files on disk in
    tests. Any non-None return is enough for rank_burst to proceed."""
    monkeypatch.setattr(burst_ranker, "_read_preview_b64", lambda _path: "ZmFrZS1iNjQ=")


def _open_conn(tmp_db) -> sqlite3.Connection:
    """Open the tmp_db file directly — get_db is a context manager which we
    can't easily nest with assertions in the body. Tests open a raw
    connection, seed data, and pass it straight to rank_burst (which doesn't
    care about commits because it does its own)."""
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_min_members_returns_too_few(tmp_db):
    conn = _open_conn(tmp_db)
    _insert_image(conn, id=1)
    conn.commit()
    result = burst_ranker.rank_burst([1], conn)
    assert result["status"] == "too_few"
    assert result["filtered_from"] == 1
    assert result["evaluated_ids"] == []
    conn.close()


def test_basic_rank_passthrough_no_filter(tmp_db, fake_picker, fake_preview_read, fake_openai):
    """3 ids, no pre-filter expected. Stubbed LLM returns a valid ranking."""
    conn = _open_conn(tmp_db)
    for i in (1, 2, 3):
        _insert_image(conn, id=i)
    conn.commit()

    fake_openai.canned_content = json.dumps([
        {"image_index": 2, "rank": 1, "reason": "best smile"},
        {"image_index": 1, "rank": 2, "reason": "eyes blinking"},
        {"image_index": 3, "rank": 3, "reason": "soft focus"},
    ])

    result = burst_ranker.rank_burst([1, 2, 3], conn)
    assert result["status"] == "ranked"
    assert result["model"] == "qwen2.5vl:7b"
    assert result["filtered_from"] == 3
    assert result["evaluated_ids"] == [1, 2, 3]
    assert len(result["rankings"]) == 3
    # rank=1 came from image_index=2 in our canned payload, which maps to id=2
    rank1 = next(r for r in result["rankings"] if r["rank"] == 1)
    assert rank1["image_id"] == 2
    conn.close()


def test_no_vision_model_returns_install_nudge(tmp_db, fake_no_picker):
    conn = _open_conn(tmp_db)
    for i in (1, 2):
        _insert_image(conn, id=i)
    conn.commit()
    result = burst_ranker.rank_burst([1, 2], conn)
    assert result["status"] == "no_vision_model"
    assert result["evaluated_ids"] == [1, 2]
    assert result["filtered_from"] == 2
    conn.close()


def test_pre_filter_trims_large_burst_to_top_n(tmp_db, fake_picker, fake_preview_read, fake_openai):
    """20 photos in, top-N (current _MAX_MEMBERS) by priority go to the LLM,
    original count surfaced. Switched from a hardcoded 12 to _MAX_MEMBERS
    in 2026-05-17 when the cap moved 12→8 alongside the resolution cut."""
    from phase2_quality.burst_ranker import _MAX_MEMBERS

    conn = _open_conn(tmp_db)
    # ids 1..20: overall_score ascending so the top-N by overall_score
    # are the last N ids. With no face_detected / face_sharpness data, the
    # priority falls through to overall_score (slots 0-4 all zero).
    for i in range(1, 21):
        _insert_image(conn, id=i, overall_score=float(i))
    conn.commit()

    # LLM will see _MAX_MEMBERS ids (sorted). Provide a matching canned ranking.
    fake_openai.canned_content = json.dumps([
        {"image_index": j + 1, "rank": j + 1, "reason": f"r{j}"}
        for j in range(_MAX_MEMBERS)
    ])

    result = burst_ranker.rank_burst(list(range(1, 21)), conn)
    assert result["status"] == "ranked"
    assert result["filtered_from"] == 20
    assert len(result["evaluated_ids"]) == _MAX_MEMBERS
    # The N winners are the highest-overall_score ids: (21-N)..20
    assert result["evaluated_ids"] == list(range(21 - _MAX_MEMBERS, 21))
    conn.close()


def test_pre_filter_face_sharpness_beats_overall(tmp_db, fake_picker, fake_preview_read, fake_openai):
    """One photo with high face_sharpness must displace the photo with the
    highest overall_score from the top-N set — face quality dominates the
    priority tuple."""
    from phase2_quality.burst_ranker import _MAX_MEMBERS

    conn = _open_conn(tmp_db)
    # _MAX_MEMBERS + 2 photos. id=99 has high overall but no face. id=100 has
    # a strong face. Top-N must include id=100 and exclude id=99 (or another
    # overall-only photo) — specifically, the lowest-overall non-face photo
    # gets dropped.
    for i in range(1, _MAX_MEMBERS + 1):
        _insert_image(conn, id=i, overall_score=float(i))
    _insert_image(conn, id=99, overall_score=999.0)  # high overall, no face
    _insert_image(conn, id=100, face_detected=1, face_sharpness_score=90.0,
                  overall_score=1.0)  # face wins despite low overall

    conn.commit()

    fake_openai.canned_content = json.dumps([
        {"image_index": j + 1, "rank": j + 1, "reason": f"r{j}"}
        for j in range(_MAX_MEMBERS)
    ])

    result = burst_ranker.rank_burst([100, 99] + list(range(1, _MAX_MEMBERS + 1)), conn)
    assert result["status"] == "ranked"
    assert 100 in result["evaluated_ids"]  # face-sharpness winner must make the cut
    assert len(result["evaluated_ids"]) == _MAX_MEMBERS
    conn.close()


def test_pre_filter_handles_missing_scores(tmp_db, fake_picker, fake_preview_read, fake_openai):
    """Rows with NULL scores must sort to the bottom but not crash the run."""
    from phase2_quality.burst_ranker import _MAX_MEMBERS

    conn = _open_conn(tmp_db)
    # _MAX_MEMBERS + 1 photos: _MAX_MEMBERS with overall_score, 1 with all NULLs
    for i in range(1, _MAX_MEMBERS + 1):
        _insert_image(conn, id=i, overall_score=float(i))
    _insert_image(conn, id=999)  # all NULLs

    conn.commit()
    fake_openai.canned_content = json.dumps([
        {"image_index": j + 1, "rank": j + 1, "reason": "r"} for j in range(_MAX_MEMBERS)
    ])

    result = burst_ranker.rank_burst([999] + list(range(1, _MAX_MEMBERS + 1)), conn)
    assert result["status"] == "ranked"
    assert 999 not in result["evaluated_ids"]
    assert len(result["evaluated_ids"]) == _MAX_MEMBERS
    conn.close()


def test_cache_hit_short_circuits_llm_call(tmp_db, fake_picker, fake_preview_read, fake_openai):
    """A pre-seeded burst_rankings row should be returned without calling
    the LLM at all. The fake OpenAI records last_messages; we assert it
    stayed None."""
    conn = _open_conn(tmp_db)
    for i in (1, 2, 3):
        _insert_image(conn, id=i)

    # Pre-seed cache. Hash is sha1 over comma-joined sorted ids.
    h = hashlib.sha1(b"1,2,3").hexdigest()
    cached_rankings = [
        {"image_id": 1, "rank": 1, "reason": "from cache"},
        {"image_id": 2, "rank": 2, "reason": "from cache"},
        {"image_id": 3, "rank": 3, "reason": "from cache"},
    ]
    conn.execute(
        "INSERT INTO burst_rankings (members_hash, member_ids, rankings_json, model) "
        "VALUES (?, ?, ?, ?)",
        (h, json.dumps([1, 2, 3]), json.dumps(cached_rankings), "qwen2.5vl:7b"),
    )
    conn.commit()

    result = burst_ranker.rank_burst([1, 2, 3], conn)
    assert result["status"] == "ranked"
    assert result["cached"] is True
    assert result["evaluated_ids"] == [1, 2, 3]
    assert result["filtered_from"] == 3
    assert fake_openai.last_payload is None  # LLM never called
    conn.close()


def test_jpeg_falls_back_to_file_path_when_preview_path_null(tmp_db, fake_picker, fake_openai, monkeypatch):
    """Non-RAW formats (JPEG/PNG/HIF) never populate preview_path — the
    server-side /previews route streams them directly without caching.
    Before the fallback fix, burst rank silently bailed with status="error"
    on those photos. This test pins down the fix: when preview_path is NULL
    AND format is JPEG, the ranker reads file_path instead."""
    conn = _open_conn(tmp_db)
    for i in (1, 2, 3):
        _insert_image(conn, id=i, preview_path=None, format='JPEG',
                      file_path=f'/some/dir/IMG{i:04d}.jpeg')
    conn.commit()

    # Stub _read_preview_b64 to succeed on the JPEG file_path values we set.
    # The new helper calls _read_preview_b64(preview_path) first (None → no
    # cached), then _read_preview_b64(file_path) which our stub answers.
    from phase2_quality import burst_ranker
    def _stub_read(p):
        return "ZmFrZS1iNjQ=" if p and p.endswith('.jpeg') else None
    monkeypatch.setattr(burst_ranker, "_read_preview_b64", _stub_read)

    fake_openai.canned_content = json.dumps([
        {"image_index": 1, "rank": 1, "reason": "r1"},
        {"image_index": 2, "rank": 2, "reason": "r2"},
        {"image_index": 3, "rank": 3, "reason": "r3"},
    ])

    result = burst_ranker.rank_burst([1, 2, 3], conn)
    assert result["status"] == "ranked", f"Expected ranked, got {result['status']}"
    assert len(result["rankings"]) == 3
    assert result["evaluated_ids"] == [1, 2, 3]
    conn.close()


def test_raw_without_cached_preview_uses_generate_fallback(tmp_db, fake_picker, fake_openai, monkeypatch, tmp_path):
    """RAW rows whose preview_path is NULL must regenerate via the canonical
    _generate_preview path, persist the result, and update preview_path.

    Regression pin for the 2026-05-17 production bug: after the first full
    analysis batch, every NEF and 88/274 RAFs had preview_path=NULL, so the
    prerank worker reported 0/N readable previews for every burst and the
    AI rank chip stayed in error for the whole library. Fix: on-demand
    generate + cache from the burst worker, same code path as /previews/<id>.
    """
    conn = _open_conn(tmp_db)
    for i in (1, 2, 3):
        _insert_image(conn, id=i, preview_path=None, format='NEF',
                      file_path=f'/some/dir/IMG{i:04d}.NEF')
    conn.commit()

    from phase2_quality import burst_ranker

    # Reset failure cache so prior tests don't bleed in.
    burst_ranker._raw_preview_failed.clear()

    # Stub the generator: return fake JPEG bytes for any RAW file path,
    # bypassing rawpy. The fallback writes these to disk and re-reads them
    # via _read_preview_b64, so we also stub the cache dir to tmp_path.
    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIFfake-jpeg-bytes"
    captured_calls: list[tuple] = []
    def _stub_generate(file_path, fmt):
        captured_calls.append((str(file_path), fmt))
        return fake_jpeg
    monkeypatch.setattr(
        "backend.routers.analysis._generate_preview", _stub_generate,
    )
    monkeypatch.setattr(
        "backend.routers.analysis.PREVIEW_CACHE_DIR", tmp_path,
    )
    # _read_preview_b64 will see the real bytes we wrote — stub it to
    # confirm the cache file actually got created on disk before reading.
    def _stub_read(p):
        if p and tmp_path.as_posix() in str(p):
            assert (tmp_path / Path(p).name).exists(), f"cache file missing: {p}"
            return "ZmFrZS1iNjQ="
        return None
    monkeypatch.setattr(burst_ranker, "_read_preview_b64", _stub_read)

    fake_openai.canned_content = json.dumps([
        {"image_index": 1, "rank": 1, "reason": "r1"},
        {"image_index": 2, "rank": 2, "reason": "r2"},
        {"image_index": 3, "rank": 3, "reason": "r3"},
    ])

    result = burst_ranker.rank_burst([1, 2, 3], conn)
    assert result["status"] == "ranked", f"Expected ranked, got {result['status']}"
    assert len(captured_calls) == 3, "_generate_preview should be called once per missing RAW"
    # preview_path was written back so /previews/<id> hits the cache next time.
    for i in (1, 2, 3):
        row = conn.execute("SELECT preview_path FROM images WHERE id = ?", (i,)).fetchone()
        assert row["preview_path"] == str(tmp_path / f"{i}.jpg")
    conn.close()


def test_raw_fallback_failure_is_cached_per_image(tmp_db, fake_picker, fake_openai, monkeypatch):
    """If _generate_preview raises on a particular RAW (corrupt file, libraw
    error), the failure must be cached in _raw_preview_failed so the next
    burst containing the same id doesn't pay the demosaic-then-fail cost
    again. Mirrors feedback_lazy_load_failure_caching.md."""
    conn = _open_conn(tmp_db)
    _insert_image(conn, id=1, preview_path=None, format='NEF', file_path='/bad/IMG.NEF')
    conn.commit()

    from phase2_quality import burst_ranker
    burst_ranker._raw_preview_failed.clear()

    call_count = [0]
    def _boom(file_path, fmt):
        call_count[0] += 1
        raise RuntimeError("simulated libraw failure")
    monkeypatch.setattr("backend.routers.analysis._generate_preview", _boom)

    row = conn.execute(
        "SELECT id, preview_path, file_path, format FROM images WHERE id = 1"
    ).fetchone()
    # Two consecutive resolution attempts — the second must short-circuit.
    assert burst_ranker._resolve_preview_bytes(row, conn) is None
    assert burst_ranker._resolve_preview_bytes(row, conn) is None
    assert call_count[0] == 1, "second call must hit _raw_preview_failed cache"
    assert 1 in burst_ranker._raw_preview_failed
    conn.close()


def test_inflight_registry_coalesces_concurrent_calls(tmp_db, fake_picker, fake_preview_read, monkeypatch):
    """Two concurrent /rank-burst calls for the same membership_hash must
    NOT both fire qwen. The first caller takes the inflight slot, the
    second waits on its result. This pins down the fix for the bug where
    a prerank worker's call + a user's loupe-open could double-trigger
    the model on a Mac with 6.5 GB VRAM, OOMing one of them and surfacing
    'AI rank unavailable' in the loupe even though the worker eventually
    succeeded."""
    import threading
    import time as _time
    from phase2_quality import burst_ranker

    conn = _open_conn(tmp_db)
    for i in (1, 2, 3):
        _insert_image(conn, id=i)
    conn.commit()

    # Track how many times the inner work (the leader's compute path) runs.
    call_count = {"n": 0}
    barrier_in = threading.Event()
    barrier_out = threading.Event()

    real_leader = burst_ranker._leader_compute_rank
    def _slow_leader(h, evaluated_ids, filtered_from, conn_arg, merged_map=None):
        call_count["n"] += 1
        barrier_in.set()           # signal "we're inside the leader"
        barrier_out.wait(timeout=2.0)  # block until the test releases us
        # Return a canned 'ranked' result so the leader's finally publishes
        # something useful for the waiter.
        return {
            "status": "ranked", "model": "stub", "members_hash": h,
            "cached": False, "rankings": [{"image_id": 1, "rank": 1, "reason": "r"}],
            "evaluated_ids": evaluated_ids, "filtered_from": filtered_from,
        }
    monkeypatch.setattr(burst_ranker, "_leader_compute_rank", _slow_leader)

    # Each thread gets its own sqlite Connection — sqlite3 doesn't let
    # connections cross threads by default.
    results: dict[str, dict] = {}
    def _thread1():
        c = _open_conn(tmp_db)
        results["leader"] = burst_ranker.rank_burst([1, 2, 3], c)
        c.close()
    def _thread2():
        c = _open_conn(tmp_db)
        results["waiter"] = burst_ranker.rank_burst([1, 2, 3], c)
        c.close()

    t1 = threading.Thread(target=_thread1)
    t1.start()
    # Wait for the leader to be inside the work block before launching the
    # second caller — otherwise the second one might race past the
    # inflight check first and become the leader itself.
    assert barrier_in.wait(timeout=2.0), "leader never entered the work block"
    t2 = threading.Thread(target=_thread2)
    t2.start()
    # Give the waiter a moment to land on the wait().
    _time.sleep(0.05)
    # Release the leader; both should now resolve.
    barrier_out.set()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)
    conn.close()

    # The compute path ran exactly once — the waiter coalesced.
    assert call_count["n"] == 1, f"Expected 1 leader call, got {call_count['n']}"
    assert results["leader"]["status"] == "ranked"
    assert results["waiter"]["status"] == "ranked"
    # Waiter is marked cached=True (it didn't pay an LLM cost).
    assert results["waiter"]["cached"] is True
    # Inflight slot is cleaned up.
    assert burst_ranker._members_hash([1, 2, 3]) not in burst_ranker._inflight


def test_cache_key_uses_filtered_set_not_input(tmp_db, fake_picker, fake_preview_read, fake_openai):
    """For a burst >_MAX_MEMBERS, the persisted members_hash must be sha1
    of the SORTED TOP-N ids — not of the full input."""
    from phase2_quality.burst_ranker import _MAX_MEMBERS

    conn = _open_conn(tmp_db)
    for i in range(1, 21):
        _insert_image(conn, id=i, overall_score=float(i))
    conn.commit()

    fake_openai.canned_content = json.dumps([
        {"image_index": j + 1, "rank": j + 1, "reason": "r"} for j in range(_MAX_MEMBERS)
    ])

    result = burst_ranker.rank_burst(list(range(1, 21)), conn)
    expected_top = list(range(21 - _MAX_MEMBERS, 21))
    expected_hash = hashlib.sha1(",".join(str(i) for i in expected_top).encode()).hexdigest()
    assert result["members_hash"] == expected_hash

    # And the row stored in the DB matches. With no embeddings, the dedup
    # step is a no-op so member_ids equals the top-N set.
    row = conn.execute(
        "SELECT members_hash, member_ids FROM burst_rankings WHERE members_hash = ?",
        (expected_hash,),
    ).fetchone()
    assert row is not None
    assert json.loads(row["member_ids"]) == expected_top
    conn.close()


# ---------------------------------------------------------------------------
# Near-duplicate dedup (added 2026-05-17)
# ---------------------------------------------------------------------------


def _insert_image_with_emb(conn, *, id: int, embedding: list[float], overall_score: float = 50.0) -> None:
    """Like _insert_image but also writes a SigLIP embedding JSON blob."""
    import json as _json
    conn.execute(
        """
        INSERT INTO images (id, filename, file_path, preview_path, format,
                            face_detected, overall_score, embedding, analysis_status)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?, 'done')
        """,
        (id, f"IMG{id:04d}.jpg", f"/photos/IMG{id:04d}.jpg", "/nonexistent.jpg",
         "JPEG", overall_score, _json.dumps(embedding)),
    )


def test_collapse_near_duplicates_merges_high_cosine(tmp_db):
    """Two embeddings with cosine 1.0 collapse to a single representative;
    a third unrelated embedding stays separate."""
    conn = _open_conn(tmp_db)
    # ids 1 & 2: identical embedding (cosine=1.0, ≥ threshold). id 3: orthogonal.
    _insert_image_with_emb(conn, id=1, embedding=[1.0, 0.0, 0.0], overall_score=80.0)
    _insert_image_with_emb(conn, id=2, embedding=[1.0, 0.0, 0.0], overall_score=40.0)
    _insert_image_with_emb(conn, id=3, embedding=[0.0, 1.0, 0.0], overall_score=60.0)
    conn.commit()

    reps, merged = burst_ranker._collapse_near_duplicates([1, 2, 3], conn)
    # id=1 has the higher overall_score so it wins as rep; id=2 is absorbed.
    assert set(reps) == {1, 3}
    assert sorted(merged[1]) == [1, 2]
    assert merged[3] == [3]
    conn.close()


def test_collapse_near_duplicates_keeps_distinct_pairs(tmp_db):
    """Embeddings below the threshold must NOT merge — micro-moment
    differences are the LLM's job to rank, not ours to flatten."""
    conn = _open_conn(tmp_db)
    # Two embeddings with cosine ≈ 0.9 (below the 0.97 threshold)
    import math
    a = [1.0, 0.0, 0.0]
    b = [math.cos(math.acos(0.9)), math.sin(math.acos(0.9)), 0.0]
    _insert_image_with_emb(conn, id=1, embedding=a)
    _insert_image_with_emb(conn, id=2, embedding=b)
    conn.commit()

    reps, merged = burst_ranker._collapse_near_duplicates([1, 2], conn)
    assert set(reps) == {1, 2}
    assert merged[1] == [1]
    assert merged[2] == [2]
    conn.close()


def test_collapse_near_duplicates_handles_missing_embeddings(tmp_db):
    """A burst whose members have no embeddings (e.g. legacy rows from
    before the SigLIP migration) must return a no-op rather than crash —
    sending the full set is preferable to silently failing the rank."""
    conn = _open_conn(tmp_db)
    # _insert_image leaves embedding NULL
    _insert_image(conn, id=1, overall_score=80.0)
    _insert_image(conn, id=2, overall_score=40.0)
    _insert_image(conn, id=3, overall_score=60.0)
    conn.commit()

    reps, merged = burst_ranker._collapse_near_duplicates([1, 2, 3], conn)
    assert set(reps) == {1, 2, 3}
    for r in reps:
        assert merged[r] == [r]
    conn.close()


def test_rank_burst_persists_near_duplicates_when_dedup_collapses_all(tmp_db, fake_picker, fake_openai):
    """When dedup collapses a burst below _MIN_MEMBERS, rank_burst returns
    status='near_duplicates' AND writes a sentinel row to burst_rankings so
    the UI can surface the state without re-running dedup on subsequent
    fetches. Regression pin for the 2026-05-17 UI-distinguishability fix."""
    conn = _open_conn(tmp_db)
    # Three photos with identical embeddings → cosine=1.0, all merge to one rep.
    _insert_image_with_emb(conn, id=1, embedding=[1.0, 0.0, 0.0], overall_score=80.0)
    _insert_image_with_emb(conn, id=2, embedding=[1.0, 0.0, 0.0], overall_score=40.0)
    _insert_image_with_emb(conn, id=3, embedding=[1.0, 0.0, 0.0], overall_score=60.0)
    conn.commit()

    result = burst_ranker.rank_burst([1, 2, 3], conn)
    assert result["status"] == "near_duplicates"
    # LLM was never called.
    assert fake_openai.last_payload is None

    # Sentinel row persisted, keyed on the PRE-DEDUP hash so the annotator
    # (which queries by the group's members hash) can find it.
    pre_dedup_hash = burst_ranker._members_hash([1, 2, 3])
    row = conn.execute(
        "SELECT outcome, rankings_json FROM burst_rankings WHERE members_hash = ?",
        (pre_dedup_hash,),
    ).fetchone()
    assert row is not None
    assert row["outcome"] == "near_duplicates"
    assert row["rankings_json"] == "[]"

    # Second call hits the sentinel and short-circuits (no second LLM
    # attempt, no second dedup run).
    result2 = burst_ranker.rank_burst([1, 2, 3], conn)
    assert result2["status"] == "near_duplicates"
    assert result2["cached"] is True
    conn.close()


def test_rank_burst_expands_rankings_to_absorbed_ids(tmp_db, fake_picker, fake_preview_read, fake_openai):
    """When dedup collapses ids, the final result must carry one ranking
    row per ORIGINAL id (rep + absorbed), with absorbed photos inheriting
    their rep's rank and a 'near-duplicate' reason."""
    conn = _open_conn(tmp_db)
    # ids 1, 2 share an embedding (will merge); id 3 is distinct.
    _insert_image_with_emb(conn, id=1, embedding=[1.0, 0.0, 0.0], overall_score=80.0)
    _insert_image_with_emb(conn, id=2, embedding=[1.0, 0.0, 0.0], overall_score=40.0)
    _insert_image_with_emb(conn, id=3, embedding=[0.0, 1.0, 0.0], overall_score=60.0)
    conn.commit()

    # LLM only sees 2 reps (ids 1 & 3). Provide a 2-item ranking.
    fake_openai.canned_content = json.dumps([
        {"image_index": 1, "rank": 1, "reason": "best frame"},
        {"image_index": 2, "rank": 2, "reason": "second"},
    ])

    result = burst_ranker.rank_burst([1, 2, 3], conn)
    assert result["status"] == "ranked"

    by_id = {r["image_id"]: r for r in result["rankings"]}
    # All three original ids present in the final rankings
    assert set(by_id.keys()) == {1, 2, 3}
    # id=2 inherits id=1's rank (its rep) with the near-duplicate reason
    assert by_id[2]["rank"] == by_id[1]["rank"]
    assert "near-duplicate" in by_id[2]["reason"].lower()
    # The evaluated_ids field surfaces the full set so the loupe chip knows
    # all three photos got an effective rank.
    assert set(result["evaluated_ids"]) == {1, 2, 3}
    conn.close()
