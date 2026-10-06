"""Create the database schema (safe to run repeatedly)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from database.db import Database  # noqa: E402


def main() -> int:
    db = Database(config.default_database_url())
    db.initialize()
    print(f"Database ready ({'PostgreSQL' if db.is_postgres else 'SQLite'}): "
          f"{'<hidden>' if db.is_postgres else db.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
