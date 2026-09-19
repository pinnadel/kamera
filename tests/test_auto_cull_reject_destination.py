"""
The auto-cull preview must tell the truth about where rejects land.

`reject_to_system_trash` routes every reject through send2trash — auto-cull's
included, because auto-cull commits through the same sink as a manual R press
(backend/sources/folder.py). Before this, the modal said "Files move
immediately" in both cases, so one click could send N files to the OS Trash
under a promise that they were being sorted into a subfolder.

The copy is only as honest as the field it renders, so these tests pin the
field: the mode must follow the setting, the label must follow the folder's
own override, and `undoable` must be False exactly when the file leaves the
folder (which is when U can no longer address it).
"""
from __future__ import annotations

import pytest

import backend.routers.decisions as decisions_mod


@pytest.fixture()
def reject_setting(monkeypatch):
    """Drive _reject_to_system_trash_enabled without touching the real DB."""
    state = {"on": False}
    monkeypatch.setattr(
        decisions_mod, "_reject_to_system_trash_enabled", lambda: state["on"]
    )
    return state


def test_folder_route_names_the_subfolder(reject_setting, monkeypatch):
    monkeypatch.setattr(decisions_mod, "get_folder_overrides", lambda _f: {})

    dest = decisions_mod._reject_destination("/tmp/shoot")

    assert dest["mode"] == "folder"
    assert dest["label"] == "_Rejects"
    assert dest["undoable"] is True


def test_folder_route_honours_a_per_folder_override(reject_setting, monkeypatch):
    # A tab pointed at its own rejects directory must not be described with
    # the default name — the user would look in the wrong place.
    monkeypatch.setattr(
        decisions_mod,
        "get_folder_overrides",
        lambda _f: {"rejects_folder": "/tmp/elsewhere/Discards"},
    )

    dest = decisions_mod._reject_destination("/tmp/shoot")

    assert dest["mode"] == "folder"
    assert dest["label"] == "Discards"
    assert dest["undoable"] is True


def test_trash_setting_flips_mode_and_kills_undo(reject_setting, monkeypatch):
    monkeypatch.setattr(decisions_mod, "get_folder_overrides", lambda _f: {})
    reject_setting["on"] = True

    dest = decisions_mod._reject_destination("/tmp/shoot")

    assert dest["mode"] == "trash"
    assert "Trash" in dest["label"]
    # The whole point: send2trash makes the file unaddressable, so the in-app
    # undo promise must not be made.
    assert dest["undoable"] is False


def test_trash_setting_wins_over_a_folder_override(reject_setting, monkeypatch):
    # An override names a folder, but the Trash route never consults it. The
    # description must follow what actually happens, not what is configured.
    monkeypatch.setattr(
        decisions_mod,
        "get_folder_overrides",
        lambda _f: {"rejects_folder": "/tmp/elsewhere/Discards"},
    )
    reject_setting["on"] = True

    assert decisions_mod._reject_destination("/tmp/shoot")["mode"] == "trash"


def test_unscoped_sweep_does_not_claim_one_path(reject_setting, monkeypatch):
    # With no source_folder the preview spans every folder tab, whose
    # overrides may differ. Naming a single resolved path would be a guess.
    monkeypatch.setattr(decisions_mod, "get_folder_overrides", lambda _f: {})

    dest = decisions_mod._reject_destination(None)

    assert dest["mode"] == "folder"
    assert dest["label"] == "each folder's _Rejects"
