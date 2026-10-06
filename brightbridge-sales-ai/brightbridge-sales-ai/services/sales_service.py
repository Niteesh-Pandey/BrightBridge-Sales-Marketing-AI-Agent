"""Deterministic next-best-action engine. Every recommendation carries WHY and SOURCE DATA."""
from __future__ import annotations

from services.utils import ago, format_inr


def recommend_next_action(row: dict) -> dict:
    """row: a lead-frame record. Returns {action, why, source_data}."""
    stage = row.get("stage")
    days = row.get("days_since_contact")
    days = None if days is None or days != days else int(days)
    value = float(row.get("deal_value") or 0)
    src = {"Stage": stage or "No opportunity", "Deal Value": format_inr(value) if value else "—",
           "Last Contact": ago(days), "Last Activity": row.get("last_activity_type") or "—",
           "Follow-up Date": row.get("follow_up_date") or "—", "Follow-up State": row.get("follow_up_state", "none")}

    def out(action, why):
        return {"action": action, "why": why, "source_data": src}

    if stage in ("Closed Won", "Closed Lost"):
        return out("No action needed", f"Opportunity is {stage}.")
    if row.get("follow_up_state") == "overdue":
        return out("Contact immediately", f"A follow-up was due on {row.get('follow_up_date')} and no contact has happened since.")
    if row.get("follow_up_state") == "due_today":
        return out("Contact today", "A follow-up is scheduled for today.")
    if stage == "Negotiation" and value >= 200_000:
        return out("Prioritize sales call", f"High-value negotiation ({format_inr(value)}); closing momentum matters.")
    if row.get("last_activity_type") in ("Meeting", "Demo") and days is not None and days <= 3:
        return out("Send meeting follow-up", f"A {row['last_activity_type'].lower()} took place {ago(days)}.")
    if stage == "Proposal" and days is not None and days <= 14:
        return out("Follow up on proposal", f"Proposal is open and last contact was {ago(days)}.")
    if days is None or days > 30:
        return out("Start re-engagement", "No contact in the last 30 days." if days is not None else "No contact is recorded.")
    if stage == "Negotiation":
        return out("Call to resolve open terms", "Negotiation stage with recent contact.")
    if stage == "Proposal":
        return out("Follow up on proposal", "Proposal stage; keep the conversation moving.")
    if stage == "Qualification":
        return out("Schedule a discovery call", "Opportunity is in qualification.")
    return out("Send a helpful touchpoint", "Early-stage or no opportunity; keep nurturing.")
