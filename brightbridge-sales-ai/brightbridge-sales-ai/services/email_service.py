"""AI email drafting. Drafts only: emails are NEVER sent automatically."""
from __future__ import annotations

import json
from datetime import date, datetime

import config
from agent import gemini_client
from agent.guardrails import INSUFFICIENT_DATA, minimal_lead_context, safe_error_message, sanitize_text
from agent.prompts import EMAIL_PROMPT, SYSTEM_PROMPT
from database.db import Database
from services.crm_service import get_lead_detail
from services.utils import ago, format_inr

GOALS = ["Follow up on proposal", "Follow up after meeting", "Re-engage after silence", "Introduce BrightBridge",
         "Check in on next steps"]


def save_draft(db: Database, content_type: str, title: str, body: str, lead_id: int | None = None) -> int:
    return db.execute(
        "INSERT INTO generated_content (content_type, lead_id, title, body, status, created_at) VALUES (?,?,?,?,?,?)",
        (content_type, lead_id, title[:200], body, "draft", datetime.now().isoformat(timespec="seconds")))


def template_email(detail: dict) -> dict:
    """Plain deterministic draft built only from CRM facts (used when Gemini is unavailable)."""
    lead, company, opp = detail["lead"], detail["company"], detail.get("best_opportunity")
    first = (lead.get("contact") or "there").split()[0]
    topic = opp["name"].split(" - ")[0] if opp else "how BrightBridge can help streamline repetitive work"
    contact_line = f"We last connected {ago(detail['days_since_contact'])}" if detail.get("days_since_contact") is not None else "I wanted to reach out"
    lines = [f"Hi {first},", "", f"{contact_line}, and I wanted to follow up on {topic} for {company['name']}.",
             ""]
    if opp:
        lines.append(f"Our current discussion is at the {opp['stage']} stage ({format_inr(opp['value_inr'])}). I would be glad to help with any open questions.")
        lines.append("")
    lines += ["Would you be open to a short call this week to agree on next steps?", "", "Regards,", "[Your Name]", "BrightBridge Solutions"]
    return {"subject": f"Following up: {topic}", "body": "\n".join(lines), "cta": "Propose a 20-minute call this week."}


def generate_email(db: Database, lead_id: int, goal: str = GOALS[0], instructions: str = "",
                   today: date | None = None, use_ai: bool = True, save: bool = True) -> dict:
    """Returns {ok, ai, subject, body, cta, message, content_id}. Sends nothing."""
    detail = get_lead_detail(db, int(lead_id), today)
    if not detail:
        return {"ok": False, "message": INSUFFICIENT_DATA}
    if use_ai and not gemini_client.is_configured():
        return {"ok": False, "ai": False, "message": config.GEMINI_MISSING_MSG}
    try:
        if use_ai:
            prompt = EMAIL_PROMPT.format(goal=sanitize_text(goal, 120), instructions=sanitize_text(instructions, 500) or "none",
                                         context=json.dumps(minimal_lead_context(detail), default=str, indent=1))
            data = gemini_client.parse_json(gemini_client.generate_text(prompt, SYSTEM_PROMPT, temperature=0.5))
            email = {"subject": str(data.get("subject", "")).strip(), "body": str(data.get("body", "")).strip(),
                     "cta": str(data.get("cta", "")).strip()}
            if not email["subject"] or not email["body"]:
                raise gemini_client.GeminiError("Gemini returned an incomplete email. Please try again.")
        else:
            email = template_email(detail)
    except gemini_client.GeminiError as exc:
        return {"ok": False, "ai": use_ai, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "ai": use_ai, "message": safe_error_message(exc, "email draft")}
    out = {"ok": True, "ai": use_ai, **email, "message": "Draft ready. Review and edit before using it; nothing has been sent."}
    if save:
        out["content_id"] = save_draft(db, "email", email["subject"], f"Subject: {email['subject']}\n\n{email['body']}\n\nSuggested CTA: {email['cta']}", int(lead_id))
    return out
