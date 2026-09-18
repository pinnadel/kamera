"""
One process-wide lock serialising every PyTorch model load.

The problem it solves
---------------------
`torch` + `transformers` + `pyiqa`/`timm` initialise model weights on the
**meta device** and only materialise them on the final `.to(device)` call.
The machinery that does this (`accelerate.init_empty_weights` and friends)
works by monkey-patching `torch.nn.Module.register_parameter` — that patch is
**global process state**, not per-model and not thread-local.

So when thread A is inside `AutoModel.from_pretrained()` (SigLIP) while
thread B constructs a net inside `pyiqa.create_metric()`, thread B's
parameters are created on the meta device without ever being filled. B's
subsequent `.to(device)` then raises:

    NotImplementedError: Cannot copy out of meta tensor; no data!

The loser's loader latches `_load_failed`, so **two of the seven scorers
silently return None for the entire rest of the batch** — no crash, no user
visible error, just blank IQA and Aesthetic columns.

Why the previous per-module locks were not enough
-------------------------------------------------
`iqa_scorer`, `aesthetic_scorer` and `similarity_scorer` each had their own
`threading.Lock`. Each correctly prevented *one module* from loading twice
concurrently — but the collision is **between** modules, so no thread ever
contended for the same lock. `face_identity` had no lock at all. Reproduced
deterministically (2/2) by loading pyiqa and SigLIP from two threads;
loading pyiqa twice from two threads passes, which is why the per-module
locks looked sufficient.

`backend/routers/analysis.py::_warm_models` loads the three torch models
sequentially in one background thread, which is why this is rarely seen in
the running app. But that is a *mitigation*, not a guarantee: if analysis
starts before warm-up finishes, `analyze_photo_quality`'s six-worker pool
calls the same loaders from different threads and they collide anyway.

Usage
-----
Every lazy loader that ends in `.to(device)` must hold this lock for the
whole construct-and-move sequence::

    with MODEL_LOAD_LOCK:
        if _model is None:
            _model = build().to(device)

Hold it across construction *and* `.to()` — the patch window spans both.
Take the usual `if _model is not None: return _model` fast path before the
lock so warm calls stay uncontended.

MediaPipe (`face_analyzer`) deliberately does **not** use this lock: it is
TFLite, never touches torch module construction, and serialising it would
only add first-photo latency.

Re-entrant so a loader that transitively triggers another load on the same
thread can't deadlock.
"""

import threading

MODEL_LOAD_LOCK = threading.RLock()
