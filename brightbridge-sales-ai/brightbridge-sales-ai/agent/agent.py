"""Evidence-first sales agent.

Flow: question -> intent -> CRM tools (deterministic) -> evidence -> [Gemini phrasing] -> ANSWER /
SUPPORTING DATA / SUGGESTED ACTION. CRM data is ALWAYS retrieved before an answer is produced.
Without Gemini the same deterministic answers are returned (CRM features never depend on AI).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

import config
from agent import gemini_client
from agent.guardrails import (HUMAN_APPROVAL_NOTE, INSUFFICIENT_DATA, is_destructive_request, is_send_or_publish_request,
                              safe_error_message, sanitize_text)
from agent.prompts import REPHRASE_PROMPT, SYSTEM_PROMPT
from agent.tools import CRMToolkit
from database.db import Database
from services.crm_service import find_leads_in_text
from services.utils import ago, format_inr

EXAMPLES = ["Which leads should I call today?", "Show high-value opportunities.", "Which leads are overdue?",
            "Why is Apex Digital Systems high priority?", "Summarize the history of <company>.", "What is my pipeline value?",
            "Which campaign generated the most leads?", "Write a follow-up email for <company>.",
            "Create a LinkedIn post about sales automation."]


@dataclass
class AgentResponse:
    answer: str
    supporting: str = ""
    action: str = ""
    intent: str = "general"
    tools_used: list = field(default_factory=list)
    ai_used: bool = False
    notice: str = ""
    draft: dict | None = None

    def to_markdown(self) -> str:
        parts = []
        if self.notice:
            parts.append(f"> {self.notice}\n")
        parts.append(f"### ANSWER\n{self.answer}")
        if self.draft:
            d = self.draft
            parts.append("### DRAFT (editable in the AI Email / Marketing tabs)\n" + (f"**Subject:** {d['subject']}\n\n" if d.get("subject") else "") + d.get("body", ""))
        if self.supporting:
            parts.append(f"### SUPPORTING DATA\n{self.supporting}")
        if self.action:
            parts.append(f"### SUGGESTED ACTION\n{self.action}")
        if self.tools_used:
            parts.append("<sub>Data retrieved with: " + ", ".join(dict.fromkeys(self.tools_used)) + "</sub>")
        return "\n\n".join(parts)


# --------------------------------------------------------------------------- rendering helpers
_LABELS = {"company": "Company", "contact": "Contact", "stage": "Stage", "deal_value": "Deal Value", "days_since_contact": "Last Contact",
           "score": "Score", "priority": "Priority", "next_action": "Next Action", "industry": "Industry", "follow_up_date": "Follow-up",
           "follow_up_state": "Follow-up State", "opp_name": "Opportunity", "expected_close": "Expected Close", "campaign": "Campaign",
           "channel": "Channel", "leads": "Leads", "clicks": "Clicks", "impressions": "Impressions", "budget_inr": "Budget",
           "ctr_pct": "CTR %", "cost_per_lead": "Cost/Lead", "type": "Type", "activity_date": "Date", "outcome": "Outcome"}
_LABELS.update({"open_pipeline_value_inr": "Open Pipeline Value", "won_revenue_inr": "Won Revenue", "value_inr": "Value",
                "active_opportunities": "Active Opportunities", "total_activities": "Total Activities", "first_activity": "First Activity",
                "last_activity": "Last Activity", "job_title": "Job Title", "pending_follow_up": "Pending Follow-up", "follow_up": "Follow-up"})
_SKIP = {"lead_id", "next_action_why", "company_id"}


def _fmt(key: str, val) -> str:
    if val is None:
        return "—"
    if key in ("deal_value", "budget_inr", "cost_per_lead") or key.endswith("_inr"):
        return format_inr(val)
    if key == "days_since_contact":
        return ago(val)
    if isinstance(val, float):
        return f"{val:,.2f}".rstrip("0").rstrip(".")
    return str(val).replace("|", "/")


def md_table(rows: list[dict], max_rows: int = 8, max_cols: int = 7) -> str:
    if not rows:
        return ""
    cols = [c for c in rows[0].keys() if c not in _SKIP and not isinstance(rows[0][c], (dict, list))][:max_cols]
    head = "| " + " | ".join(_LABELS.get(c, c.replace("_", " ").title()) for c in cols) + " |"
    sep = "|" + "---|" * len(cols)
    body = ["| " + " | ".join(_fmt(c, r.get(c)) for c in cols) + " |" for r in rows[:max_rows]]
    return "\n".join([head, sep, *body])


def md_kv(d: dict) -> str:
    lines = []
    for k, v in d.items():
        if k in _SKIP or k in ("score_breakdown", "score_reasons", "breakdown", "reasons"):
            continue
        if k == "opportunity" and isinstance(v, dict):
            lines.append(f"- **Opportunity:** {v.get('name')} — {v.get('stage')}, {format_inr(v.get('value_inr'))}")
            continue
        if isinstance(v, (dict, list)):
            if isinstance(v, dict) and all(not isinstance(x, (dict, list)) for x in v.values()):
                v = ", ".join(f"{a.replace('_', ' ')}: {_fmt(a, b)}" for a, b in v.items() if b is not None)
            else:
                continue
        lines.append(f"- **{_LABELS.get(k, k.replace('_', ' ').title())}:** {_fmt(k, v)}")
    return "\n".join(lines)


def render_evidence(calls: list[dict]) -> str:
    blocks = []
    for c in calls:
        res = c["result"]
        if not res.get("found") or c["tool"] in ("generate_email", "generate_social_post"):
            continue
        data = res["data"]
        if isinstance(data, dict) and isinstance(data.get("leads"), list):
            blocks.append(f"**{data.get('total_matches', len(data['leads']))} matching lead(s)** (top {len(data['leads'])} by score):")
            blocks.append(md_table(data["leads"]))
            continue
        if isinstance(data, list):
            blocks.append(md_table(data))
        elif isinstance(data, dict):
            blocks.append(md_kv({k: v for k, v in data.items() if k != "by_stage"}))
            if data.get("by_stage"):
                blocks.append(md_table(data["by_stage"]))
            if isinstance(data.get("breakdown"), dict):
                blocks.append("Score breakdown: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in data["breakdown"].items()))
    return "\n\n".join(b for b in blocks if b)


def _parse_amount(text: str) -> float | None:
    m = re.search(r"(?:above|over|more than|>=?|at least|min(?:imum)?)\s*₹?\s*([\d][\d,\.]*)\s*(crore|cr|lakh|lac|l|k)?", text.lower())
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return n * {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5, "l": 1e5, "k": 1e3}.get(m.group(2) or "", 1)


_STAGE_WORDS = {"closed won": "Closed Won", "won deals": "Closed Won", "won deal": "Closed Won", "closed lost": "Closed Lost",
                "lost deals": "Closed Lost", "lost deal": "Closed Lost", "prospecting": "Prospecting", "qualification": "Qualification",
                "proposal": "Proposal", "negotiation": "Negotiation"}
_PRIORITY_WORDS = {"call now": "CALL NOW", "high priority": "CALL NOW", "hot lead": "CALL NOW", "follow up soon": "FOLLOW UP SOON",
                   "nurture": "NURTURE", "low priority": "LOW PRIORITY"}


def parse_filters(q: str, frame) -> dict:
    """Pull simple filter criteria (industry, city, stage, source, priority, value, inactivity) out of a question."""
    ql = q.lower()
    f: dict = {}
    if frame is None or frame.empty:
        return f
    for col, key in (("industry", "industry"), ("city", "city"), ("source", "source")):
        vals = sorted((v for v in frame[col].dropna().unique() if len(str(v)) > 2), key=lambda v: -len(str(v)))
        hit = next((v for v in vals if re.search(r"\b" + re.escape(str(v).lower()) + r"\b", ql)), None)
        if hit:
            f[key] = hit
    stage = next((v for k, v in _STAGE_WORDS.items() if k in ql), None)
    if stage:
        f["stage"] = stage
    prio = next((v for k, v in _PRIORITY_WORDS.items() if k in ql), None)
    if prio:
        f["priority"] = prio
    m = re.search(r"(?:no contact|not (?:been )?contacted|inactive|untouched|silent|no activity)[^\d]{0,20}(\d+)\s*days", ql)
    if m:
        f["min_days_since_contact"] = int(m.group(1))
    elif re.search(r"not (?:been )?contacted|no contact|inactive|stale|gone cold|haven'?t (?:been )?contacted|untouched|no activity", ql):
        f["min_days_since_contact"] = 30
    low = re.search(r"(?:below|under|less than|<=?)\s*₹?\s*([\d][\d,\.]*)\s*(crore|cr|lakh|lac|l|k)?", ql)
    if low:
        mult = {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5, "l": 1e5, "k": 1e3}.get(low.group(2) or "", 1)
        try:
            f["max_value"] = float(low.group(1).replace(",", "")) * mult
        except ValueError:
            pass
    amount = _parse_amount(q)
    if amount:
        f["min_value"] = amount
    return f


# --------------------------------------------------------------------------- the agent
class SalesAgent:
    HIGH_VALUE = 300_000

    def __init__(self, db: Database, today: date | None = None):
        self.db, self.today = db, today or date.today()
        self.tk = CRMToolkit(db, self.today)

    # public -------------------------------------------------------------------
    def classify(self, q: str) -> str:
        ql = q.lower()
        if is_destructive_request(ql):
            return "refuse"
        if re.search(r"linkedin|social (media )?post|tweet|caption|campaign idea|content calendar|marketing (post|content)", ql):
            return "social"
        if re.search(r"\b(e-?mail|follow-?up (mail|message)|draft)\b", ql) and re.search(r"write|draft|generate|create|compose|send|prepare", ql):
            return "email"
        if re.search(r"\bwhy\b", ql) and re.search(r"priorit|score|rank|call now|hot", ql):
            return "why"
        if re.search(r"summar|history|timeline|what happened|tell me about", ql):
            return "summary"
        if re.search(r"overdue|follow-?ups? due|due follow|missed follow", ql):
            return "overdue"
        if re.search(r"campaign", ql):
            return "campaign"
        filters = parse_filters(q, self.tk.frame)
        dims = set(filters) - {"priority", "min_value"}
        if dims or ("priority" in filters and re.search(r"\bin\b|\bfrom\b|list|show|which|how many", ql) and not re.search(r"call|today|who should", ql)):
            return "filter"
        if re.search(r"how many|number of|count of", ql) and re.search(r"lead|deal|opportunit|contact|compan", ql):
            return "filter"
        if re.search(r"high[- ]value|big deal|largest|biggest|top (deals|opportunit)|opportunit", ql):
            return "opps"
        if re.search(r"pipeline|forecast|revenue", ql):
            return "pipeline"
        if re.search(r"call|prioriti|who should|whom|top leads|best leads|hot leads|today", ql):
            return "priority"
        return "general"

    def ask(self, question: str, focus_lead_id: int | None = None) -> AgentResponse:
        q = sanitize_text(question)
        if not q:
            return AgentResponse("Please type a question about your CRM, e.g. “" + EXAMPLES[0] + "”.")
        self.tk.calls.clear()
        try:
            intent = self.classify(q)
            if intent == "refuse":
                return AgentResponse(HUMAN_APPROVAL_NOTE + " Please make record changes yourself in the CRM tab or via Import Data.", intent=intent)
            handler = getattr(self, f"_h_{intent}")
            resp: AgentResponse = handler(q, focus_lead_id)
            resp.intent = intent
            if is_send_or_publish_request(q) and intent in ("email", "social"):
                resp.notice = HUMAN_APPROVAL_NOTE
            if intent not in ("email", "social", "general") and resp.answer != INSUFFICIENT_DATA:
                self._phrase_with_gemini(q, resp)
            resp.tools_used = [c["tool"] for c in self.tk.calls]
            return resp
        except Exception as exc:  # never leak stack traces
            return AgentResponse(safe_error_message(exc, "assistant request"))

    # helpers ------------------------------------------------------------------
    def _resolve_lead(self, q: str, focus_lead_id):
        found = find_leads_in_text(self.tk.frame, q)
        if not found.empty:
            return int(found.sort_values("score", ascending=False).iloc[0]["lead_id"]), len(found)
        if focus_lead_id:
            return int(focus_lead_id), 1
        return None, 0

    def _need_lead(self) -> AgentResponse:
        return AgentResponse("Which lead do you mean? Mention the company or contact name (or pick one in “Focus lead”).",
                             action="Example: “" + EXAMPLES[3] + "”")

    def _phrase_with_gemini(self, q: str, resp: AgentResponse) -> None:
        if not gemini_client.is_configured():
            return
        evidence = json.dumps([{"tool": c["tool"], "args": c["args"], "data": c["result"].get("data")} for c in self.tk.calls],
                              default=str)[:7000]
        try:
            data = gemini_client.parse_json(gemini_client.generate_text(
                REPHRASE_PROMPT.format(question=q, evidence=evidence), SYSTEM_PROMPT, temperature=0.2))
            ans, act = str(data.get("answer", "")).strip(), str(data.get("suggested_action", "")).strip()
            if ans and ans != INSUFFICIENT_DATA:
                resp.answer, resp.ai_used = ans, True
                if act:
                    resp.action = act
        except gemini_client.GeminiError as exc:
            resp.notice = f"{exc} Showing the deterministic CRM answer instead."
        except Exception:
            pass

    def _no_data(self, supporting: str = "") -> AgentResponse:
        return AgentResponse(INSUFFICIENT_DATA, supporting=supporting)

    # intent handlers ------------------------------------------------------------
    def _h_priority(self, q, focus):
        r = self.tk.get_priority_leads(5)
        if not r["found"]:
            return self._no_data()
        rows, top = r["data"], r["data"][0]
        n_call = sum(1 for x in rows if x["priority"] == "CALL NOW")
        if top["priority"] == "CALL NOW":
            answer = f"Call {top['company']} today" + (f", then {', '.join(x['company'] for x in rows[1:n_call])}." if n_call > 1 else ".")
        else:
            answer = f"No lead reaches CALL NOW today. The highest-ranked lead is {top['company']} ({top['priority']}, score {top['score']})."
        return AgentResponse(answer, render_evidence(self.tk.calls),
                             f"{top['next_action']}: {top['next_action_why']}")

    def _h_opps(self, q, focus):
        threshold = _parse_amount(q) or self.HIGH_VALUE
        r = self.tk.get_opportunities(min_value=threshold, limit=8)
        if not r["found"]:
            r = self.tk.get_opportunities(min_value=0, limit=5)
            if not r["found"]:
                return self._no_data()
            note = f"No open opportunity is above {format_inr(threshold)}; showing the largest open deals instead."
        else:
            note = f"{len(r['data'])} open opportunit{'y' if len(r['data']) == 1 else 'ies'} shown at or above {format_inr(threshold)} (largest first)."
        top = r["data"][0]
        return AgentResponse(f"{note} Largest: {top['company']} — {format_inr(top['deal_value'])} at {top['stage']}.",
                             render_evidence(self.tk.calls), f"{top['next_action']}: {top['next_action_why']}")

    def _h_overdue(self, q, focus):
        r = self.tk.get_followups_due(0, 50)
        if not r["found"]:
            return AgentResponse("No follow-ups are overdue or due today.", supporting="Checked all leads with a pending follow-up date.",
                                 action="Review the Lead Priority tab for leads to nurture.")
        ranked = sorted(r["data"], key=lambda x: (x["follow_up_state"] != "overdue", -x["score"]))
        top = ranked[0]
        self.tk.calls[-1]["result"]["data"] = ranked[:8]
        return AgentResponse(f"{r['total_due']} lead(s) have a follow-up that is overdue or due today. "
                             f"Highest-priority first: {top['company']} (follow-up due {top['follow_up_date']}, score {top['score']}).",
                             render_evidence(self.tk.calls), f"Contact {top['company']} first: {top['next_action_why']}")

    def _h_pipeline(self, q, focus):
        r = self.tk.get_pipeline()
        if not r["found"]:
            return self._no_data()
        d = r["data"]
        return AgentResponse(f"Open pipeline value is {format_inr(d['open_pipeline_value_inr'])} across {d['active_opportunities']} active opportunities; "
                             f"won revenue to date is {format_inr(d['won_revenue_inr'])}.", render_evidence(self.tk.calls),
                             "Focus on Proposal and Negotiation deals to convert pipeline into revenue.")

    def _h_campaign(self, q, focus):
        r = self.tk.get_campaign_performance(5)
        if not r["found"]:
            return self._no_data()
        top = r["data"][0]
        return AgentResponse(f"“{top['campaign']}” ({top['channel']}) generated the most leads: {top['leads']}.",
                             render_evidence(self.tk.calls),
                             f"Review what worked in this campaign and repeat it on {top['channel']}.")

    def _h_why(self, q, focus):
        lid, _ = self._resolve_lead(q, focus)
        if lid is None:
            return self._need_lead()
        r = self.tk.calculate_lead_score(lid)
        self.tk.get_lead(lid)
        if not r["found"]:
            return self._no_data()
        d = r["data"]
        lead = self.tk.calls[-1]["result"]["data"]
        return AgentResponse(f"{d['company']} is {d['priority']} (score {d['score']}/100) because of: {'; '.join(d['reasons'])}.",
                             render_evidence(self.tk.calls[:1]) + "\n\n" + md_kv({"stage": (lead["opportunity"] or {}).get("stage", "No opportunity"),
                                 "deal_value": (lead["opportunity"] or {}).get("value_inr") or 0, "days_since_contact": lead["days_since_contact"]}),
                             f"{lead['next_action']['action']}: {lead['next_action']['why']}")

    def _h_summary(self, q, focus):
        lid, _ = self._resolve_lead(q, focus)
        if lid is None:
            return self._need_lead()
        s = self.tk.summarize_activity(lid)
        self.tk.get_lead(lid)
        hist = self.tk.get_lead_history(lid, 6)
        if not s["found"]:
            return self._no_data()
        d, lead = s["data"], self.tk.calls[1]["result"]["data"]
        types = ", ".join(f"{n} {t.lower()}" for t, n in sorted(d["by_type"].items(), key=lambda x: -x[1]))
        fu = d["pending_follow_up"]
        fu_txt = f" A follow-up is {fu['state'].replace('_', ' ')} ({fu['date']})." if fu and fu.get("date") else ""
        ans = (f"{d['company']}: {d['total_activities']} recorded activities ({types}) between {d['first_activity']} and {d['last_activity']}; "
               f"last contact {ago(d['days_since_contact'])}.{fu_txt}")
        sup = md_table(hist["data"]) if hist["found"] else ""
        return AgentResponse(ans, sup, f"{lead['next_action']['action']}: {lead['next_action']['why']}")

    def _h_email(self, q, focus):
        lid, _ = self._resolve_lead(q, focus)
        if lid is None:
            return self._need_lead()
        goal = "Follow up on proposal" if "proposal" in q.lower() else "Follow up after meeting" if "meeting" in q.lower() else "Check in on next steps"
        use_ai = gemini_client.is_configured()
        from services.email_service import generate_email
        res = generate_email(self.db, lid, goal, today=self.today, use_ai=use_ai)
        note = ""
        if use_ai and not res["ok"]:                      # Gemini failed -> still give a useful, honest template
            note = f"Gemini problem: {res['message']} Showing a plain template built from CRM facts instead."
            use_ai = False
            res = generate_email(self.db, lid, goal, today=self.today, use_ai=False)
        elif not use_ai:
            note = f"{config.GEMINI_MISSING_MSG} Showing a plain template built from CRM facts."
        self.tk.calls.append({"tool": "generate_email", "args": {"lead_id": lid, "goal": goal}, "result": {"found": res["ok"], "data": None}})
        self.tk.get_lead(lid)
        if not res["ok"]:
            return AgentResponse(res["message"], intent="email")
        r = AgentResponse("Here is a draft for your review. Nothing has been sent.", render_evidence(self.tk.calls),
                          "Edit the draft in the AI Email tab, then send it from your own email client.", ai_used=use_ai,
                          draft={"subject": res["subject"], "body": res["body"]})
        r.notice = note
        return r

    def _h_social(self, q, focus):
        from services.marketing_service import generate_content
        ql = q.lower()
        kind = "Content calendar" if "calendar" in ql else "Campaign ideas" if "campaign idea" in ql else \
               "Short social post" if re.search(r"short|tweet|caption", ql) else "LinkedIn post"
        m = re.search(r"\babout\b\s+(.+)$", q, re.I)
        topic = (m.group(1) if m else "sales automation for growing businesses").strip(" .?!")
        use_ai = gemini_client.is_configured()
        res = generate_content(self.db, kind, topic, use_ai=use_ai)
        note = ""
        if use_ai and not res["ok"]:
            note = f"Gemini problem: {res['message']} Showing a template draft instead."
            use_ai = False
            res = generate_content(self.db, kind, topic, use_ai=False)
        elif not use_ai:
            note = f"{config.GEMINI_MISSING_MSG} Showing a simple template draft (no AI)."
        self.tk.calls.append({"tool": "generate_social_post", "args": {"topic": topic, "kind": kind}, "result": {"found": res["ok"], "data": None}})
        if not res["ok"]:
            return AgentResponse(res["message"])
        r = AgentResponse(f"Here is a {kind.lower()} draft about “{topic}”. Nothing has been published.", "",
                          "Edit it in the Marketing tab, then use “Open in LinkedIn / X” and press Post yourself.", ai_used=use_ai,
                          draft={"body": res["text"]})
        r.notice = note
        return r

    def _h_filter(self, q, focus):
        f = parse_filters(q, self.tk.frame)
        r = self.tk.query_leads(**{k: v for k, v in f.items()}, limit=8)
        crit = ", ".join(f"{k.replace('_', ' ')}: {format_inr(v) if 'value' in k else v}" for k, v in f.items()) or "all leads"
        if not r["found"]:
            return AgentResponse(f"No leads match these criteria ({crit}).", f"Checked {len(self.tk.frame)} leads in the CRM.",
                                 "Try fewer filters or check the CRM tab.")
        d = r["data"]
        top = d["leads"][0]
        return AgentResponse(f"{d['total_matches']} lead(s) match ({crit}). Highest score: {top['company']} ({top['priority']}, {top['score']}).",
                             render_evidence(self.tk.calls), f"{top['next_action']}: {top['next_action_why']}")

    def _h_general(self, q, focus):
        lid, _ = self._resolve_lead(q, focus)
        if lid is not None:
            return self._h_summary(q, focus)
        if gemini_client.is_configured():
            try:
                text = gemini_client.generate_with_tools(q, self.tk.gemini_functions(), SYSTEM_PROMPT)
            except gemini_client.GeminiError as exc:
                return AgentResponse(INSUFFICIENT_DATA, notice=f"Gemini problem: {exc}")
            if self.tk.calls and text:   # retrieve-before-answer: discard answers produced without CRM data
                return AgentResponse(text, render_evidence(self.tk.calls), "", ai_used=True)
            return self._no_data()
        words = [w for w in re.findall(r"[A-Za-z]{4,}", q) if w.lower() not in {"which", "what", "show", "tell", "about", "with", "have", "that", "this"}]
        for w in words:
            r = self.tk.search_leads(w, 5)
            if r["found"]:
                return AgentResponse(f"Found {len(r['data'])} lead(s) matching “{w}”.", render_evidence(self.tk.calls),
                                     f"{r['data'][0]['next_action']}: {r['data'][0]['next_action_why']}")
        return AgentResponse(INSUFFICIENT_DATA, action="Try: " + " | ".join(EXAMPLES[:4]))
