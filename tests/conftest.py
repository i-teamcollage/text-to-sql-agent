import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "data"))

import generate  # noqa: E402


@pytest.fixture(scope="session")
def warehouse_db(tmp_path_factory) -> Path:
    """The made-up warehouse, built once per test session (a few seconds)."""
    path = tmp_path_factory.mktemp("warehouse") / "warehouse.duckdb"
    generate.build_database(path)
    return path
