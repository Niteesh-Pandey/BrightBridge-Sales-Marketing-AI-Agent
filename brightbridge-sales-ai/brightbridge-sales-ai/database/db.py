"""Small database abstraction. SQLite by default; PostgreSQL-ready via DATABASE_URL.

All SQL uses '?' placeholders (always parameterised). For PostgreSQL they are
translated to '%s' at execution time.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import pandas as pd

import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class DatabaseError(Exception):
    """Raised for any database problem. Message is safe to show to users."""


class Database:
    def __init__(self, url: str | None = None):
        self.url = url or config.default_database_url()
        self.is_postgres = self.url.startswith(("postgres://", "postgresql://"))
        self._memory_conn: sqlite3.Connection | None = None
        if not self.is_postgres:
            if not self.url.startswith("sqlite:///"):
                raise DatabaseError("Unsupported DATABASE_URL. Use sqlite:///path or postgresql://...")
            self.path = self.url[len("sqlite:///"):]
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- connections
    @contextmanager
    def connect(self) -> Iterator[Any]:
        try:
            if self.is_postgres:
                try:
                    import psycopg  # optional dependency
                except ImportError as exc:  # pragma: no cover
                    raise DatabaseError("PostgreSQL support needs: pip install 'psycopg[binary]'") from exc
                conn = psycopg.connect(self.url)
            elif self.path == ":memory:":
                if self._memory_conn is None:
                    self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                    self._memory_conn.row_factory = sqlite3.Row
                    self._memory_conn.execute("PRAGMA foreign_keys = ON")
                conn = self._memory_conn
            else:
                conn = sqlite3.connect(self.path, timeout=30)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(f"Database operation failed: {type(exc).__name__}") from exc
        finally:
            if not (not self.is_postgres and self.path == ":memory:"):
                try:
                    conn.close()  # type: ignore[possibly-undefined]
                except Exception:
                    pass

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    # ---------------------------------------------------------------- queries
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(self._sql(sql), tuple(params))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_df(self, sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:
        rows = self.query(sql, params)
        return pd.DataFrame(rows)

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        rows = self.query(sql, params)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int | None:
        """Run a write statement; returns the new row id for INSERTs."""
        with self.connect() as conn:
            cur = conn.cursor()
            if self.is_postgres and sql.lstrip().upper().startswith("INSERT") and "RETURNING" not in sql.upper():
                cur.execute(self._sql(sql) + " RETURNING id", tuple(params))
                row = cur.fetchone()
                return row[0] if row else None
            cur.execute(self._sql(sql), tuple(params))
            return getattr(cur, "lastrowid", None)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.executemany(self._sql(sql), [tuple(r) for r in rows])

    # ----------------------------------------------------------------- schema
    def initialize(self) -> None:
        script = SCHEMA_PATH.read_text(encoding="utf-8")
        if self.is_postgres:
            script = script.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            with self.connect() as conn:
                cur = conn.cursor()
                for stmt in filter(None, (s.strip() for s in script.split(";"))):
                    cur.execute(stmt)
        else:
            with self.connect() as conn:
                conn.executescript(script)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after v0.1.0 to existing databases (idempotent)."""
        with self.connect() as conn:
            cur = conn.cursor()
            if self.is_postgres:
                cur.execute("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS win_probability REAL")
            else:
                cols = {r[1] for r in cur.execute("PRAGMA table_info(opportunities)").fetchall()}
                if "win_probability" not in cols:
                    cur.execute("ALTER TABLE opportunities ADD COLUMN win_probability REAL")

    def count(self, table: str) -> int:
        if not re.fullmatch(r"[a-z_]+", table):
            raise DatabaseError("Invalid table name")
        return int(self.scalar(f"SELECT COUNT(*) FROM {table}") or 0)

    def is_empty(self) -> bool:
        return self.count("leads") == 0

    def foreign_key_violations(self) -> list:
        if self.is_postgres:
            return []
        with self.connect() as conn:
            return [tuple(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]

    def clear_all(self) -> None:
        """Delete all rows (used only by demo seeding / tests)."""
        with self.connect() as conn:
            cur = conn.cursor()
            for table in ["generated_content", "activities", "opportunities", "leads", "campaigns", "contacts", "companies"]:
                cur.execute(f"DELETE FROM {table}")
