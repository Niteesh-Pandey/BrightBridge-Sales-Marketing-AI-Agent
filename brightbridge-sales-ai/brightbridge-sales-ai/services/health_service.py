"""System health report. Never reveals secrets."""
from __future__ import annotations

import os
import platform

import pandas as pd

import config
from database.db import Database, DatabaseError

OK, FAIL, NOT_CONFIGURED = "✓ OK", "✗ Problem", "○ Not configured"


def check_health(db: Database | None, mode: str) -> pd.DataFrame:
    rows = []
    # Database
    try:
        if db is None:
            raise DatabaseError("not connected")
        db.scalar("SELECT 1")
        rows.append(("Database", OK, f"{'PostgreSQL' if db.is_postgres else 'SQLite'} reachable"))
        leads = db.count("leads")
        fk = db.foreign_key_violations()
        if mode == "demo":
            rows.append(("Demo Data", OK if leads else FAIL, f"{leads} synthetic leads loaded" if leads else "No demo data; open Settings to regenerate"))
        else:
            rows.append(("Demo Data", "— n/a", f"Real mode: {leads} imported leads"))
        rows.append(("Referential integrity", OK if not fk else FAIL, "No foreign-key violations" if not fk else f"{len(fk)} violations"))
    except Exception as exc:
        rows.append(("Database", FAIL, f"{type(exc).__name__}: check DATABASE_URL and file permissions"))
        rows.append(("Demo Data", FAIL, "Database unavailable"))
    # Gemini
    from agent import gemini_client
    st = gemini_client.status()
    if st["state"] == "ok":
        rows.append(("Gemini API", OK, f"Verified at {st['checked_at']} · model {st['model']} (key hidden)"))
    elif st["state"] == "error":
        rows.append(("Gemini API", FAIL, st["message"]))
    elif st["state"] == "unknown":
        rows.append(("Gemini API", "○ Key set, not verified", "A key is present but no real test call has succeeded yet. Click 'Test Gemini connection'."))
    else:
        rows.append(("Gemini API", NOT_CONFIGURED, config.GEMINI_MISSING_MSG))
    # Environment
    writable = os.access(config.DATA_DIR, os.W_OK)
    rows.append(("Environment", OK if writable else FAIL,
                 f"Python {platform.python_version()} · DATA_MODE={mode} · data dir {'writable' if writable else 'NOT writable'}"))
    rows.append(("Application", OK, f"{config.APP_NAME} v{config.APP_VERSION}"))
    return pd.DataFrame(rows, columns=["Component", "Status", "Details"])
