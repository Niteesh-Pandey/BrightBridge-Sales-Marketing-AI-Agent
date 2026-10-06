"""Deterministic analytics: KPIs and chart datasets. Pure SQL/pandas; no AI involved.

Defensive by design: all numeric columns are coerced first, every metric is wrapped in `safe_call`, and every
dataset function returns a well-formed (possibly empty) DataFrame instead of raising.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from database.db import Database
from database.models import OPEN_STAGES, STAGES
from services.crm_service import get_followups
from services.sanitize import clean_numeric_columns, safe_call

EMPTY_STAGE = pd.DataFrame({"stage": STAGES, "opportunities": 0, "value_inr": 0.0})
# Used only when an opportunity has no explicit win_probability (e.g. older imports)
STAGE_DEFAULT_PROB = {"Prospecting": 15.0, "Qualification": 30.0, "Proposal": 55.0, "Negotiation": 75.0,
                      "Closed Won": 100.0, "Closed Lost": 0.0}


def _opps(db: Database) -> pd.DataFrame:
    """All opportunities with guaranteed-numeric value_inr / win_probability and string stage."""
    df = db.query_df("SELECT o.id, o.lead_id, o.stage, o.value_inr, o.win_probability, o.created_at, c.industry "
                     "FROM opportunities o JOIN leads l ON l.id = o.lead_id JOIN companies c ON c.id = l.company_id")
    if df.empty:
        return pd.DataFrame({"id": [], "lead_id": [], "stage": [], "value_inr": [], "win_probability": [], "created_at": [], "industry": []})
    df = clean_numeric_columns(df, ["value_inr"], non_negative=True)
    df["win_probability"] = pd.to_numeric(df["win_probability"], errors="coerce")
    df["stage"] = df["stage"].fillna("Prospecting").astype(str).str.strip()
    df["created_at"] = df["created_at"].fillna("").astype(str)
    df["industry"] = df["industry"].fillna("Unknown").astype(str)
    return df


def kpis(db: Database, frame: pd.DataFrame) -> dict:
    opps = safe_call(_opps, pd.DataFrame({"stage": [], "value_inr": []}), "load opportunities", db)
    is_open = opps["stage"].isin(OPEN_STAGES) if len(opps) else pd.Series([], dtype=bool)
    return {
        "Total Leads": int(safe_call(lambda: len(frame), 0, "total leads")),
        "Active Opportunities": int(safe_call(lambda: int(is_open.sum()), 0, "active opportunities")),
        "Pipeline Value": float(safe_call(lambda: float(opps.loc[is_open, "value_inr"].sum()), 0.0, "pipeline value")),
        "Won Revenue": float(safe_call(lambda: float(opps.loc[opps["stage"] == "Closed Won", "value_inr"].sum()), 0.0, "won revenue")),
        "Follow-ups Due": int(safe_call(lambda: len(get_followups(frame)), 0, "follow-ups due")),
        "High Priority Leads": int(safe_call(lambda: int((frame["priority"] == "CALL NOW").sum()), 0, "high priority leads")),
    }


def extra_metrics(db: Database) -> dict:
    """Optional extra KPIs (average deal size, win rate, weighted pipeline) — each with a safe fallback."""
    opps = safe_call(_opps, pd.DataFrame({"stage": [], "value_inr": [], "win_probability": []}), "load opportunities", db)

    def avg_deal():
        v = opps.loc[opps["value_inr"] > 0, "value_inr"]
        return float(v.mean()) if len(v) else 0.0

    def win_rate():
        won, lost = int((opps["stage"] == "Closed Won").sum()), int((opps["stage"] == "Closed Lost").sum())
        return round(100.0 * won / (won + lost), 1) if (won + lost) else 0.0

    def weighted():
        o = opps[opps["stage"].isin(OPEN_STAGES)].copy()
        if o.empty:
            return 0.0, False
        explicit = bool(o["win_probability"].notna().any())
        # missing probabilities fall back to stage-based estimates so the metric never collapses to ₹0
        o["win_probability"] = o["win_probability"].fillna(o["stage"].map(STAGE_DEFAULT_PROB).fillna(15.0))
        return float((o["value_inr"] * o["win_probability"] / 100.0).sum()), explicit

    w_val, w_explicit = safe_call(weighted, (0.0, False), "weighted pipeline")
    return {"Average Deal Size": safe_call(avg_deal, 0.0, "avg deal"), "Win Rate %": safe_call(win_rate, 0.0, "win rate"),
            "Weighted Pipeline": w_val, "Weighted Pipeline Is Estimate": not w_explicit}


def pipeline_by_stage(db: Database) -> pd.DataFrame:
    def run():
        opps = _opps(db)
        if opps.empty:
            return EMPTY_STAGE.copy()
        df = opps.groupby("stage", as_index=False).agg(opportunities=("id", "count"), value_inr=("value_inr", "sum"))
        df["stage"] = pd.Categorical(df["stage"], STAGES, ordered=True)
        return df.sort_values("stage").reset_index(drop=True)
    return safe_call(run, EMPTY_STAGE.copy(), "pipeline by stage")


def leads_by_source(db: Database) -> pd.DataFrame:
    empty = pd.DataFrame({"source": [], "leads": []})

    def run():
        df = db.query_df("SELECT COALESCE(source,'Unknown') AS source, COUNT(*) AS leads FROM leads GROUP BY source ORDER BY leads DESC")
        return df if len(df) else empty
    return safe_call(run, empty, "leads by source")


def score_distribution(frame: pd.DataFrame) -> pd.DataFrame:
    empty = pd.DataFrame({"score": [], "priority": []})

    def run():
        if frame is None or frame.empty:
            return empty
        open_ = frame[frame["priority"] != "CLOSED"][["score", "priority"]].copy()
        open_["score"] = pd.to_numeric(open_["score"], errors="coerce").fillna(0)
        return open_ if len(open_) else empty
    return safe_call(run, empty, "score distribution")


def opportunities_by_industry(db: Database) -> pd.DataFrame:
    empty = pd.DataFrame({"industry": [], "opportunities": [], "value_inr": []})

    def run():
        opps = _opps(db)
        if opps.empty:
            return empty
        return (opps.groupby("industry", as_index=False).agg(opportunities=("id", "count"), value_inr=("value_inr", "sum"))
                .sort_values("value_inr", ascending=False).reset_index(drop=True))
    return safe_call(run, empty, "opportunities by industry")


def monthly_pipeline(db: Database) -> pd.DataFrame:
    empty = pd.DataFrame({"month": [], "value_inr": []})

    def run():
        opps = _opps(db)
        opps = opps[opps["created_at"].str.len() >= 7] if len(opps) else opps
        if opps.empty:
            return empty
        # real month-start timestamps (not "YYYY-MM" text) so every Plotly version renders clean date ticks
        # instead of raw end-of-month timestamps like "23:59:59.999 Sep 30"
        month = pd.to_datetime(opps["created_at"].str[:7] + "-01", errors="coerce")
        opps = opps.assign(month=month)
        opps = opps[opps["month"].notna()]
        if opps.empty:
            return empty
        return opps.groupby("month", as_index=False)["value_inr"].sum().sort_values("month")
    return safe_call(run, empty, "monthly pipeline")


def campaign_performance(db: Database) -> pd.DataFrame:
    cols = ["id", "campaign", "channel", "budget_inr", "impressions", "clicks", "leads", "ctr_pct", "cost_per_lead"]

    def run():
        df = db.query_df("""SELECT cp.id, cp.name AS campaign, cp.channel, cp.budget_inr, cp.impressions, cp.clicks,
            (SELECT COUNT(*) FROM leads l WHERE l.campaign_id = cp.id) AS leads FROM campaigns cp""")
        if df.empty:
            return pd.DataFrame(columns=cols)
        df = clean_numeric_columns(df, ["budget_inr", "impressions", "clicks", "leads"], non_negative=True)
        df["channel"] = df["channel"].fillna("Unknown").astype(str)
        imp, leads = df["impressions"].to_numpy(dtype=float), df["leads"].to_numpy(dtype=float)
        # zero denominators give NaN (undefined), never a TypeError
        with np.errstate(divide="ignore", invalid="ignore"):
            df["ctr_pct"] = np.round(np.where(imp > 0, df["clicks"].to_numpy(dtype=float) / np.where(imp > 0, imp, 1) * 100, np.nan), 2)
            df["cost_per_lead"] = np.round(np.where(leads > 0, df["budget_inr"].to_numpy(dtype=float) / np.where(leads > 0, leads, 1), np.nan), 0)
        df["leads"] = df["leads"].astype(int)
        return df.sort_values("leads", ascending=False).reset_index(drop=True)
    return safe_call(run, pd.DataFrame(columns=cols), "campaign performance")
