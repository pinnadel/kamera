"""
Root-level conftest.py — adds the project root to sys.path so that
`backend`, `phase1_technical`, and `phase3_learning` are importable
without an editable install.
"""
import sys
from pathlib import Path

# Insert the project root at the front of sys.path once, before any
# test module is collected. pytest auto-loads this file first.
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
