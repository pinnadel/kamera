"""FolderWatcher live-progress counters + decision-folder skip guard.

These cover the watch-progress signal (GET /watch-progress) and the fix for the
"Could not load image" toast: files moved into _Keeps/_Maybes/_Trash must never
be (re)analyzed, so an external archiver moving a keeper out of _Keeps can't
race the watcher. No real photos / cv2 needed — we assert routing + counters.
"""
import threading
from pathlib import Path

from backend.file_watcher import FolderWatcher, _PhotoEventHandler


def test_counters_start_at_zero_and_cycle():
    w = FolderWatcher(Path("/tmp/none.db"))
    assert w.in_flight == 0 and w.analyzed == 0
    w._enter_inflight()
    assert w.in_flight == 1 and w.analyzed == 0
    w._exit_inflight(analyzed=True)
    assert w.in_flight == 0 and w.analyzed == 1


def test_exit_without_analyze_does_not_bump_analyzed():
    w = FolderWatcher(Path("/tmp/none.db"))
    w._enter_inflight()
    w._exit_inflight(analyzed=False)   # e.g. file vanished during settle
    assert w.in_flight == 0 and w.analyzed == 0


def _spy_threads(monkeypatch):
    spawned = []
    real = threading.Thread

    class _Spy(real):
        def start(self):  # don't actually run analysis in the test
            spawned.append(self._args)

    monkeypatch.setattr(threading, "Thread", _Spy)
    return spawned


def test_handle_skips_decision_subfolder_files(monkeypatch):
    spawned = _spy_threads(monkeypatch)
    h = _PhotoEventHandler(Path("/tmp/x.db"))
    h._handle("/card/folder/_Keeps/DSCF0856.JPG")   # decided → skip
    h._handle("/card/folder/_Maybes/DSCF0857.RAF")  # decided → skip
    h._handle("/card/folder/_Trash/DSCF0858.JPG")   # decided → skip
    assert spawned == []   # nothing analyzed


def test_handle_analyzes_root_files(monkeypatch):
    spawned = _spy_threads(monkeypatch)
    h = _PhotoEventHandler(Path("/tmp/x.db"))
    h._handle("/card/folder/DSCF0900.JPG")          # fresh import → analyze
    assert len(spawned) == 1


def test_handle_ignores_unsupported_extensions(monkeypatch):
    spawned = _spy_threads(monkeypatch)
    h = _PhotoEventHandler(Path("/tmp/x.db"))
    h._handle("/card/folder/notes.txt")
    assert spawned == []
