"""Background pre-ranking worker.

When a batch analysis finishes, the loupe is the natural next step the user
will take — and the burst-rank call inside the loupe takes 30-90 s, mostly
spent in the vision encoder of qwen2.5vl. That wait is what makes the
feature feel "slow" even though the actual culling work happens elsewhere.

This module pre-emptively runs `rank_burst()` for the biggest similarity
groups in the background, so by the time the user opens a loupe the result
is already in the burst_rankings cache. From the user's perspective the
amber ring just appears, no spinner.

Concurrency model:
  - Single worker thread. qwen2.5vl holds ~6.5 GB VRAM on Apple silicon;
    a second concurrent runner OOMs the unified memory pool.
  - Cancellable via a threading.Event. The watch route fires cancel on
    folder change so we don't waste cycles on stale folders.
  - The worker calls rank_burst() with a fresh connection per item so it
    never holds a transaction longer than one item.

Why a module-level singleton, not a queue per request:
  We need exactly one background pipeline regardless of how many times the
  trigger endpoint is hit. A second POST while a queue is running should
  *enqueue more work*, not spawn a parallel worker.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class _Job:
    """One pre-rank work item: an ordered list of image_ids that make up
    a similarity group. We don't store the group's metadata — just the ids
    — because the only thing the worker needs to call rank_burst() is the
    member list, and the cache key is derived from the sorted ids."""
    image_ids: list[int]
    size: int


@dataclass
class _State:
    """Module-level singleton state. Guarded by a single lock so the
    worker, the queue mutators, and the status-snapshot reader all see
    a consistent view."""
    queue: deque[_Job] = field(default_factory=deque)
    running: bool = False
    cancelled: threading.Event = field(default_factory=threading.Event)
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    # Deliberate non-rank outcome: burst_ranker's intra-burst SigLIP dedup
    # collapsed the group below _MIN_MEMBERS, so no LLM call was made and
    # status='near_duplicates' was returned. Counted separately from `failed`
    # because the UI surfaces it as a first-class outcome (the "≈" chip), not
    # an error. See burst_ranker._collapse_near_duplicates.
    near_duplicates: int = 0
    total_queued: int = 0
    last_error: str | None = None
    # Hash of the burst whose rank is currently executing (None when the
    # worker is between jobs). Exposed via snapshot() so the grid can mark
    # exactly one tile as "AI is looking at this right now."
    current_job_hash: str | None = None


_state = _State()
_lock = threading.Lock()
_worker: threading.Thread | None = None


# Capped so we never blast through 100 calls in the background — the user's
# typical session opens 5–15 groups. Past that the worker becomes wasted
# work. Pre-ranks fire in descending size order so the biggest, most-likely-
# to-be-opened bursts are ready first.
_DEFAULT_MAX_GROUPS = 20

# Minimum group size to bother pre-ranking. Below this, the loupe shows a
# silent score-based pick anyway and the LLM rank wouldn't change anything
# meaningful — and the call still costs the runner-load + per-image vision
# encode time. Mirrors the in-rank-burst _MIN_MEMBERS check but separately
# tunable for pre-rank policy.
_MIN_GROUP_SIZE = 3


def snapshot() -> dict:
    """Read-only status the frontend can poll if it wants to show progress.

    Shape:
      {
        "running":          bool,     # worker thread alive and processing
        "queued":           int,      # items waiting (does NOT count the in-flight job)
        "completed":        int,      # successfully ranked this run
        "failed":           int,      # rank_burst returned error / no_vision_model
        "skipped":          int,      # cache hit, no LLM call needed
        "near_duplicates":  int,      # dedup collapsed below _MIN_MEMBERS (deliberate)
        "total_queued":     int,      # original queue depth (for "12 of 20" UI)
        "last_error":       str|None,
        "current_job_hash": str|None, # members_hash of the in-flight burst; None when idle
      }
    """
    with _lock:
        return {
            "running": _state.running,
            "queued": len(_state.queue),
            "completed": _state.completed,
            "failed": _state.failed,
            "skipped": _state.skipped,
            "near_duplicates": _state.near_duplicates,
            "total_queued": _state.total_queued,
            "last_error": _state.last_error,
            "current_job_hash": _state.current_job_hash,
        }


def cancel() -> None:
    """Signal the worker to stop after its current item finishes. Drains
    the queue so a subsequent enqueue starts clean. Idempotent."""
    with _lock:
        _state.cancelled.set()
        _state.queue.clear()


def _reset_counters_locked() -> None:
    """Caller must hold _lock."""
    _state.completed = 0
    _state.failed = 0
    _state.skipped = 0
    _state.near_duplicates = 0
    _state.total_queued = 0
    _state.last_error = None
    _state.current_job_hash = None
    _state.cancelled.clear()


def enqueue_groups(
    groups: list[list[int]],
    *,
    max_groups: int = _DEFAULT_MAX_GROUPS,
    min_size: int = _MIN_GROUP_SIZE,
) -> dict:
    """Filter, sort, and queue groups for background pre-ranking.

    `groups` is a list of image-id lists, one per similarity group. We
    filter to size >= min_size, sort by size descending (biggest first
    so the most-likely-to-be-opened groups are ready first), cap at
    max_groups, then enqueue. Returns the same shape as snapshot() so
    the caller can immediately render a progress UI.

    If a worker is already running, the new items are appended to the
    existing queue. The caller should call cancel() first if they want
    a clean restart (e.g. folder changed).

    Always clears the cancellation flag so a prior cancel() that fired
    while a job was in-flight doesn't block the freshly-queued work.
    Without this, the worker would finish its current job, observe
    cancelled.is_set() == True, and exit — leaving the new queue
    undrained until the next /prerank-cancel + re-enqueue cycle.
    """
    eligible = [g for g in groups if len(g) >= min_size]
    eligible.sort(key=len, reverse=True)
    eligible = eligible[:max_groups]

    with _lock:
        # If the worker isn't running, we're starting a fresh run — reset
        # counters. Otherwise we're appending and the existing counters
        # stay (caller may have called enqueue twice during a session).
        if not _state.running:
            _reset_counters_locked()
        # New work has arrived — by definition it is NOT cancelled. Clearing
        # here is correct whether the worker is idle (no-op) or mid-flight
        # (overrides a prior cancel signal so the appended jobs get drained).
        _state.cancelled.clear()
        for ids in eligible:
            _state.queue.append(_Job(image_ids=list(ids), size=len(ids)))
            _state.total_queued += 1
        snap = {
            "running": _state.running,
            "queued": len(_state.queue),
            "completed": _state.completed,
            "failed": _state.failed,
            "skipped": _state.skipped,
            "near_duplicates": _state.near_duplicates,
            "total_queued": _state.total_queued,
            "last_error": _state.last_error,
            "current_job_hash": _state.current_job_hash,
        }

    # Start the worker if it isn't running. Done outside the lock to keep
    # critical section short.
    _ensure_worker_running()
    return snap


def _ensure_worker_running() -> None:
    """Spawn the singleton worker thread if it isn't alive. Safe to call
    repeatedly — checks the running flag under the lock and only starts
    when the queue has work AND no worker is currently active."""
    global _worker
    with _lock:
        if _state.running:
            return
        if not _state.queue:
            return
        _state.running = True
        _state.cancelled.clear()
    _worker = threading.Thread(target=_run, name="prerank-worker", daemon=True)
    _worker.start()


def _run() -> None:
    """Worker thread body. Pops one job at a time, calls rank_burst(),
    moves on. Exits when the queue is empty or cancel() was called."""
    # Imported here to avoid a circular import at module load: prerank is
    # imported from a router, which would otherwise need rank_burst loaded
    # before its own module is fully initialised.
    from backend.database import get_db
    from phase2_quality.burst_ranker import rank_burst, _members_hash

    logger.info("prerank: worker started")
    try:
        while True:
            with _lock:
                if _state.cancelled.is_set() or not _state.queue:
                    break
                job = _state.queue.popleft()
                # Publish the in-flight hash BEFORE releasing the lock so
                # any reader between pop and rank_burst sees the live job.
                _state.current_job_hash = _members_hash(job.image_ids)

            t0 = time.monotonic()
            try:
                with get_db() as conn:
                    result = rank_burst(job.image_ids, conn)
                status = result.get("status")
                cached = bool(result.get("cached"))
                if status == "ranked":
                    with _lock:
                        if cached:
                            _state.skipped += 1
                        else:
                            _state.completed += 1
                    logger.info(
                        "prerank: group size=%d ranked in %.1fs (cached=%s)",
                        job.size, time.monotonic() - t0, cached,
                    )
                elif status == "near_duplicates":
                    # Deliberate non-rank outcome: burst_ranker's SigLIP dedup
                    # collapsed every frame to a single rep, so there was
                    # nothing for the LLM to compare. The sentinel row is
                    # persisted by burst_ranker; the UI surfaces a "≈" chip
                    # via prerank_state='near_duplicates'. Not a failure.
                    with _lock:
                        _state.near_duplicates += 1
                    logger.info(
                        "prerank: group size=%d collapsed to near-duplicates (no LLM call)",
                        job.size,
                    )
                else:
                    # no_vision_model / too_few / error — don't retry; the
                    # user will see the appropriate chip when they open
                    # the loupe and can act on it themselves.
                    with _lock:
                        _state.failed += 1
                    logger.info(
                        "prerank: group size=%d returned status=%r (no retry)",
                        job.size, status,
                    )
            except Exception as exc:
                logger.exception("prerank: worker error on group size=%d", job.size)
                with _lock:
                    _state.failed += 1
                    _state.last_error = str(exc)
            finally:
                # Always clear the in-flight hash — success, parse failure,
                # or unexpected exception. Without this, a crash would leak
                # the previous job's hash into the next snapshot.
                with _lock:
                    _state.current_job_hash = None
    finally:
        with _lock:
            _state.running = False
            _state.current_job_hash = None
        logger.info("prerank: worker exited")


# Type alias for callers that don't want the dataclass — used by the
# /prerank-groups endpoint which receives raw int lists from the client.
EnqueueFn = Callable[[list[list[int]]], dict]
