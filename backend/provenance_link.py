"""KaMeRa's side of the Provenance seam — read-only, no HTTP, no imports.

Provenance (`~/dev/provenance`) is the workflow harness around this app: it
ingests cards, keeps a dedup ledger, and archives released keepers into the
Luminar library. Until now the relationship was one-way — Provenance drives
KaMeRa over HTTP and KaMeRa knew nothing about Provenance, so a photo simply
vanished when it was archived and an import was invisible while it ran.

This module is the return channel, and it is deliberately the ONLY place
KaMeRa touches anything of Provenance's. Two read-only sources, both in the
`~/Pictures/.kamera/` directory the two apps already share:

  - `ledger.sqlite`        durable facts: the stable per-photo key, where a
                           file was archived to, whether Time Machine has it
  - `import_status.json`   the run in flight: phase and per-photo counts

**Read-only on purpose, and a file rather than a port.** The one thing KaMeRa
asks Provenance to do — "archive this now" — is already expressed by moving a
file into `_Keeps_reviewed`, and that contract works. Nothing here justifies a
server, a bound port or a second lifecycle to supervise.

Everything degrades to None. Provenance is optional: a machine without it must
lose these annotations and nothing else, exactly as `photos_library` is gated
off a non-macOS host.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

# Provenance's spec default, overridable for tests and for anyone who moved it.
_DEFAULT_DIR = Path.home() / "Pictures" / ".kamera"


def shared_dir() -> Path:
    return Path(os.environ.get("KAMERA_PROVENANCE_DIR") or _DEFAULT_DIR)


def ledger_path() -> Path:
    return shared_dir() / "ledger.sqlite"


def status_path() -> Path:
    return shared_dir() / "import_status.json"


def _has_ledger() -> bool:
    try:
        return ledger_path().is_file()
    except OSError:
        return False


def is_available() -> bool:
    """True when this machine has Provenance at all.

    Either artifact counts, and the status file has to: on a first-ever run
    Provenance writes the "reading card…" status BEFORE the ledger is created,
    so gating on the ledger alone would hide a live import behind
    "not installed". The ledger reads below gate on the ledger separately.
    """
    if _has_ledger():
        return True
    try:
        return status_path().is_file()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Live import status
# ---------------------------------------------------------------------------

def import_status() -> dict | None:
    """The in-flight (or last) import run, or None.

    The file is replaced atomically by the writer, so a torn read is not
    possible — but a missing or malformed file still has to read as "nothing
    is happening" rather than raise into a request handler.
    """
    try:
        with status_path().open() as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Ledger reads
# ---------------------------------------------------------------------------
#
# The ledger is another process's database. We open it read-only (`mode=ro`)
# so a bug here can never write to it, and we never hold the connection: an
# import may be writing, and a long-lived reader would be in its way.

def _connect() -> sqlite3.Connection | None:
    # Specifically the ledger — is_available() is the broader "Provenance
    # exists" question and is true during a first run that has no ledger yet.
    if not _has_ledger():
        return None
    try:
        conn = sqlite3.connect(f"file:{ledger_path()}?mode=ro", uri=True,
                               timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


# `temp_path` carries no index in Provenance's schema, and both lookups below
# run once per photo during a batch or a grid load. One cached snapshot per
# ledger revision turns N scans into one; the (mtime, size) stamp invalidates
# it whenever Provenance writes, which during an import is constantly.
_cache_lock = threading.Lock()
_cache_stamp: tuple[float, int] | None = None
_cache_by_path: dict[str, str] = {}
_cache_by_key: dict[str, dict] = {}


def _snapshot() -> tuple[dict[str, str], dict[str, dict]]:
    """({staged path: key}, {key: {archived_path, backed_up, status}}).

    Both maps come from one pass, because every caller needs one or the other
    and the expensive part is the scan, not the dict.
    """
    global _cache_stamp, _cache_by_path, _cache_by_key
    try:
        st = ledger_path().stat()
        stamp = (st.st_mtime, st.st_size)
    except OSError:
        return {}, {}

    with _cache_lock:
        if stamp == _cache_stamp:
            return _cache_by_path, _cache_by_key

    conn = _connect()
    if conn is None:
        return {}, {}
    try:
        rows = conn.execute(
            "SELECT temp_path, exif_key, archived_path, backed_up, status "
            "FROM imports"
        ).fetchall()
    except sqlite3.Error:
        return {}, {}
    finally:
        conn.close()

    by_path: dict[str, str] = {}
    by_key: dict[str, dict] = {}
    for r in rows:
        key = r["exif_key"]
        if not key:
            continue
        if r["temp_path"]:
            by_path[r["temp_path"]] = key
        by_key[key] = {"archived_path": r["archived_path"],
                       "backed_up": bool(r["backed_up"]),
                       "status": r["status"]}

    with _cache_lock:
        _cache_stamp, _cache_by_path, _cache_by_key = stamp, by_path, by_key
    return by_path, by_key


def key_for_path(path: str | Path) -> str | None:
    """Provenance's stable per-photo key for a file, or None.

    **Call this at ingest, before any decision moves the file.** The join is on
    the staged path, and a K/M/R moves the photo into `_Keeps`/`_Maybes`/
    `_Rejects` — after which this returns None forever. None is a normal
    answer: a photo that never came through Provenance has no key and needs
    none.
    """
    if not _has_ledger():
        return None
    return _snapshot()[0].get(str(path))


def archive_info_many(keys) -> dict[str, dict]:
    """Batched `archive_info` — {key: info} for the keys that have a row.

    `GET /images` annotates every photo, so the per-key version would open one
    connection per photo. This reads the cached snapshot instead.
    """
    if not _has_ledger():
        return {}
    by_key = _snapshot()[1]
    return {k: by_key[k] for k in keys if k in by_key}


def lifecycle_for(key: str | None, decision: str | None) -> str | None:
    """Where a photo is in its journey out of KaMeRa, or None.

    Keep-only by design: a maybe or a reject never leaves for the library, so
    it has no lifecycle to report. `staged` is the state the old completion
    dialog claimed was `archived` — the keeper sits in `_Keeps` and nothing has
    reached the library yet.
    """
    return _lifecycle(key, decision, archive_info_many([key]) if key else {})


def _lifecycle(key: str | None, decision: str | None,
               infos: dict[str, dict]) -> str | None:
    if not key or decision != "keep":
        return None
    info = infos.get(key)
    if info is None or info.get("status") != "archived":
        return "staged"
    return "backed_up" if info.get("backed_up") else "archived"


def annotate_lifecycle(rows: list[dict]) -> None:
    """Stamp `lifecycle` (and `archived_path`) onto /images rows, in place.

    One ledger snapshot for the whole page. Rows without a key — anything that
    never came through Provenance — get None and are unaffected.
    """
    keys = {r.get("provenance_key") for r in rows if r.get("provenance_key")}
    infos = archive_info_many(keys) if keys else {}
    for r in rows:
        key = r.get("provenance_key")
        r["lifecycle"] = _lifecycle(key, r.get("decision"), infos)
        info = infos.get(key) if key else None
        r["archived_path"] = info.get("archived_path") if info else None


def archive_info(key: str) -> dict | None:
    """Where a photo ended up, by its key: {archived_path, backed_up, status}.

    This is what lets an archived photo keep a row in KaMeRa instead of
    404-ing into deletion — the file is gone from the working folder, but it
    is not gone.
    """
    if not key:
        return None
    conn = _connect()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT archived_path, backed_up, status FROM imports "
            "WHERE exif_key = ? LIMIT 1", (key,)
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return {"archived_path": row["archived_path"],
            "backed_up": bool(row["backed_up"]),
            "status": row["status"]}


def reset_cache() -> None:
    """Tests, and anything that moves the shared directory at runtime."""
    global _cache_stamp, _cache_by_path, _cache_by_key
    with _cache_lock:
        _cache_stamp, _cache_by_path, _cache_by_key = None, {}, {}
