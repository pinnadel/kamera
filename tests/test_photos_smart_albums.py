"""
Smart Albums are stored queries, not stored lists.

Photos keeps no album↔asset rows for a Smart Album (verified 2026-09-19: zero
rows for one holding 2114 photos) and evaluates the predicate live, which is
why osxphotos does not expose them and why membership has to come from Photos
over AppleScript.

That makes two things worth pinning without a real library: the UUID parsing
(Photos returns `<uuid>/L0/001`, KaMeRa keys on the bare UUID) and the
addressing (by id, never by name — album names are not unique and
`album "Foo"` silently picks the first match, which is a wrong read that looks
like a right one).
"""
from __future__ import annotations

import pytest

import backend.photos_library as pl


# ── parsing what Photos returns ─────────────────────────────────────────────

def test_member_uuids_are_stripped_of_the_version_suffix(monkeypatch) -> None:
    monkeypatch.setattr(
        pl, "_osascript",
        lambda _s, timeout=120: "AAA-1/L0/001, BBB-2/L0/001, CCC-3/L0/001",
    )
    assert pl.smart_album_member_uuids("ALB") == ["AAA-1", "BBB-2", "CCC-3"]


def test_an_empty_smart_album_is_an_empty_list_not_an_error(monkeypatch) -> None:
    monkeypatch.setattr(pl, "_osascript", lambda _s, timeout=120: "\n")
    assert pl.smart_album_member_uuids("ALB") == []


def test_member_lookup_addresses_the_album_by_id(monkeypatch) -> None:
    """Never by name. Duplicate album names resolve to whichever is first."""
    seen = {}

    def _capture(script, timeout=120):
        seen["script"] = script
        return ""

    monkeypatch.setattr(pl, "_osascript", _capture)
    pl.smart_album_member_uuids("THE-UUID")

    assert 'album id "THE-UUID"' in seen["script"]
    assert 'album "' not in seen["script"], "must not address the album by name"


# ── counts are best-effort ──────────────────────────────────────────────────

def test_counts_are_parsed_per_album(monkeypatch) -> None:
    monkeypatch.setattr(pl, "_osascript", lambda _s, timeout=60: "A=12\nB=0\n")
    assert pl.smart_album_counts(["A", "B"]) == {"A": 12, "B": 0}


def test_counts_are_batched_into_one_call(monkeypatch) -> None:
    """One process spawn, not one per album — the picker waits on this."""
    calls = []

    def _count(script, timeout=60):
        calls.append(script)
        return "A=1\nB=2\nC=3\n"

    monkeypatch.setattr(pl, "_osascript", _count)
    pl.smart_album_counts(["A", "B", "C"])

    assert len(calls) == 1
    for uuid in ("A", "B", "C"):
        assert f'album id "{uuid}"' in calls[0]


def test_a_closed_photos_app_costs_the_counts_not_the_picker(monkeypatch) -> None:
    """The album picker must still open when Photos is not running."""
    def _boom(_s, timeout=60):
        raise pl.SmartAlbumUnavailable("Photos is not running")

    monkeypatch.setattr(pl, "_osascript", _boom)
    assert pl.smart_album_counts(["A"]) == {}


def test_no_smart_albums_means_no_applescript_at_all(monkeypatch) -> None:
    def _never(*_a, **_k):
        raise AssertionError("osascript must not run when there is nothing to count")

    monkeypatch.setattr(pl, "_osascript", _never)
    assert pl.smart_album_counts([]) == {}


def test_unparseable_count_lines_are_skipped(monkeypatch) -> None:
    monkeypatch.setattr(pl, "_osascript", lambda _s, timeout=60: "A=12\ngarbage\nB=x\n")
    assert pl.smart_album_counts(["A", "B"]) == {"A": 12}


# ── which albums count as "the user made this" ──────────────────────────────

class _FakeDB:
    def __init__(self, details):
        self._dbalbum_details = details


def test_only_titled_untrashed_smart_albums_are_offered(monkeypatch) -> None:
    """Apple's built-ins share the kind range but carry no stored title.

    Photos localises "Favourites", "Recents", "Screenshots" at runtime, so a
    missing title is the marker for "the app ships this", not "the user made
    this". Trashed ones are gone.
    """
    details = {
        "user":     {"kind": pl._SMART_ALBUM_KIND, "title": "2026",  "intrash": False},
        "builtin":  {"kind": pl._SMART_ALBUM_KIND, "title": None,     "intrash": False},
        "deleted":  {"kind": pl._SMART_ALBUM_KIND, "title": "Old",    "intrash": True},
        "regular":  {"kind": 2,                    "title": "Greece", "intrash": False},
        "folder":   {"kind": 4000,                 "title": "Prints", "intrash": False},
    }
    monkeypatch.setattr(pl, "_db_for", lambda _p, refresh=False: _FakeDB(details))

    assert pl.smart_albums("/lib") == {"user": "2026"}


# ── resolution falls through in the right order ─────────────────────────────

def test_an_unknown_uuid_is_a_keyerror_not_an_applescript_call(monkeypatch) -> None:
    monkeypatch.setattr(pl, "smart_albums", lambda _p: {})

    def _never(*_a, **_k):
        raise AssertionError("must not ask Photos about an album it has never heard of")

    monkeypatch.setattr(pl, "smart_album_member_uuids", _never)

    class _DB:
        album_info: list = []

    with pytest.raises(KeyError):
        pl._album_photos(_DB(), "/lib", "nope")


def test_photos_order_is_preserved_not_lookup_order(monkeypatch) -> None:
    """The grid's initial order should match what the album shows."""
    monkeypatch.setattr(pl, "smart_albums", lambda _p: {"ALB": "Smart"})
    monkeypatch.setattr(pl, "smart_album_member_uuids", lambda _u: ["c", "a", "b"])

    class _Photo:
        def __init__(self, uuid): self.uuid = uuid

    class _DB:
        album_info: list = []
        def photos(self, uuid=None, movies=False):
            # Deliberately the wrong order, and one extra the album lost.
            return [_Photo("a"), _Photo("b"), _Photo("c")]

    got = pl._album_photos(_DB(), "/lib", "ALB")
    assert [p.uuid for p in got] == ["c", "a", "b"]


def test_a_member_missing_from_the_catalogue_is_dropped(monkeypatch) -> None:
    monkeypatch.setattr(pl, "smart_albums", lambda _p: {"ALB": "Smart"})
    monkeypatch.setattr(pl, "smart_album_member_uuids", lambda _u: ["a", "ghost"])

    class _Photo:
        def __init__(self, uuid): self.uuid = uuid

    class _DB:
        album_info: list = []
        def photos(self, uuid=None, movies=False):
            return [_Photo("a")]

    assert [p.uuid for p in pl._album_photos(_DB(), "/lib", "ALB")] == ["a"]
