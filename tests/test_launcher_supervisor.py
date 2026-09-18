"""
Launcher supervisor: when may it tear the backend down?

Regression guard for 2026-09-18. `_watch_pwa_macos` used to sleep 2s and then
terminate the backend if `pgrep` found no PWA window. That is only safe when
the launcher opened the window itself. Under `--no-browser` — the mode the
macOS .app bundle uses — the AppleScript opens the PWA only *after* /health
answers, which is the same instant the launcher enters the supervisor. Chrome
needs longer than 2s to produce app_mode_loader, so the backend was terminated
seconds after startup, and the window then loaded against a dead server: every
request failed with "failed to fetch", with no trace in any log.
"""
from __future__ import annotations

import threading

import pytest

import launcher


class FakeProc:
    def __init__(self) -> None:
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch: pytest.MonkeyPatch):
    """Keep the loops sub-second so the tests stay fast."""
    monkeypatch.setattr(launcher, "PWA_POLL_SECONDS", 0.01)
    monkeypatch.setattr(launcher, "PWA_APPEAR_TIMEOUT", 0.3)


def _run_supervisor(proc: FakeProc) -> threading.Thread:
    t = threading.Thread(
        target=launcher._watch_pwa_macos, args=([proc], "http://localhost:8000"),
        daemon=True,
    )
    t.start()
    return t


def test_survives_a_window_that_opens_late(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact production bug: the window shows up after the supervisor has
    already started looking for it."""
    polls = {"n": 0}

    def _pwa():
        polls["n"] += 1
        return polls["n"] > 5      # absent for the first 5 checks, then open

    monkeypatch.setattr(launcher, "_pwa_is_running", _pwa)
    proc = FakeProc()
    t = _run_supervisor(proc)
    t.join(timeout=1.0)

    assert not proc.terminated, "backend killed while the window was still opening"


def test_terminates_once_a_seen_window_goes_away(monkeypatch: pytest.MonkeyPatch) -> None:
    polls = {"n": 0}

    def _pwa():
        polls["n"] += 1
        return polls["n"] <= 5     # open for the first 5 checks, then quit

    monkeypatch.setattr(launcher, "_pwa_is_running", _pwa)
    proc = FakeProc()
    t = _run_supervisor(proc)
    t.join(timeout=2.0)

    assert proc.terminated, "backend outlived the window the user closed"


def test_never_terminates_when_no_window_ever_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provenance drives the API with --no-browser and opens no window. A
    lingering backend is recoverable; one killed under a live window is not."""
    monkeypatch.setattr(launcher, "_pwa_is_running", lambda: False)
    proc = FakeProc()
    t = _run_supervisor(proc)
    t.join(timeout=1.0)

    assert t.is_alive(), "supervisor should keep waiting, not fall through"
    assert not proc.terminated


def test_health_wait_is_not_shorter_than_the_applescript_loop() -> None:
    """The .app bundle polls /health 120 times at 1s. If this function gives up
    first it terminates uvicorn, and the AppleScript's patience never pays off."""
    import inspect

    default = inspect.signature(launcher._wait_for_backend).parameters["timeout"].default
    assert default >= 120
