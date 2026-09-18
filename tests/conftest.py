"""
Shared pytest fixtures for the KaMeRa test suite.

Key design decisions:
  - `tmp_db` creates a fresh, isolated SQLite database in pytest's tmp_path
    so tests never touch the real data/pca.db or each other's data.
  - The fixture is function-scoped (default) so each test gets a clean slate.
  - We import create_tables directly — never import from backend/main.py,
    which triggers model loads and server-side effects on import.
"""
import pytest
from pathlib import Path

from backend.database import create_tables


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """
    Return the Path to a freshly initialised SQLite database in a temp directory.

    Schema is applied via create_tables() so the DB is identical to what the
    real app creates on first launch, including all migrations.
    """
    db_path = tmp_path / "test_pca.db"
    create_tables(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Live Apple Photos tests
# ---------------------------------------------------------------------------

#: Only this library may be written to by tests. The name check — not merely
#: "is a library open" — is the interlock: without it, running `pytest` on a
#: normal day would write KaMeRa keywords into the real library, and the user
#: would have to hunt them down afterwards.
PHOTOS_TEST_LIBRARY_NAME = "KaMeRa-Test"


@pytest.fixture(scope="session")
def photos_test_library() -> str:
    """Path to the test library, or skip.

    Setup instructions live in docs/PLAN-photos-source.md.
    """
    from backend import photos_library

    if not photos_library.is_available():
        pytest.skip("Apple Photos support unavailable on this system")

    current = photos_library.current_library_path()
    if not current:
        pytest.skip("No Photos library is open")
    if Path(current).stem != PHOTOS_TEST_LIBRARY_NAME:
        pytest.skip(
            f"Photos has {Path(current).stem!r} open, not "
            f"{PHOTOS_TEST_LIBRARY_NAME!r} — refusing to write to a real library"
        )
    return current
