"""Load generated synthetic data into a database (demo mode only)."""
from __future__ import annotations

from datetime import date

from database.db import Database

_COLS = {
    "companies": ["id", "name", "industry", "city", "state", "size", "website", "created_at"],
    "contacts": ["id", "company_id", "full_name", "job_title", "email", "phone", "created_at"],
    "campaigns": ["id", "name", "channel", "start_date", "end_date", "budget_inr", "impressions", "clicks", "conversions"],
    "leads": ["id", "company_id", "contact_id", "campaign_id", "source", "status", "owner", "notes", "created_at"],
    "opportunities": ["id", "lead_id", "name", "stage", "value_inr", "expected_close_date", "created_at", "closed_at", "win_probability"],
    "activities": ["id", "lead_id", "opportunity_id", "type", "activity_date", "outcome", "notes", "follow_up_date"],
}

STALE_AFTER_DAYS = 45  # demo data older than this is regenerated so scores/follow-ups stay meaningful


def seed_database(db: Database, data: dict) -> None:
    """Replace database content with the given synthetic data (FK-safe order)."""
    db.clear_all()
    for table in ["companies", "contacts", "campaigns", "leads", "opportunities", "activities"]:
        cols = _COLS[table]
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        db.executemany(sql, [[row.get(c) for c in cols] for row in data[table]])


def _is_stale(db: Database, today: date) -> bool:
    """Demo data goes stale: all activities far in the past -> recency scores collapse to 0,
    follow-ups vanish and the priority list empties. Detect it so we can regenerate."""
    try:
        latest = db.scalar("SELECT MAX(activity_date) FROM activities")
        if not latest:
            return False
        from services.utils import parse_date
        d = parse_date(str(latest)[:10])
        return d is not None and (today - d).days > STALE_AFTER_DAYS
    except Exception:
        return False


def ensure_demo_data(db: Database, **kwargs) -> bool:
    """Create schema and, if empty (or stale from a previous session), generate fresh demo data.
    Returns True when data was (re)generated."""
    from scripts.generate_demo_data import generate

    db.initialize()
    today = date.today()
    if db.is_empty() or _is_stale(db, today):
        seed_database(db, generate(today=today, **kwargs))
        return True
    return False
