"""CRM read-model: builds the scored lead table and detail views from the database."""
from __future__ import annotations

from datetime import date

import pandas as pd

from database.db import Database
from database.models import OPEN_STAGES
from services.lead_scoring import ScoreResult, pending_followup, score_lead
from services.sales_service import recommend_next_action
from services.sanitize import safe_str, to_number
from services.utils import parse_date

FRAME_COLUMNS = ["lead_id", "company_id", "company", "contact", "job_title", "email", "phone", "industry", "city",
                 "source", "status", "campaign", "created_date", "opp_id", "opp_name", "stage", "deal_value",
                 "expected_close", "activity_count", "last_contact_date", "last_activity_type", "days_since_contact",
                 "follow_up_date", "follow_up_state", "score", "priority", "next_action", "next_action_why"]

_LEAD_SQL = """
SELECT l.id AS lead_id, l.company_id, c.name AS company, ct.full_name AS contact, ct.job_title, ct.email, ct.phone,
       c.industry, c.city, l.source, l.status, cp.name AS campaign, l.created_at AS created_date
FROM leads l
JOIN companies c ON c.id = l.company_id
LEFT JOIN contacts ct ON ct.id = l.contact_id
LEFT JOIN campaigns cp ON cp.id = l.campaign_id
"""


def clean_opportunities(opps: list[dict]) -> list[dict]:
    """Type-safe opportunity rows: numeric value (never None/str), text stage/dates (never None)."""
    out = []
    for o in opps:
        o = dict(o)
        o["value_inr"] = to_number(o.get("value_inr"), 0.0, non_negative=True)
        o["stage"] = safe_str(o.get("stage"), "Prospecting").strip() or "Prospecting"
        o["created_at"] = safe_str(o.get("created_at"))
        o["name"] = safe_str(o.get("name"), "Deal")
        out.append(o)
    return out


def _best_opportunity(opps: list[dict]) -> dict | None:
    if not opps:
        return None
    open_ = [o for o in opps if o["stage"] in OPEN_STAGES]
    if open_:
        return max(open_, key=lambda o: to_number(o["value_inr"]))
    return max(opps, key=lambda o: safe_str(o["created_at"]))


def get_lead_frame(db: Database, today: date | None = None) -> pd.DataFrame:
    """One row per lead with deterministic score, priority and next action."""
    today = today or date.today()
    leads = db.query(_LEAD_SQL)
    if not leads:
        return pd.DataFrame(columns=FRAME_COLUMNS)
    acts_by, opps_by = {}, {}
    for a in db.query("SELECT lead_id, type, activity_date, follow_up_date FROM activities"):
        acts_by.setdefault(a["lead_id"], []).append(a)
    for o in clean_opportunities(db.query("SELECT * FROM opportunities")):
        opps_by.setdefault(o["lead_id"], []).append(o)

    rows = []
    for lead in leads:
        acts, opp = acts_by.get(lead["lead_id"], []), _best_opportunity(opps_by.get(lead["lead_id"], []))
        dates = [(parse_date(a["activity_date"]), a["type"]) for a in acts]
        last_d, last_t = max(((d, t) for d, t in dates if d), default=(None, None))
        fu_date, fu_state = pending_followup(acts, today)
        res = score_lead(acts, opp, lead["status"], today)
        row = {**lead, "opp_id": opp["id"] if opp else None, "opp_name": opp["name"] if opp else None,
               "stage": opp["stage"] if opp else "No Opportunity", "deal_value": float(opp["value_inr"]) if opp else 0.0,
               "expected_close": opp["expected_close_date"] if opp else None, "activity_count": len(acts),
               "last_contact_date": last_d.isoformat() if last_d else None, "last_activity_type": last_t,
               "days_since_contact": (today - last_d).days if last_d else None,
               "follow_up_date": fu_date.isoformat() if fu_date else None, "follow_up_state": fu_state,
               "score": res.score, "priority": res.priority}
        nxt = recommend_next_action(row)
        row["next_action"], row["next_action_why"] = nxt["action"], nxt["why"]
        rows.append(row)
    df = pd.DataFrame(rows)[FRAME_COLUMNS]
    df["days_since_contact"] = pd.to_numeric(df["days_since_contact"], errors="coerce").astype("float")
    df["deal_value"] = pd.to_numeric(df["deal_value"], errors="coerce").fillna(0.0)
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0).astype(int)
    df["created_date"] = df["created_date"].map(lambda v: safe_str(v))
    return df.sort_values(["score", "deal_value"], ascending=False).reset_index(drop=True)


def filter_leads(df: pd.DataFrame, search: str = "", industry: str = "All", stage: str = "All", source: str = "All",
                 priority: str = "All", min_value: float | None = None, max_value: float | None = None,
                 date_from: str | None = None, date_to: str | None = None) -> pd.DataFrame:
    out = df
    if search and search.strip():
        q = search.strip().lower()
        mask = pd.Series(False, index=out.index)
        for col in ("company", "contact", "email", "city", "industry", "opp_name"):
            mask |= out[col].fillna("").astype(str).str.lower().str.contains(q, regex=False)
        out = out[mask]
    for col, val in (("industry", industry), ("stage", stage), ("source", source), ("priority", priority)):
        if val and val != "All":
            out = out[out[col] == val]
    if min_value not in (None, "") and to_number(min_value) > 0:
        out = out[out["deal_value"] >= float(min_value)]
    if max_value not in (None, "") and to_number(max_value) > 0:
        out = out[out["deal_value"] <= float(max_value)]
    d_from, d_to = parse_date(date_from), parse_date(date_to)
    if d_from:
        out = out[out["created_date"].map(safe_str) >= d_from.isoformat()]
    if d_to:
        out = out[out["created_date"].map(safe_str) <= d_to.isoformat()]
    return out


def get_lead_detail(db: Database, lead_id: int, today: date | None = None) -> dict | None:
    """Full evidence record for one lead (used by UI, email and agent)."""
    today = today or date.today()
    leads = db.query(_LEAD_SQL + " WHERE l.id = ?", (int(lead_id),))
    if not leads:
        return None
    lead = leads[0]
    company = db.query("SELECT * FROM companies WHERE id = ?", (lead["company_id"],))[0]
    acts = db.query("SELECT * FROM activities WHERE lead_id = ? ORDER BY activity_date DESC, id DESC", (int(lead_id),))
    opps = clean_opportunities(db.query("SELECT * FROM opportunities WHERE lead_id = ? ORDER BY created_at DESC", (int(lead_id),)))
    best = _best_opportunity(opps)
    res: ScoreResult = score_lead(acts, best, lead["status"], today)
    dates = [parse_date(a["activity_date"]) for a in acts if parse_date(a["activity_date"])]
    last = max(dates, default=None)
    fu_date, fu_state = pending_followup(acts, today)
    frame_row = {"stage": best["stage"] if best else "No Opportunity", "deal_value": best["value_inr"] if best else 0,
                 "days_since_contact": (today - last).days if last else None,
                 "last_activity_type": acts[0]["type"] if acts else None,
                 "follow_up_date": fu_date.isoformat() if fu_date else None, "follow_up_state": fu_state}
    return {"lead": lead, "company": company, "activities": acts, "opportunities": opps, "best_opportunity": best,
            "score": res.score, "priority": res.priority, "score_breakdown": res.breakdown, "score_reasons": res.reasons,
            "next_action": recommend_next_action(frame_row), "days_since_contact": frame_row["days_since_contact"],
            "follow_up": {"date": frame_row["follow_up_date"], "state": fu_state}}


def search_leads(db: Database, query: str, limit: int = 10, today: date | None = None) -> pd.DataFrame:
    df = get_lead_frame(db, today)
    return filter_leads(df, search=query).head(limit)


def find_leads_in_text(df: pd.DataFrame, text: str) -> pd.DataFrame:
    """Leads whose company or contact name is mentioned in free text."""
    t = (text or "").lower()
    if df.empty:
        return df
    mask = df["company"].str.lower().apply(lambda n: n in t) | df["contact"].fillna("").str.lower().apply(lambda n: bool(n) and n in t)
    return df[mask]


def get_followups(df: pd.DataFrame, include_upcoming_days: int = 0, today: date | None = None) -> pd.DataFrame:
    """Follow-ups that are overdue or due today (plus upcoming within N days)."""
    today = today or date.today()
    if df is None or df.empty:
        return pd.DataFrame(columns=FRAME_COLUMNS)
    states = ["overdue", "due_today"]
    out = df[df["follow_up_state"].isin(states)]
    if include_upcoming_days > 0:
        horizon = (today + pd.Timedelta(days=include_upcoming_days)).isoformat()
        # fillna with a far-future sentinel: comparing None <= "YYYY-MM-DD" is a TypeError on some pandas versions
        fu = df["follow_up_date"].fillna("9999-12-31").astype(str)
        up = df[(df["follow_up_state"] == "upcoming") & (fu <= horizon)]
        out = pd.concat([out, up])
    return out[out["stage"] != "Closed Won"].sort_values("follow_up_date")


def filter_options(df: pd.DataFrame) -> dict:
    def opts(col):
        return ["All"] + sorted(x for x in df[col].dropna().unique().tolist()) if not df.empty else ["All"]
    return {"industry": opts("industry"), "stage": opts("stage"), "source": opts("source"),
            "priority": ["All", "CALL NOW", "FOLLOW UP SOON", "NURTURE", "LOW PRIORITY", "CLOSED"]}
