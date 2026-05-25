"""Tests for the background pre-rank worker in phase2_quality/prerank.py.

The worker spawns a daemon thread and calls rank_burst() for each queued
group. We stub rank_burst so the tests run synchronously-fast: the stub
records the calls and returns canned status/cached flags.

Why test the queue policy and not just the contract: the policy
(min_size filter, largest-first sort, max_groups cap, skip-on-cache
counter) is what determines whether a user's loupe-open is instant or
not. If we silently regress to "queue everything", a user with 100
similarity groups would spawn 100 minutes of background LLM work.
"""
from __future__ import annotations

import time

import pytest

from phase2_quality import prerank


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    """Spin-wait with a short sleep until predicate returns True or timeout
    elapses. Tests use this to wait for the background worker to drain."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"Predicate did not become true within {timeout}s")


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset prerank state between tests so the worker thread from a
    previous test doesn't bleed counters into the next one."""
    prerank.cancel()
    # Wait for any prior worker to actually exit so its counter writes
    # don't race with our fresh state setup.
    _wait_until(lambda: not prerank.snapshot()["running"], timeout=3.0)
    with prerank._lock:
        prerank._reset_counters_locked()
        prerank._state.queue.clear()
    yield
    prerank.cancel()
    _wait_until(lambda: not prerank.snapshot()["running"], timeout=3.0)


@pytest.fixture()
def stub_rank_burst(monkeypatch):
    """Replace `rank_burst` with a fast stub that records call args and
    returns whatever the test sets. Default: always 'ranked', cached=False."""
    calls: list[list[int]] = []
    config = {"status": "ranked", "cached": False}

    def _stub(member_ids, conn):
        calls.append(list(member_ids))
        return {
            "status": config["status"],
            "cached": config["cached"],
            "rankings": [],
            "evaluated_ids": sorted(member_ids),
            "filtered_from": len(member_ids),
            "members_hash": "stub",
            "model": "stub",
        }

    # Patch where the worker LOOKS UP the symbol — prerank._run imports
    # `from phase2_quality.burst_ranker import rank_burst` at call time,
    # so the patch must land on the source module.
    import phase2_quality.burst_ranker as _br
    monkeypatch.setattr(_br, "rank_burst", _stub)

    # Also stub get_db so the worker doesn't try to open a real connection.
    import backend.database as _db
    from contextlib import contextmanager
    @contextmanager
    def _fake_db():
        yield None
    monkeypatch.setattr(_db, "get_db", _fake_db)

    return {"calls": calls, "config": config}


# ---------------------------------------------------------------------------
# Queue policy: filter / sort / cap
# ---------------------------------------------------------------------------


def test_filters_groups_below_min_size(stub_rank_burst):
    """Groups smaller than _MIN_GROUP_SIZE are dropped before queueing —
    they don't help (LLM rank ≈ score-based for 2 photos) and they still
    cost a full model call."""
    prerank.enqueue_groups([[1, 2], [3, 4, 5], [6]])
    _wait_until(lambda: not prerank.snapshot()["running"])
    # Only the 3-member group was queued.
    assert len(stub_rank_burst["calls"]) == 1
    assert sorted(stub_rank_burst["calls"][0]) == [3, 4, 5]


def test_sorts_largest_first(stub_rank_burst):
    """User opens the biggest groups first — those should be ready first.
    The worker processes the queue in insertion order, so the enqueue
    function is what controls priority."""
    prerank.enqueue_groups([
        [1, 2, 3],          # size 3
        [10, 11, 12, 13],   # size 4
        [20, 21, 22, 23, 24],  # size 5 — biggest, should rank first
    ])
    _wait_until(lambda: not prerank.snapshot()["running"])
    sizes = [len(c) for c in stub_rank_burst["calls"]]
    assert sizes == [5, 4, 3]


def test_caps_at_max_groups(stub_rank_burst):
    """A pathological folder with 200 similarity groups must not enqueue
    all of them — that's a runaway background workload."""
    groups = [[i * 10, i * 10 + 1, i * 10 + 2] for i in range(50)]
    prerank.enqueue_groups(groups, max_groups=5)
    _wait_until(lambda: not prerank.snapshot()["running"])
    assert len(stub_rank_burst["calls"]) == 5


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_drains_queue(monkeypatch):
    """Cancel must stop the worker and clear pending items so the next
    enqueue starts fresh (counters reset, no leftover work)."""
    # Block the worker on the first item so we can race a cancel against it.
    import threading
    barrier = threading.Event()

    def _slow_stub(member_ids, conn):
        barrier.wait(timeout=2.0)  # block until the test releases us
        return {"status": "ranked", "cached": False, "rankings": [],
                "evaluated_ids": sorted(member_ids), "filtered_from": len(member_ids),
                "members_hash": "stub", "model": "stub"}

    import phase2_quality.burst_ranker as _br
    monkeypatch.setattr(_br, "rank_burst", _slow_stub)
    import backend.database as _db
    from contextlib import contextmanager
    @contextmanager
    def _fake_db():
        yield None
    monkeypatch.setattr(_db, "get_db", _fake_db)

    prerank.enqueue_groups([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
    # Worker has popped the first job and is blocked on the barrier.
    # Cancel drains items 2 and 3 from the queue.
    prerank.cancel()
    barrier.set()
    _wait_until(lambda: not prerank.snapshot()["running"])
    snap = prerank.snapshot()
    assert snap["queued"] == 0
    # First item completed; cancel only affects items not yet popped.
    assert snap["completed"] == 1


# ---------------------------------------------------------------------------
# Counter semantics: cached vs fresh vs failed
# ---------------------------------------------------------------------------


def test_cached_result_counts_as_skipped(stub_rank_burst):
    """rank_burst returning cached=True means the LLM didn't run — count
    those as 'skipped' (cache hit) instead of 'completed' (fresh rank)
    so the UI can show 'all in cache, nothing to wait for'."""
    stub_rank_burst["config"]["cached"] = True
    prerank.enqueue_groups([[1, 2, 3], [4, 5, 6]])
    _wait_until(lambda: not prerank.snapshot()["running"])
    snap = prerank.snapshot()
    assert snap["completed"] == 0
    assert snap["skipped"] == 2
    assert snap["failed"] == 0


def test_non_ranked_status_counts_as_failed(stub_rank_burst):
    """no_vision_model / error / too_few from rank_burst all count as
    failed in the prerank queue — the worker doesn't retry, the user
    will see the appropriate chip on opening the loupe."""
    stub_rank_burst["config"]["status"] = "no_vision_model"
    prerank.enqueue_groups([[1, 2, 3], [4, 5, 6]])
    _wait_until(lambda: not prerank.snapshot()["running"])
    snap = prerank.snapshot()
    assert snap["failed"] == 2
    assert snap["completed"] == 0


# ---------------------------------------------------------------------------
# Snapshot contract
# ---------------------------------------------------------------------------


def test_snapshot_returns_full_contract(stub_rank_burst):
    """Frontend reads these fields by name. Pin them down."""
    snap = prerank.snapshot()
    expected = {"running", "queued", "completed", "failed", "skipped",
                "near_duplicates", "total_queued", "last_error",
                "current_job_hash"}
    assert set(snap.keys()) == expected
    assert isinstance(snap["running"], bool)
    assert isinstance(snap["queued"], int)
    assert isinstance(snap["near_duplicates"], int)


def test_current_job_hash_populated_while_running(monkeypatch):
    """The grid uses snapshot()['current_job_hash'] to mark exactly ONE tile
    as 'AI is looking at this right now'. While the worker is mid-call, the
    hash must be non-null and match the in-flight job's members hash. When
    the worker is idle, it must be None."""
    import threading
    barrier_in = threading.Event()
    barrier_out = threading.Event()

    def _slow_stub(member_ids, conn):
        barrier_in.set()    # signal we're mid-call
        barrier_out.wait(timeout=2.0)  # block until test releases us
        return {"status": "ranked", "cached": False, "rankings": [],
                "evaluated_ids": sorted(member_ids), "filtered_from": len(member_ids),
                "members_hash": "stub", "model": "stub"}

    import phase2_quality.burst_ranker as _br
    monkeypatch.setattr(_br, "rank_burst", _slow_stub)
    import backend.database as _db
    from contextlib import contextmanager
    @contextmanager
    def _fake_db():
        yield None
    monkeypatch.setattr(_db, "get_db", _fake_db)

    member_ids = [10, 11, 12]
    prerank.enqueue_groups([member_ids])
    # Wait until the worker has popped + published the hash.
    assert barrier_in.wait(timeout=2.0), "worker never reached the LLM call"
    snap = prerank.snapshot()
    expected_hash = _br._members_hash(member_ids)
    assert snap["current_job_hash"] == expected_hash
    # Release the call so the worker can finish and clear the hash.
    barrier_out.set()
    _wait_until(lambda: not prerank.snapshot()["running"])
    assert prerank.snapshot()["current_job_hash"] is None


def test_current_job_hash_cleared_on_failure(monkeypatch):
    """If rank_burst raises (or returns a non-'ranked' status), the worker
    must still clear current_job_hash before popping the next job. Otherwise
    the next snapshot would leak the previous group's hash and the grid
    would mark the wrong tile as in_progress."""
    def _failing_stub(member_ids, conn):
        raise RuntimeError("stubbed failure")

    import phase2_quality.burst_ranker as _br
    monkeypatch.setattr(_br, "rank_burst", _failing_stub)
    import backend.database as _db
    from contextlib import contextmanager
    @contextmanager
    def _fake_db():
        yield None
    monkeypatch.setattr(_db, "get_db", _fake_db)

    prerank.enqueue_groups([[1, 2, 3]])
    _wait_until(lambda: not prerank.snapshot()["running"])
    snap = prerank.snapshot()
    assert snap["current_job_hash"] is None
    assert snap["failed"] == 1
    assert snap["last_error"] is not None


def test_enqueue_returns_snapshot_shape(stub_rank_burst):
    """The enqueue endpoint returns the snapshot directly so the frontend
    can render initial state without a second poll."""
    snap = prerank.enqueue_groups([[1, 2, 3]])
    assert "total_queued" in snap
    assert "near_duplicates" in snap  # New field on the contract.
    # total_queued is bumped synchronously by enqueue, before the worker runs.
    assert snap["total_queued"] >= 1


def test_near_duplicates_status_counted_separately(stub_rank_burst):
    """status='near_duplicates' from rank_burst is a DELIBERATE non-rank
    outcome (the dedup determined every frame was a near-duplicate), not
    a failure. The counter routes it to its own bucket so the UI can
    distinguish 'AI saw nothing to compare' from 'AI errored out.'"""
    stub_rank_burst["config"]["status"] = "near_duplicates"
    prerank.enqueue_groups([[1, 2, 3], [4, 5, 6]])
    _wait_until(lambda: not prerank.snapshot()["running"])
    snap = prerank.snapshot()
    assert snap["near_duplicates"] == 2
    assert snap["failed"] == 0
    assert snap["completed"] == 0


def test_enqueue_during_inflight_cancel_drains_appended_work(monkeypatch):
    """Regression pin for the 2026-05-17 cancel-flag-leak bug.

    When cancel() fires while a job is in-flight, _state.cancelled.is_set()
    becomes True. The worker honors it only AFTER the current job finishes.
    If a new enqueue_groups arrives in that window, the appended jobs are
    queued — but without clearing the flag, the worker observes 'cancelled'
    on its next loop iteration and exits, leaving the appended work
    undrained. Reproduced live during the post-optimization drain.

    Fix: enqueue_groups now unconditionally clears _state.cancelled so
    new work always gets a fresh shot.
    """
    import threading
    in_flight = threading.Event()
    release = threading.Event()
    call_count = {"n": 0}

    def _two_stage_stub(member_ids, conn):
        call_count["n"] += 1
        if call_count["n"] == 1:
            in_flight.set()
            release.wait(timeout=2.0)  # block first call until test releases
        return {"status": "ranked", "cached": False, "rankings": [],
                "evaluated_ids": sorted(member_ids), "filtered_from": len(member_ids),
                "members_hash": f"stub{call_count['n']}", "model": "stub"}

    import phase2_quality.burst_ranker as _br
    monkeypatch.setattr(_br, "rank_burst", _two_stage_stub)
    import backend.database as _db
    from contextlib import contextmanager
    @contextmanager
    def _fake_db():
        yield None
    monkeypatch.setattr(_db, "get_db", _fake_db)

    # 1. Enqueue the first batch. Worker pops job 1 and blocks on `release`.
    prerank.enqueue_groups([[1, 2, 3]])
    assert in_flight.wait(timeout=2.0), "worker never started on job 1"

    # 2. Cancel mid-flight — sets the flag, drains the (empty) queue.
    prerank.cancel()

    # 3. Re-enqueue. WITHOUT the fix, the worker would finish job 1, see
    #    cancelled.is_set() == True, and exit without touching jobs 2-3.
    prerank.enqueue_groups([[4, 5, 6], [7, 8, 9]])

    # 4. Release job 1 and wait for the worker to drain everything.
    release.set()
    _wait_until(lambda: not prerank.snapshot()["running"])
    snap = prerank.snapshot()
    # All three jobs should have run (1 from the original batch + 2 appended).
    # With the bug, only job 1 would have completed and queued would still
    # show the appended work as untouched.
    assert call_count["n"] == 3, f"expected 3 worker invocations, got {call_count['n']}"
    assert snap["queued"] == 0
