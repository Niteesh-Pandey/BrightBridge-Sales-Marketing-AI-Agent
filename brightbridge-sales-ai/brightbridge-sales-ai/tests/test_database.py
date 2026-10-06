import pytest

from database.db import Database, DatabaseError
from database.models import TABLES


def test_schema_creates_all_tables(empty_db):
    names = {r["name"] for r in empty_db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= names


def test_indexes_exist(empty_db):
    idx = {r["name"] for r in empty_db.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_leads_company" in idx and "idx_acts_lead" in idx


def test_foreign_keys_enforced(empty_db):
    with pytest.raises(DatabaseError):
        empty_db.execute("INSERT INTO contacts (company_id, full_name, created_at) VALUES (999, 'X', '2026-01-01')")


def test_parameterised_queries_resist_injection(empty_db):
    empty_db.execute("INSERT INTO companies (name, created_at) VALUES (?, ?)", ("Safe Co", "2026-01-01"))
    rows = empty_db.query("SELECT * FROM companies WHERE name = ?", ("x'; DROP TABLE companies;--",))
    assert rows == [] and empty_db.count("companies") == 1


def test_insert_returns_id(empty_db):
    assert empty_db.execute("INSERT INTO companies (name, created_at) VALUES (?, ?)", ("A", "2026-01-01")) == 1


def test_bad_url_rejected():
    with pytest.raises(DatabaseError):
        Database("mysql://nope")


def test_invalid_table_name_rejected(empty_db):
    with pytest.raises(DatabaseError):
        empty_db.count("companies; DROP TABLE x")


def test_initialize_is_idempotent(empty_db):
    empty_db.initialize()
    assert empty_db.is_empty()
