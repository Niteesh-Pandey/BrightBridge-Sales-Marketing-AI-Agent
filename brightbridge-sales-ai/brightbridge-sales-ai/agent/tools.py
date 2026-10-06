"""CRM tools exposed to the agent (and to Gemini function calling).

Every tool reads from the database through deterministic services, returns JSON-safe
dicts, and records the call so the UI can show SUPPORTING DATA for every answer.
"""
from __future__ import annotations

import functools
import inspect
import json
import typing
from collections import Counter
from datetime import date

import pandas as pd

from agent.guardrails import INSUFFICIENT_DATA
from database.db import Database
from services import analytics_service as analytics
from services import crm_service as crm
from services.utils import ago, format_inr

_SHOW = ["lead_id", "company", "contact", "industry", "stage", "deal_value", "days_since_contact", "score", "priority",
         "next_action", "next_action_why", "follow_up_date", "follow_up_state"]


def _records(df: pd.DataFrame, cols=None) -> list[dict]:
    if df is None or df.empty:
        return []
    df = df[cols] if cols else df
    return json.loads(df.to_json(orient="records"))


def _clean(obj):
    return json.loads(json.dumps(obj, default=str))


class CRMToolkit:
    def __init__(self, db: Database, today: date | None = None):
        self.db, self.today = db, today or date.today()
        self.calls: list[dict] = []
        self._frame: pd.DataFrame | None = None

    # -- internals ---------------------------------------------------------
    @property
    def frame(self) -> pd.DataFrame:
        if self._frame is None:
            self._frame = crm.get_lead_frame(self.db, self.today)
        return self._frame

    def _record(self, tool: str, args: dict, result: dict) -> dict:
        self.calls.append({"tool": tool, "args": args, "result": result})
        return result

    def _result(self, tool, args, data, found=None, message=""):
        found = bool(data) if found is None else found
        return self._record(tool, args, {"found": found, "data": _clean(data) if found else None,
                                         "message": message if found else (message or INSUFFICIENT_DATA)})

    # -- tools -------------------------------------------------------------
    def search_leads(self, query: str, limit: int = 5) -> dict:
        """Search leads by company, contact, city, industry or email text. Returns scored leads."""
        df = crm.filter_leads(self.frame, search=query).head(max(1, min(int(limit), 25)))
        return self._result("search_leads", {"query": query, "limit": limit}, _records(df, _SHOW))

    def get_priority_leads(self, limit: int = 5) -> dict:
        """Open leads ranked by deterministic priority score (highest first). Use for 'who should I call'."""
        df = self.frame[self.frame["priority"] != "CLOSED"].sort_values(["score", "deal_value"], ascending=False)
        top = df[df["priority"] == "CALL NOW"].head(limit)
        if top.empty:
            top = df.head(limit)
        return self._result("get_priority_leads", {"limit": limit}, _records(top, _SHOW))

    def query_leads(self, industry: str = "", city: str = "", stage: str = "", source: str = "", priority: str = "",
                    min_value: float = 0, max_value: float = 0, min_days_since_contact: int = 0, limit: int = 8) -> dict:
        """Filter leads by industry, city, stage, source, priority (CALL NOW, FOLLOW UP SOON, NURTURE, LOW PRIORITY),
        deal value range in INR, and minimum days since last contact. Returns total match count and top leads by score."""
        df = self.frame
        for col, val in (("industry", industry), ("city", city), ("stage", stage), ("source", source), ("priority", priority)):
            if val:
                df = df[df[col].fillna("").str.lower() == str(val).lower()]
        if min_value:
            df = df[df["deal_value"] >= float(min_value)]
        if max_value:
            df = df[df["deal_value"] <= float(max_value)]
        if min_days_since_contact:
            df = df[df["days_since_contact"].fillna(10_000) >= int(min_days_since_contact)]
        df = df.sort_values(["score", "deal_value"], ascending=False)
        args = {k: v for k, v in dict(industry=industry, city=city, stage=stage, source=source, priority=priority, min_value=min_value,
                                      max_value=max_value, min_days_since_contact=min_days_since_contact, limit=limit).items() if v}
        data = {"total_matches": int(len(df)), "leads": _records(df.head(max(1, min(int(limit), 25))), _SHOW)}
        return self._result("query_leads", args, data, found=len(df) > 0)

    def get_lead(self, lead_id: int) -> dict:
        """Full details of one lead: contact, company, opportunity, score, next action."""
        d = crm.get_lead_detail(self.db, int(lead_id), self.today)
        if not d:
            return self._result("get_lead", {"lead_id": lead_id}, None)
        slim = {"lead_id": int(lead_id), "company": d["company"]["name"], "industry": d["company"]["industry"],
                "city": d["company"]["city"], "contact": d["lead"]["contact"], "job_title": d["lead"]["job_title"],
                "source": d["lead"]["source"], "status": d["lead"]["status"], "campaign": d["lead"]["campaign"],
                "opportunity": d["best_opportunity"], "score": d["score"], "priority": d["priority"],
                "score_breakdown": d["score_breakdown"], "score_reasons": d["score_reasons"],
                "days_since_contact": d["days_since_contact"], "follow_up": d["follow_up"], "next_action": d["next_action"]}
        return self._result("get_lead", {"lead_id": lead_id}, slim)

    def get_lead_history(self, lead_id: int, limit: int = 10) -> dict:
        """Most recent activities (calls, meetings, emails) for one lead."""
        acts = self.db.query("SELECT type, activity_date, outcome, follow_up_date FROM activities WHERE lead_id = ? "
                             "ORDER BY activity_date DESC, id DESC LIMIT ?", (int(lead_id), max(1, min(int(limit), 50))))
        return self._result("get_lead_history", {"lead_id": lead_id, "limit": limit}, acts)

    def get_pipeline(self) -> dict:
        """Pipeline value and counts by stage (open pipeline excludes closed deals)."""
        by_stage = analytics.pipeline_by_stage(self.db)
        k = analytics.kpis(self.db, self.frame)
        data = {"open_pipeline_value_inr": k["Pipeline Value"], "won_revenue_inr": k["Won Revenue"],
                "active_opportunities": k["Active Opportunities"],
                "by_stage": _records(by_stage.assign(stage=by_stage["stage"].astype(str)))}
        return self._result("get_pipeline", {}, data, found=self.db.count("opportunities") > 0)

    def get_opportunities(self, min_value: float = 0, stage: str = "", limit: int = 10) -> dict:
        """Open opportunities filtered by minimum value (INR) and/or stage, largest first."""
        df = self.frame[self.frame["opp_id"].notna() & ~self.frame["stage"].isin(["Closed Won", "Closed Lost"])]
        df = df[df["deal_value"] >= float(min_value or 0)]
        if stage:
            df = df[df["stage"].str.lower() == stage.lower()]
        df = df.sort_values("deal_value", ascending=False).head(max(1, min(int(limit), 25)))
        return self._result("get_opportunities", {"min_value": min_value, "stage": stage, "limit": limit},
                            _records(df, _SHOW + ["opp_name", "expected_close"]))

    def get_followups_due(self, include_upcoming_days: int = 0, limit: int = 10) -> dict:
        """Leads whose follow-up is overdue or due today (optionally upcoming within N days)."""
        full = crm.get_followups(self.frame, int(include_upcoming_days), self.today).sort_values("score", ascending=False)
        df, total = full.head(max(1, min(int(limit), 50))), len(full)
        res = self._result("get_followups_due", {"include_upcoming_days": include_upcoming_days, "limit": limit}, _records(df, _SHOW))
        res["total_due"] = total
        return res

    def calculate_lead_score(self, lead_id: int) -> dict:
        """Deterministic 0-100 score for one lead with factor breakdown (calculated by code, not by AI)."""
        d = crm.get_lead_detail(self.db, int(lead_id), self.today)
        if not d:
            return self._result("calculate_lead_score", {"lead_id": lead_id}, None)
        return self._result("calculate_lead_score", {"lead_id": lead_id},
                            {"lead_id": int(lead_id), "company": d["company"]["name"], "score": d["score"], "priority": d["priority"],
                             "breakdown": d["score_breakdown"], "reasons": d["score_reasons"]})

    def get_campaign_performance(self, limit: int = 5) -> dict:
        """Campaigns ranked by leads generated, with budget, clicks and cost per lead."""
        df = analytics.campaign_performance(self.db).head(max(1, min(int(limit), 20)))
        return self._result("get_campaign_performance", {"limit": limit},
                            _records(df, ["campaign", "channel", "leads", "clicks", "impressions", "budget_inr", "ctr_pct", "cost_per_lead"]) if not df.empty else [])

    def summarize_activity(self, lead_id: int) -> dict:
        """Deterministic activity summary for a lead: counts by type, first/last contact, pending follow-up."""
        d = crm.get_lead_detail(self.db, int(lead_id), self.today)
        if not d or not d["activities"]:
            return self._result("summarize_activity", {"lead_id": lead_id}, None)
        acts = d["activities"]
        counts = Counter(a["type"] for a in acts)
        data = {"lead_id": int(lead_id), "company": d["company"]["name"], "total_activities": len(acts),
                "by_type": dict(counts), "first_activity": acts[-1]["activity_date"], "last_activity": acts[0]["activity_date"],
                "days_since_contact": d["days_since_contact"], "pending_follow_up": d["follow_up"],
                "latest_outcomes": [{"date": a["activity_date"], "type": a["type"], "outcome": a["outcome"]} for a in acts[:3]]}
        return self._result("summarize_activity", {"lead_id": lead_id}, data)

    def generate_email(self, lead_id: int, goal: str = "Follow up on proposal") -> dict:
        """Draft (never send) a personalised email for one lead using only CRM facts. Needs Gemini."""
        from services.email_service import generate_email
        r = generate_email(self.db, int(lead_id), goal, today=self.today)
        return self._record("generate_email", {"lead_id": lead_id, "goal": goal}, _clean(r))

    def generate_social_post(self, topic: str, platform: str = "LinkedIn", audience: str = "SME owners",
                             tone: str = "Professional", objective: str = "Generate leads") -> dict:
        """Draft (never publish) a social post. Needs Gemini."""
        from services.marketing_service import generate_content
        kind = "LinkedIn post" if platform.lower() == "linkedin" else "Short social post"
        r = generate_content(self.db, kind, topic, audience, platform, tone, objective)
        return self._record("generate_social_post", {"topic": topic, "platform": platform}, _clean(r))

    # -- exposure to Gemini ---------------------------------------------------
    def gemini_functions(self) -> list:
        """Plain wrapper functions for Gemini function calling.

        The SDK deep-copies tool configs; bound methods would drag the whole toolkit (DataFrames, DB handles)
        into that copy and tool calls could be recorded on the copy. Plain functions are never copied.
        """
        def make(fn):
            @functools.wraps(fn)
            def tool(*args, **kwargs):
                return fn(*args, **kwargs)
            # `from __future__ import annotations` makes hints strings; the SDK needs real types to convert arguments.
            hints = typing.get_type_hints(fn)
            sig = inspect.signature(fn)
            tool.__signature__ = sig.replace(
                parameters=[p.replace(annotation=hints.get(p.name, p.annotation)) for p in sig.parameters.values()],
                return_annotation=hints.get("return", sig.return_annotation))
            tool.__annotations__ = dict(hints)
            del tool.__wrapped__
            return tool
        names = ["search_leads", "query_leads", "get_priority_leads", "get_lead", "get_lead_history", "get_pipeline", "get_opportunities",
                 "get_followups_due", "calculate_lead_score", "get_campaign_performance", "generate_email",
                 "generate_social_post", "summarize_activity"]
        return [make(getattr(self, n)) for n in names]

    TOOL_NAMES = ["search_leads", "get_lead", "get_lead_history", "get_pipeline", "get_opportunities", "get_followups_due",
                  "calculate_lead_score", "get_campaign_performance", "generate_email", "generate_social_post", "summarize_activity"]
