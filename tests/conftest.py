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
