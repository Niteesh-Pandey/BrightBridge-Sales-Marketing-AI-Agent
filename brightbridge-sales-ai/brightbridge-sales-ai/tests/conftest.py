import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database  # noqa: E402
from database.seed import seed_database  # noqa: E402
from scripts.generate_demo_data import generate  # noqa: E402

TODAY = date(2026, 10, 1)


@pytest.fixture()
def empty_db(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    db.initialize()
    return db


@pytest.fixture()
def demo_db(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'demo.db').as_posix()}")
    db.initialize()
    seed_database(db, generate(60, 200, 110, 600, 8, seed=7, today=TODAY))
    return db


@pytest.fixture()
def today():
    return TODAY
