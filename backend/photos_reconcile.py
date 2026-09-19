"""
Diffing a Photos album against KaMeRa's decisions, and applying the fixes.

## Why this exists

Decisions flow one way: KaMeRa writes `KaMeRa:Keep|Maybe|Reject` keywords and
never reads them back. Photos is a live app the user also edits by hand, so the
two drift — and the drift is silent. On 2026-09-18 three keywords were deleted
in Photos and KaMeRa never noticed; two photos kept a `keep` decision with no
marker behind it, which is what sent a debugging session chasing a bug that did
not exist.

## Why the classification is a pure function

`classify()` takes plain data — DB rows, a uuid→keywords map, the library's
uuid set — and returns the diff. It touches no library and no database, so the
interesting half of this feature is testable on a machine with no Photos at
all, which is the half where a wrong call silently drops a row.

## Why "authoritative" is not a setting

Photos and KaMeRa disagree for good reasons in both directions: a keyword
edited in Photos is a real decision, and so is one made in KaMeRa. Picking a
winner up front would be wrong half the time, so the diff is shown and the
direction is chosen per class. Only `keyword_missing` has a safe default —
there the two sides do not actually disagree, one simply never arrived.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.sources.photos import ALL_DECISION_KEYWORDS, DECISION_KEYWORDS

# keyword → decision, the inverse of DECISION_KEYWORDS.
KEYWORD_DECISIONS: dict[str, str] = {v: k for k, v in DECISION_KEYWORDS.items()}

# The mismatch classes, in the order the UI shows them: safest fix first.
CLASSES: tuple[str, ...] = (
    "keyword_missing",   # decided here, no marker there — the write never landed
    "keyword_foreign",   # marker disagrees with the decision — a real conflict
    "decision_missing",  # marker there, nothing here — decided in Photos
    "not_in_album",      # asset left the album
    "asset_gone",        # asset no longer exists in the library
)

# What may be done about each class. The first entry is the default, and only
# keyword_missing defaults to anything other than leaving it alone.
ACTIONS: dict[str, tuple[str, ...]] = {
    "keyword_missing":  ("apply", "clear", "skip"),
    "keyword_foreign":  ("skip", "kamera", "photos"),
    "decision_missing": ("skip", "import", "clear"),
    "not_in_album":     ("skip", "drop"),
    "asset_gone":       ("skip", "drop"),
}

DEFAULT_ACTIONS: dict[str, str] = {name: opts[0] for name, opts in ACTIONS.items()}


@dataclass
class Diff:
    """One album's drift, grouped by what should be done about it."""

    checked: int = 0
    in_sync: int = 0
    classes: dict[str, list[dict]] = field(
        default_factory=lambda: {name: [] for name in CLASSES}
    )
    # Assets carrying a KaMeRa keyword that this tab has no images row for —
    # analysed elsewhere, or marked in Photos before KaMeRa ever saw them.
    # Reported as a number, not a fixable class: there is no row to reconcile,
    # and inventing one would mean claiming an analysis that never ran.
    unmanaged_keyword: int = 0

    @property
    def total_drift(self) -> int:
        return sum(len(v) for v in self.classes.values())

    def as_dict(self) -> dict:
        return {
            "checked": self.checked,
            "in_sync": self.in_sync,
            "drift": self.total_drift,
            "classes": self.classes,
            "unmanaged_keyword": self.unmanaged_keyword,
        }


def marker_of(keywords: list[str]) -> str | None:
    """The KaMeRa keyword on this asset, or None.

    More than one is possible — a hand-edited library can carry both Keep and
    Reject. We report the first in canonical order rather than picking
    arbitrarily, and such an asset lands in keyword_foreign regardless (it
    cannot agree with a single decision), so the ambiguity is surfaced rather
    than resolved behind the user's back.
    """
    present = [k for k in DECISION_KEYWORDS.values() if k in keywords]
    return present[0] if present else None


def has_multiple_markers(keywords: list[str]) -> bool:
    return sum(1 for k in ALL_DECISION_KEYWORDS if k in keywords) > 1


def classify(
    rows: list[dict],
    in_album: dict[str, list[str]],
    library_uuids: set[str],
) -> Diff:
    """Compare KaMeRa's rows against the library's actual keywords.

    `rows` are this tab's images joined to their decision:
    id, filename, photos_uuid, decision (None when undecided).
    `in_album` maps uuid → keywords for every asset currently in the album.
    `library_uuids` is every uuid in the library.
    """
    diff = Diff()
    seen_uuids: set[str] = set()

    for row in rows:
        diff.checked += 1
        uuid = row.get("photos_uuid")
        decision = row.get("decision")
        entry = {
            "image_id": row.get("id"),
            "filename": row.get("filename"),
            "photos_uuid": uuid,
            "decision": decision,
        }

        # A row with no uuid cannot be matched to anything. Treat it as gone
        # rather than silently in-sync: it is unusable for write-back either way.
        if not uuid:
            diff.classes["asset_gone"].append({**entry, "keyword": None})
            continue

        seen_uuids.add(uuid)

        if uuid not in in_album:
            # Order matters: "not in the library at all" is the stronger fact.
            if uuid not in library_uuids:
                diff.classes["asset_gone"].append({**entry, "keyword": None})
            else:
                diff.classes["not_in_album"].append({**entry, "keyword": None})
            continue

        keywords = in_album[uuid]
        marker = marker_of(keywords)
        entry["keyword"] = marker
        entry["ambiguous"] = has_multiple_markers(keywords)

        if decision is None:
            if marker is None:
                diff.in_sync += 1            # undecided here, unmarked there
            else:
                diff.classes["decision_missing"].append(entry)
            continue

        expected = DECISION_KEYWORDS.get(decision)
        if marker is None:
            diff.classes["keyword_missing"].append(entry)
        elif marker == expected and not entry["ambiguous"]:
            diff.in_sync += 1
        else:
            diff.classes["keyword_foreign"].append(entry)

    # Album assets carrying a marker that no row of ours accounts for.
    for uuid, keywords in in_album.items():
        if uuid in seen_uuids:
            continue
        if marker_of(keywords) is not None:
            diff.unmanaged_keyword += 1

    return diff


def validate_actions(actions: dict[str, str]) -> dict[str, str]:
    """Fill in defaults and reject anything that is not a known action.

    Unknown class names and unknown actions are errors rather than ignored:
    this endpoint deletes rows and rewrites keywords, so a typo must not
    quietly resolve to "do the default thing to everything".
    """
    resolved = dict(DEFAULT_ACTIONS)
    for name, action in (actions or {}).items():
        if name not in ACTIONS:
            raise ValueError(f"Unknown mismatch class {name!r}")
        if action not in ACTIONS[name]:
            raise ValueError(
                f"Action {action!r} is not valid for {name!r} "
                f"(expected one of {', '.join(ACTIONS[name])})"
            )
        resolved[name] = action
    return resolved
