"""Dashboard / analytics must never raise TypeError, whatever is in the data."""
import numpy as np
import pandas as pd
import pytest

from database.db import Database
from services import analytics_service as an
from services.crm_service import get_lead_detail, get_lead_frame
from services.sanitize import clean_numeric_columns, safe_call, safe_div, to_number, to_probability
from ui import dashboard
from ui.common import kpi_html


def test_to_number_handles_everything():
    cases = {"₹3,20,000": 320000, " $1,500 ": 1500, "1.5L": 150000, "2 crore": 2e7, "250k": 250000, "Rs. 5,000": 5000, "(500)": -500, 7: 7.0,
             None: 0.0, "": 0.0, "abc": 0.0, "nan": 0.0, float("nan"): 0.0, float("inf"): 0.0, "N/A": 0.0, True: 0.0, "-": 0.0}
    for raw, expected in cases.items():
        assert to_number(raw) == expected, raw
    assert to_number("-5", non_negative=True) == 0.0 and to_number("abc", default=-1) == -1
    assert to_probability("60%") == 60 and to_probability("250") == 100 and to_probability("x") == 0


def test_clean_numeric_columns_replaces_nan_and_none_with_zero():
    df = pd.DataFrame({"value_inr": ["₹1,000", None, "abc", np.nan, 5], "keep": list("abcde")})
    out = clean_numeric_columns(df, ["value_inr", "missing_col"])
    assert out["value_inr"].tolist() == [1000.0, 0.0, 0.0, 0.0, 5.0] and out["value_inr"].dtype == "float64" and out["keep"].tolist() == list("abcde")


def test_safe_call_and_safe_div():
    assert safe_call(lambda: 1 / 0, "N/A", "boom") == "N/A" and safe_call(lambda: "ok", 0) == "ok"
    assert safe_div(5, 0) == 0.0 and safe_div("10", "4") == 2.5 and safe_div(None, None) == 0.0


def _db(tmp_path=None):
    db = Database("sqlite:///:memory:")
    db.initialize()
    return db


def test_campaign_without_leads_or_impressions_no_longer_raises():       # was: TypeError float() ... NAType
    db = _db()
    db.execute("INSERT INTO companies (name, created_at) VALUES ('A', '2026-01-01')")
    db.execute("INSERT INTO leads (company_id, status, created_at) VALUES (1,'New','2026-01-01')")
    db.execute("INSERT INTO campaigns (name, channel, budget_inr, impressions, clicks) VALUES ('Quiet','Email',50000,0,0)")
    db.execute("INSERT INTO campaigns (name, channel, budget_inr, impressions, clicks) VALUES ('Busy','Email',1000,100,10)")
    db.execute("UPDATE leads SET campaign_id = 2")
    perf = an.campaign_performance(db).set_index("campaign")
    assert np.isnan(perf.loc["Quiet", "ctr_pct"]) and np.isnan(perf.loc["Quiet", "cost_per_lead"])
    assert perf.loc["Busy", "ctr_pct"] == 10 and perf.loc["Busy", "cost_per_lead"] == 1000
    assert len(dashboard.make_figures(db, get_lead_frame(db))) == 6


def test_deal_values_stored_as_text_do_not_crash_anything():             # was: TypeError '>' not supported between float and str
    db = _db()
    db.execute("INSERT INTO companies (name, industry, created_at) VALUES ('B','Retail','2026-01-01')")
    db.execute("INSERT INTO leads (company_id, status, created_at) VALUES (1,'New','2026-01-01')")
    for i, v in enumerate(["₹3,20,000", 150000, "abc", "1.5L", "", " 2,000 "]):
        db.execute("INSERT INTO opportunities (lead_id, name, stage, value_inr, created_at) VALUES (1,?,'Proposal',?,'2026-01-01')", (f"D{i}", v))
    frame = get_lead_frame(db)
    assert float(frame.iloc[0]["deal_value"]) == 320000                      # best open deal, parsed from "₹3,20,000"
    k = an.kpis(db, frame)
    assert k["Pipeline Value"] == 320000 + 150000 + 150000 + 2000 and k["Active Opportunities"] == 6
    assert len(dashboard.make_figures(db, frame)) == 6 and get_lead_detail(db, 1)["best_opportunity"]["value_inr"] == 320000
    assert "₹" in kpi_html(k)


def test_none_dates_and_stages_are_tolerated():
    db = _db()
    db.execute("INSERT INTO companies (name, created_at) VALUES ('C','2026-01-01')")
    db.execute("INSERT INTO leads (company_id, status, created_at) VALUES (1,'New','2026-01-01')")
    db.execute("INSERT INTO opportunities (lead_id, name, stage, value_inr, created_at) VALUES (1,'D','Closed Won',5,'')")
    db.execute("INSERT INTO opportunities (lead_id, name, stage, value_inr, created_at) VALUES (1,'D2','Closed Lost',5,'2026-01-01')")
    frame = get_lead_frame(db)
    assert len(frame) == 1 and len(dashboard.make_figures(db, frame)) == 6


def test_empty_database_gives_safe_defaults():
    db = _db()
    frame = get_lead_frame(db)
    assert an.kpis(db, frame) == {"Total Leads": 0, "Active Opportunities": 0, "Pipeline Value": 0.0, "Won Revenue": 0.0, "Follow-ups Due": 0, "High Priority Leads": 0}
    assert an.extra_metrics(db) == {"Average Deal Size": 0.0, "Win Rate %": 0.0, "Weighted Pipeline": 0.0,
                                    "Weighted Pipeline Is Estimate": True}
    for fn in (an.pipeline_by_stage, an.leads_by_source, an.opportunities_by_industry, an.monthly_pipeline, an.campaign_performance):
        assert isinstance(fn(db), pd.DataFrame)
    assert isinstance(an.score_distribution(frame), pd.DataFrame) and len(dashboard.make_figures(db, frame)) == 6


def test_analytics_survive_a_broken_database(monkeypatch):
    db = _db()
    monkeypatch.setattr(db, "query_df", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db exploded")))
    frame = pd.DataFrame({"priority": [], "score": []})
    assert an.kpis(db, frame)["Pipeline Value"] == 0.0
    assert an.pipeline_by_stage(db)["value_inr"].sum() == 0 and an.campaign_performance(db).empty
    assert len(dashboard.make_figures(db, frame)) == 6


def test_one_failing_chart_does_not_take_down_the_dashboard(monkeypatch):
    db = _db()
    monkeypatch.setattr(an, "leads_by_source", lambda d: (_ for _ in ()).throw(TypeError("boom")))
    figs = dashboard.make_figures(db, pd.DataFrame({"priority": [], "score": []}))
    assert len(figs) == 6 and any("unavailable" in str(f.layout.annotations) for f in figs)


def test_weighted_pipeline_and_win_rate():
    db = _db()
    db.execute("INSERT INTO companies (name, created_at) VALUES ('W','2026-01-01')")
    db.execute("INSERT INTO leads (company_id, status, created_at) VALUES (1,'New','2026-01-01')")
    rows = [("a", "Proposal", 1000, 50), ("b", "Negotiation", 2000, 25), ("c", "Closed Won", 500, None), ("d", "Closed Lost", 500, None)]
    for n, st, v, p in rows:
        db.execute("INSERT INTO opportunities (lead_id, name, stage, value_inr, win_probability, created_at) VALUES (1,?,?,?,?,'2026-01-01')", (n, st, v, p))
    m = an.extra_metrics(db)
    assert m["Weighted Pipeline"] == 1000 and m["Win Rate %"] == 50.0 and m["Average Deal Size"] == 1000.0
    assert m["Weighted Pipeline Is Estimate"] is False        # explicit probabilities were used


def test_weighted_pipeline_falls_back_to_stage_estimates():
    db = _db()
    db.execute("INSERT INTO companies (name, created_at) VALUES ('W','2026-01-01')")
    db.execute("INSERT INTO leads (company_id, status, created_at) VALUES (1,'New','2026-01-01')")
    db.execute("INSERT INTO opportunities (lead_id, name, stage, value_inr, created_at) VALUES (1,'a','Proposal',100000,'2026-01-01')")
    db.execute("INSERT INTO opportunities (lead_id, name, stage, value_inr, created_at) VALUES (1,'b','Negotiation',200000,'2026-01-01')")
    m = an.extra_metrics(db)
    assert m["Weighted Pipeline"] == 100000 * 0.55 + 200000 * 0.75 and m["Weighted Pipeline Is Estimate"] is True


def test_old_database_without_probability_column_is_migrated(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript("CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, industry TEXT, city TEXT, state TEXT, size TEXT, website TEXT, created_at TEXT);"
                      "CREATE TABLE leads (id INTEGER PRIMARY KEY, company_id INT, contact_id INT, campaign_id INT, source TEXT, status TEXT, owner TEXT, notes TEXT, created_at TEXT);"
                      "CREATE TABLE opportunities (id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INT, name TEXT, stage TEXT, value_inr REAL, expected_close_date TEXT, created_at TEXT, closed_at TEXT);")
    con.commit(); con.close()
    db = Database(f"sqlite:///{p.as_posix()}")
    db.initialize()
    cols = {r["name"] for r in db.query("PRAGMA table_info(opportunities)")}
    assert "win_probability" in cols
    db.initialize()                                                       # idempotent
