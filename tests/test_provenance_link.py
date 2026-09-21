"""KaMeRa's read-only side of the Provenance seam.

No Provenance install is needed: the ledger is another process's SQLite file
and the status file is JSON, so both are trivially constructible here. That is
the point of the seam — neither app imports the other.
"""

import json
import sqlite3

import pytest

from backend import provenance_link

# Provenance's schema (src/provenance/ledger.py), trimmed to what we read.
_DDL = """
CREATE TABLE imports (
    id                 INTEGER PRIMARY KEY,
    exif_key           TEXT UNIQUE NOT NULL,
    original_filename  TEXT,
    temp_path          TEXT,
    archived_path      TEXT,
    status             TEXT DEFAULT 'imported',
    backed_up          INTEGER DEFAULT 0
);
"""


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setenv("KAMERA_PROVENANCE_DIR", str(tmp_path))
    provenance_link.reset_cache()
    yield tmp_path
    provenance_link.reset_cache()


def _ledger(shared, rows=()):
    conn = sqlite3.connect(shared / "ledger.sqlite")
    conn.executescript(_DDL)
    for r in rows:
        conn.execute(
            "INSERT INTO imports (exif_key, temp_path, archived_path, status, "
            "backed_up) VALUES (?,?,?,?,?)", r)
    conn.commit()
    conn.close()


# --- Absence is a normal state, not an error --------------------------------

def test_unavailable_without_a_ledger(shared):
    assert provenance_link.is_available() is False
    assert provenance_link.key_for_path("/any/file.RAF") is None
    assert provenance_link.archive_info("k") is None


def test_status_missing_reads_as_none(shared):
    assert provenance_link.import_status() is None


def test_status_corrupt_reads_as_none(shared):
    (shared / "import_status.json").write_text("{half-written")
    assert provenance_link.import_status() is None


def test_status_non_dict_reads_as_none(shared):
    (shared / "import_status.json").write_text(json.dumps(["nope"]))
    assert provenance_link.import_status() is None


def test_status_round_trip(shared):
    (shared / "import_status.json").write_text(json.dumps(
        {"run_id": "abc", "phase": "copying", "photos_done": 7,
         "photos_total": 40, "volume": "Untitled"}))
    st = provenance_link.import_status()
    assert st["phase"] == "copying" and st["photos_done"] == 7


# --- The key lookup ---------------------------------------------------------

def test_key_for_staged_path(shared):
    _ledger(shared, [("SER|2026:01:01 10:00:00.12|41|1234",
                      "/tmp/temp_import/A.RAF", None, "imported", 0)])
    assert provenance_link.key_for_path("/tmp/temp_import/A.RAF") == \
        "SER|2026:01:01 10:00:00.12|41|1234"


def test_key_is_none_once_the_file_has_moved(shared):
    """The join is on the staged path, so a decision that moves the photo into
    _Keeps breaks it. That is exactly why the key is stamped at ingest."""
    _ledger(shared, [("K1", "/tmp/temp_import/A.RAF", None, "imported", 0)])
    assert provenance_link.key_for_path("/tmp/temp_import/_Keeps/A.RAF") is None


def test_key_for_unknown_path_is_none(shared):
    _ledger(shared, [("K1", "/tmp/temp_import/A.RAF", None, "imported", 0)])
    assert provenance_link.key_for_path("/elsewhere/B.JPG") is None


def test_cache_refreshes_when_the_ledger_changes(shared):
    """An import writes to the ledger constantly while a batch analyses, so a
    snapshot that never invalidates would miss every photo after the first."""
    _ledger(shared, [("K1", "/tmp/A.RAF", None, "imported", 0)])
    assert provenance_link.key_for_path("/tmp/A.RAF") == "K1"
    assert provenance_link.key_for_path("/tmp/B.RAF") is None

    conn = sqlite3.connect(shared / "ledger.sqlite")
    conn.execute("INSERT INTO imports (exif_key, temp_path) VALUES ('K2','/tmp/B.RAF')")
    conn.commit()
    conn.close()
    assert provenance_link.key_for_path("/tmp/B.RAF") == "K2"


def test_reader_never_writes_to_the_ledger(shared):
    """Opened mode=ro — a bug here must not be able to corrupt another app's
    database."""
    _ledger(shared, [("K1", "/tmp/A.RAF", None, "imported", 0)])
    conn = provenance_link._connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO imports (exif_key) VALUES ('X')")
    conn.close()


# --- Where a photo ended up -------------------------------------------------

def test_archive_info(shared):
    _ledger(shared, [("K1", "/tmp/A.RAF",
                      "/Users/p/Pictures/Luminar Neo/2026/2026 Bilder/A.RAF",
                      "archived", 1)])
    info = provenance_link.archive_info("K1")
    assert info["status"] == "archived" and info["backed_up"] is True
    assert info["archived_path"].endswith("2026 Bilder/A.RAF")


def test_archive_info_unknown_key(shared):
    _ledger(shared, [("K1", "/tmp/A.RAF", None, "imported", 0)])
    assert provenance_link.archive_info("nope") is None


def test_archive_info_before_archiving(shared):
    _ledger(shared, [("K1", "/tmp/A.RAF", None, "imported", 0)])
    info = provenance_link.archive_info("K1")
    assert info["archived_path"] is None and info["backed_up"] is False


def test_a_first_run_is_visible_before_the_ledger_exists(shared):
    """Provenance writes the "reading card…" status before it creates the
    ledger, so on a first-ever run the two artifacts appear in that order.
    Gating availability on the ledger alone would report "not installed"
    while an import is visibly running."""
    (shared / "import_status.json").write_text(json.dumps(
        {"run_id": "r1", "phase": "reading", "volume": "Untitled"}))
    assert provenance_link.is_available() is True
    assert provenance_link.import_status()["phase"] == "reading"
    # Ledger reads still correctly report nothing.
    assert provenance_link.key_for_path("/tmp/A.RAF") is None
    assert provenance_link.archive_info("K1") is None
