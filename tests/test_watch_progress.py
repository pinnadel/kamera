"""FolderWatcher live-progress counters + decision-folder skip guard.

These cover the watch-progress signal (GET /watch-progress), the fix for the
"Could not load image" toast (files moved into _Keeps/_Maybes/_Rejects must never
be (re)analyzed), and the queued single-worker pipeline (the fix for the
thread-per-file meltdown: a bulk card drop must not spawn hundreds of
concurrent analyses). No real photos / cv2 needed.
"""
import time
from pathlib import Path

from backend.file_watcher import FolderWatcher, _PhotoEventHandler


def test_counters_start_at_zero_and_cycle():
    w = FolderWatcher(Path("/tmp/none.db"))
    assert w.in_flight == 0 and w.analyzed == 0
    p = Path("/card/folder/DSCF0900.JPG")
    w._enqueue(p)
    assert w.in_flight == 1 and w.analyzed == 0
    w._finish(p, analyzed=True)
    assert w.in_flight == 0 and w.analyzed == 1


def test_finish_without_analyze_does_not_bump_analyzed():
    w = FolderWatcher(Path("/tmp/none.db"))
    p = Path("/card/folder/DSCF0900.JPG")
    w._enqueue(p)
    w._finish(p, analyzed=False)   # e.g. file vanished during settle
    assert w.in_flight == 0 and w.analyzed == 0


def test_enqueue_dedupes_repeat_events():
    """One copy can fire several watchdog events for the same file — only the
    first may queue an analysis. After the file is finished it may queue again
    (a genuine rewrite)."""
    w = FolderWatcher(Path("/tmp/none.db"))
    p = Path("/card/folder/DSCF0900.JPG")
    w._enqueue(p)
    w._enqueue(p)
    assert w.in_flight == 1 and w._queue.qsize() == 1
    w._finish(p, analyzed=True)
    w._enqueue(p)
    assert w.in_flight == 1 and w._queue.qsize() == 2


def test_handle_skips_decision_subfolder_files():
    w = FolderWatcher(Path("/tmp/x.db"))
    h = _PhotoEventHandler(Path("/tmp/x.db"), owner=w)
    h._handle("/card/folder/_Keeps/DSCF0856.JPG")   # decided → skip
    h._handle("/card/folder/_Maybes/DSCF0857.RAF")  # decided → skip
    h._handle("/card/folder/_Rejects/DSCF0858.JPG")   # decided → skip
    assert w.in_flight == 0 and w._queue.qsize() == 0


def test_handle_enqueues_root_files():
    w = FolderWatcher(Path("/tmp/x.db"))
    h = _PhotoEventHandler(Path("/tmp/x.db"), owner=w)
    h._handle("/card/folder/DSCF0900.JPG")          # fresh import → analyze
    assert w.in_flight == 1 and w._queue.qsize() == 1


def test_handle_ignores_unsupported_extensions():
    w = FolderWatcher(Path("/tmp/x.db"))
    h = _PhotoEventHandler(Path("/tmp/x.db"), owner=w)
    h._handle("/card/folder/notes.txt")
    assert w.in_flight == 0 and w._queue.qsize() == 0


def test_worker_drains_queue_sequentially(monkeypatch, tmp_path):
    """A bulk drop is analyzed one file at a time by the single worker —
    counters end balanced and every file is seen exactly once, in order."""
    from backend import file_watcher

    seen = []
    monkeypatch.setattr(
        file_watcher, "_analyze_and_store",
        lambda p, db, on_error=None: seen.append(p.name),
    )
    monkeypatch.setattr(file_watcher, "SETTLE_SECONDS", 0.0)

    # Watch an EMPTY dir so the real observer stays silent; files live outside
    # it and are fed through _enqueue directly — deterministic, no FSEvents.
    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()
    files = []
    for name in ("DSCF0901.JPG", "DSCF0902.RAF", "DSCF0903.JPG"):
        f = tmp_path / name
        f.write_bytes(b"x")
        files.append(f)

    w = FolderWatcher(Path("/tmp/none.db"))
    w.start(str(watch_dir))
    try:
        for f in files:
            w._enqueue(f)
        deadline = time.time() + 5
        while w.analyzed < len(files) and time.time() < deadline:
            time.sleep(0.02)
    finally:
        w.stop()

    assert seen == ["DSCF0901.JPG", "DSCF0902.RAF", "DSCF0903.JPG"]
    assert w.in_flight == 0 and w.analyzed == 3


def test_stop_abandons_backlog(monkeypatch, tmp_path):
    """stop() must not analyze the queued backlog — the worker exits promptly."""
    from backend import file_watcher

    seen = []
    monkeypatch.setattr(
        file_watcher, "_analyze_and_store",
        lambda p, db, on_error=None: seen.append(p.name),
    )
    # Long settle keeps the worker parked on the first file while we stop.
    monkeypatch.setattr(file_watcher, "SETTLE_SECONDS", 30.0)

    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()
    f = tmp_path / "DSCF0901.JPG"
    f.write_bytes(b"x")

    w = FolderWatcher(Path("/tmp/none.db"))
    w.start(str(watch_dir))
    w._enqueue(f)
    time.sleep(0.1)   # let the worker dequeue and enter its settle sleep
    w.stop()
    assert w.watching is None
    assert seen == []   # backlog dropped, nothing analyzed after stop


def test_vanished_file_is_skipped_not_errored(monkeypatch, tmp_db, tmp_path):
    """A file moved/renamed out from under the watcher during analysis must be
    skipped silently — no 'error' row, no on_error toast (the [Errno 2] case)."""
    from backend import file_watcher

    gone = tmp_path / "DSCF0857.RAF"   # never created → does not exist
    def _raise_enoent(p):
        raise FileNotFoundError(2, "No such file or directory", str(gone))
    monkeypatch.setattr(file_watcher, "analyze_photo_quality", _raise_enoent)

    errors = []
    file_watcher._analyze_and_store(gone, tmp_db, on_error=errors.append)
    assert errors == []   # no toast for a vanished file

    # And no 'error' row was written for it.
    from backend.database import get_db
    with get_db(tmp_db) as conn:
        row = conn.execute(
            "SELECT analysis_status FROM images WHERE file_path = ?",
            (str(gone),)).fetchone()
    assert row is None


def test_real_failure_still_errors_and_toasts(monkeypatch, tmp_db, tmp_path):
    """A genuine analysis failure on a file that EXISTS still surfaces an error
    (we only suppress the vanished-file race, not real corruption)."""
    from backend import file_watcher

    bad = tmp_path / "corrupt.RAF"
    bad.write_bytes(b"not a real raw")   # exists, but unanalyzable
    def _raise_value(p):
        raise ValueError("Could not load image: corrupt")
    monkeypatch.setattr(file_watcher, "analyze_photo_quality", _raise_value)

    errors = []
    file_watcher._analyze_and_store(bad, tmp_db, on_error=errors.append)
    assert len(errors) == 1 and "corrupt.RAF" in errors[0]["file"]
