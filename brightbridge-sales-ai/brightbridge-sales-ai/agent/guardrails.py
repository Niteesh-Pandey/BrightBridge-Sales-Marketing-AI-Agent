"""Guardrails: no-hallucination rule, privacy minimisation, human-approval rules, safe errors."""
from __future__ import annotations

import re

INSUFFICIENT_DATA = "Insufficient data to determine this."
MAX_INPUT_CHARS = 2000

_DESTRUCTIVE = re.compile(r"\b(delete|remove|drop|erase|wipe|modify|update|edit|change|overwrite|merge)\b.*\b(lead|leads|record|records|crm|opportunit\w*|contact|contacts|deal|deals|data|database)\b", re.I)
_SEND_PUBLISH = re.compile(r"\b(send|email it|mail it|publish|post it|tweet|schedule a post|auto-?send|auto-?post)\b", re.I)

HUMAN_APPROVAL_NOTE = ("I never send emails, publish posts, or change CRM records automatically. "
                       "I can prepare a draft for you to review, edit and use yourself.")


def sanitize_text(text: str | None, max_len: int = MAX_INPUT_CHARS) -> str:
    """Strip control characters and cap length."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or ""))
    return cleaned.strip()[:max_len]


def is_destructive_request(text: str) -> bool:
    return bool(_DESTRUCTIVE.search(text or ""))


def is_send_or_publish_request(text: str) -> bool:
    return bool(_SEND_PUBLISH.search(text or ""))


def safe_error_message(exc: Exception, area: str = "request") -> str:
    """Never leak stack traces, keys or connection strings."""
    name = type(exc).__name__
    return f"Sorry, the {area} could not be completed ({name}). Please try again; CRM features remain available."


def minimal_lead_context(detail: dict, max_activities: int = 8) -> dict:
    """Only the facts Gemini needs for ONE lead. No phone, no unrelated customers."""
    lead, company = detail["lead"], detail["company"]
    best = detail.get("best_opportunity") or {}
    return {
        "contact_name": lead.get("contact"), "contact_title": lead.get("job_title"),
        "company": company.get("name"), "industry": company.get("industry"), "city": company.get("city"),
        "lead_source": lead.get("source"), "lead_status": lead.get("status"),
        "opportunity": {"name": best.get("name"), "stage": best.get("stage"), "value_inr": best.get("value_inr"),
                        "expected_close_date": best.get("expected_close_date")} if best else None,
        "days_since_last_contact": detail.get("days_since_contact"),
        "pending_follow_up": detail.get("follow_up"),
        "priority": detail.get("priority"), "score": detail.get("score"),
        "recent_activities": [{"type": a["type"], "date": a["activity_date"], "outcome": a.get("outcome")}
                              for a in detail.get("activities", [])[:max_activities]],
    }
