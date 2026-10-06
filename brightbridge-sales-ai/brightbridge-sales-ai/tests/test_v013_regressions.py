"""Regression tests for the v0.1.3 fixes (funnel %, stale demo, follow-up crash, monthly axis, scoring)."""
from datetime import date, timedelta

import pandas as pd

from database.db import Database
from database.seed import ensure_demo_data, seed_database
from scripts.generate_demo_data import generate
from services import analytics_service as an
from services.crm_service import get_followups, get_lead_frame

TODAY = date(2026, 10, 1)


def _db(today=TODAY):
    db = Database("sqlite:///:memory:")
    db.initialize()
    seed_database(db, generate(60, 200, 110, 600, 8, seed=7, today=today))
    return db


def test_demo_data_always_has_win_probability():
    d = generate(30, 60, 40, 150, 4, seed=3)
    assert all(o["win_probability"] is not None and 0 <= o["win_probability"] <= 100 for o in d["opportunities"])
    won = [o for o in d["opportunities"] if o["stage"] == "Closed Won"]
    assert all(o["win_probability"] == 100 for o in won)


def test_demo_seeding_produces_call_now_leads_and_followups():
    db = _db(date.today())
    frame = get_lead_frame(db)
    assert int((frame["priority"] == "CALL NOW").sum()) >= 5
    assert len(get_followups(frame)) > 0


def test_monthly_pipeline_returns_real_month_timestamps():
    db = _db()
    d = an.monthly_pipeline(db)
    assert len(d) and pd.api.types.is_datetime64_any_dtype(d["month"])
    assert all(x.day == 1 for x in d["month"])


def test_followups_upcoming_filter_is_none_safe():
    db = _db()
    frame = get_lead_frame(db)
    assert frame["follow_up_date"].isna().any()                      # make sure we really test the None case
    out = get_followups(frame, include_upcoming_days=14)
    assert len(out) >= len(get_followups(frame))


def test_stale_demo_database_is_regenerated(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'demo.db').as_posix()}")
    db.initialize()
    old = TODAY - timedelta(days=400)
    seed_database(db, generate(20, 40, 20, 100, 4, seed=2, today=old))
    assert ensure_demo_data(db) is True                              # stale -> regenerated
    latest = db.scalar("SELECT MAX(activity_date) FROM activities")
    assert (date.today() - date.fromisoformat(str(latest)[:10])).days <= 30
    assert ensure_demo_data(db) is False                             # fresh data is kept
