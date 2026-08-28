"""
Guards the process-wide model-load lock.

The bug (found 2026-08-28, root-caused to a 2026-07-19 partial fix):
`accelerate` implements meta-device weight init by monkey-patching
`torch.nn.Module.register_parameter` — **global** process state. So a thread
inside `AutoModel.from_pretrained()` (SigLIP) makes a *different* thread's
`pyiqa.create_metric()` build its net on the meta device, and that thread's
`.to(device)` then dies with "Cannot copy out of meta tensor". The loser
latches `_load_failed`, so IQA and Aesthetic return None for the whole batch —
silently, with no user-visible error.

Each scorer previously had its own `threading.Lock`, which could never help:
the collision is *between* modules, so no two threads contended for the same
lock. Loading pyiqa twice concurrently passes; loading pyiqa against SigLIP
failed 2/2. That asymmetry is why the per-module locks looked correct.

These tests pin both halves of the fix: every loader references the one shared
lock, and each actually *holds* it at the moment it is about to construct its
model.

Test technique: each loader calls `_begin()` (the model_status toast hook)
inside the lock, immediately before the expensive construction. Patching that
name in the loader's own module namespace lets us observe the lock state at
exactly the right instant and abort before anything heavy loads, so these
tests are fast and never touch the network or the weight cache. Patching the
library entry points instead (`pyiqa.create_metric`, `transformers.AutoModel`)
is not reliable here — `transformers` is a `_LazyModule` and the swap does not
take effect under pytest even though the module attribute reads back correctly.
"""
import importlib
import threading

import pytest

from phase2_quality.model_loading import MODEL_LOAD_LOCK


class _Sentinel(Exception):
    """Raised from the patched _begin to abort before the real model loads."""


def _lock_held_by_another_thread() -> bool:
    """
    True if MODEL_LOAD_LOCK is currently held.

    Probed from a *separate* thread on purpose: MODEL_LOAD_LOCK is an RLock,
    so a non-blocking acquire from the thread that already owns it always
    succeeds and would make this check vacuously false.
    """
    got = []

    def probe():
        acquired = MODEL_LOAD_LOCK.acquire(blocking=False)
        if acquired:
            MODEL_LOAD_LOCK.release()
        got.append(acquired)

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return not got[0]


def test_lock_is_reentrant():
    """
    Must be an RLock: a loader that transitively triggers another load on the
    same thread would deadlock on a plain Lock.
    """
    with MODEL_LOAD_LOCK:
        assert MODEL_LOAD_LOCK.acquire(blocking=False), "not re-entrant"
        MODEL_LOAD_LOCK.release()


def test_probe_helper_detects_a_held_lock():
    """The helper every test below depends on actually works."""
    assert not _lock_held_by_another_thread()
    with MODEL_LOAD_LOCK:
        assert _lock_held_by_another_thread()


# --- Structural: every torch loader shares the ONE lock ----------------------

@pytest.mark.parametrize(
    "module_name",
    [
        "phase2_quality.iqa_scorer",
        "phase2_quality.aesthetic_scorer",
        "phase2_quality.similarity_scorer",
    ],
)
def test_scorers_share_the_process_wide_lock(module_name):
    """
    `is`, not `==`: the failure being guarded against is someone reintroducing
    a module-local `threading.Lock()`, which this catches unambiguously.
    """
    module = importlib.import_module(module_name)
    assert module._load_lock is MODEL_LOAD_LOCK, (
        f"{module_name} has its own lock — it will not serialise against the "
        "other loaders, which is the entire failure mode"
    )


def test_face_identity_references_the_lock():
    """
    face_identity had no lock at all and still calls `.to(device)`, so it could
    both corrupt and be corrupted. It has no `_load_lock` alias, so assert on
    the loader's source.
    """
    import inspect

    from phase2_quality import face_identity

    assert "MODEL_LOAD_LOCK" in inspect.getsource(face_identity._get_model)


# --- Behavioural: the lock is HELD at the moment of model construction -------

# (module, loader attribute, state attributes to reset before the call)
_LOADERS = [
    ("phase2_quality.iqa_scorer", "_get_metric", {"_metric": None, "_load_failed": False}),
    ("phase2_quality.aesthetic_scorer", "_get_metric", {"_metric": None, "_load_failed": False}),
    ("phase2_quality.similarity_scorer", "_get_siglip", {"_siglip": None, "_load_failed": False}),
    ("phase2_quality.face_identity", "_get_model", {"_model": None, "_load_failed": False}),
]


@pytest.mark.parametrize(
    "module_name, loader_name, reset",
    _LOADERS,
    ids=[m.rsplit(".", 1)[-1] for m, _, _ in _LOADERS],
)
def test_loader_holds_shared_lock_before_constructing(module_name, loader_name, reset, monkeypatch):
    module = importlib.import_module(module_name)
    seen = {}

    def fake_begin(*args, **kwargs):
        seen["held"] = _lock_held_by_another_thread()
        raise _Sentinel()

    monkeypatch.setattr(module, "_begin", fake_begin)
    for attr, value in reset.items():
        monkeypatch.setattr(module, attr, value)

    # face_identity swallows exceptions and returns None; the others re-raise.
    # Either is fine — we only care about the state recorded inside _begin.
    try:
        getattr(module, loader_name)()
    except _Sentinel:
        pass

    assert seen.get("held"), (
        f"{module_name}.{loader_name} reached model construction without "
        "holding the shared MODEL_LOAD_LOCK"
    )


# --- The failure mode itself: two loaders must never overlap -----------------

def test_two_loaders_never_overlap(monkeypatch):
    """
    End-to-end shape of the real bug with the expensive construction skipped:
    run the IQA and SigLIP loaders from two threads released simultaneously and
    assert their in-lock windows are disjoint. Under the pre-fix per-module
    locks these overlap, which is exactly when accelerate's global patch leaks
    across threads.
    """
    from phase2_quality import iqa_scorer, similarity_scorer

    active = 0
    max_active = 0
    bookkeeping = threading.Lock()

    def fake_begin(*args, **kwargs):
        nonlocal active, max_active
        with bookkeeping:
            active += 1
            max_active = max(max_active, active)
        threading.Event().wait(0.05)   # widen the window a real load would have
        with bookkeeping:
            active -= 1
        raise _Sentinel()

    monkeypatch.setattr(iqa_scorer, "_begin", fake_begin)
    monkeypatch.setattr(similarity_scorer, "_begin", fake_begin)
    monkeypatch.setattr(iqa_scorer, "_metric", None)
    monkeypatch.setattr(iqa_scorer, "_load_failed", False)
    monkeypatch.setattr(similarity_scorer, "_siglip", None)
    monkeypatch.setattr(similarity_scorer, "_load_failed", False)

    start = threading.Barrier(2)
    entered = []

    def run(fn):
        start.wait()
        try:
            fn()
        except _Sentinel:
            entered.append(fn)
        except Exception:
            pass

    threads = [
        threading.Thread(target=run, args=(iqa_scorer._get_metric,)),
        threading.Thread(target=run, args=(similarity_scorer._get_siglip,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Guard against the assertion below passing vacuously because neither
    # loader actually reached its construction window.
    assert len(entered) == 2, "both loaders should have reached _begin"
    assert max_active == 1, (
        f"{max_active} model constructions overlapped — accelerate's global "
        "meta-device patch can leak between them"
    )
