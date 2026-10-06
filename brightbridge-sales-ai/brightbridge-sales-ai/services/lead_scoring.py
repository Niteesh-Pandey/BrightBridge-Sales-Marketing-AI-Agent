"""Deterministic lead scoring (0-100). Gemini never calculates scores.

Factor weights (sum = 100):
    engagement 20 | recency 20 | deal value 15 | stage 15 | activity count 10
    email engagement 8 | meeting activity 7 | follow-up status 5
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from services.utils import parse_date

CALL_NOW, FOLLOW_UP, NURTURE, LOW, CLOSED = "CALL NOW", "FOLLOW UP SOON", "NURTURE", "LOW PRIORITY", "CLOSED"
MAX_POINTS = {"engagement": 20, "recency": 20, "deal_value": 15, "stage": 15, "activity_count": 10,
              "email_engagement": 8, "meeting_activity": 7, "follow_up_status": 5}
STAGE_POINTS = {"Negotiation": 15, "Proposal": 13, "Qualification": 8, "Prospecting": 4}
ENGAGEMENT_WEIGHTS = {"Email Replied": 4, "Meeting": 4, "Demo": 4, "Call": 3, "Email Opened": 1.5, "Email Sent": 0.5, "Note": 0}


@dataclass
class ScoreResult:
    score: int
    priority: str
    breakdown: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


def classify(score: int) -> str:
    if score >= 80:
        return CALL_NOW
    if score >= 60:
        return FOLLOW_UP
    if score >= 40:
        return NURTURE
    return LOW


def pending_followup(activities: list[dict], today: date) -> tuple[date | None, str]:
    """Latest follow-up date is pending unless a later activity has happened since it was set."""
    dated = [(parse_date(a["activity_date"]), parse_date(a.get("follow_up_date"))) for a in activities]
    dated = [(d, f) for d, f in dated if d]
    if not dated:
        return None, "none"
    last_activity = max(d for d, _ in dated)
    with_fu = [(d, f) for d, f in dated if f]
    if not with_fu:
        return None, "none"
    set_on, fu = max(with_fu, key=lambda x: (x[0], x[1]))
    if last_activity > set_on:
        return None, "none"
    state = "overdue" if fu < today else "due_today" if fu == today else "upcoming"
    return fu, state


def _value_points(value: float) -> int:
    for threshold, pts in ((500_000, 15), (200_000, 12), (100_000, 8), (50_000, 5), (1, 2)):
        if value >= threshold:
            return pts
    return 0


def _recency_points(days) -> int:
    if days is None:
        return 0
    for limit, pts in ((3, 20), (7, 16), (14, 11), (30, 6), (60, 3)):
        if days <= limit:
            return pts
    return 0


def score_lead(activities: list[dict], opportunity: dict | None, lead_status: str, today: date) -> ScoreResult:
    stage = opportunity["stage"] if opportunity else None
    if stage in ("Closed Won", "Closed Lost"):
        return ScoreResult(0, CLOSED, {k: 0 for k in MAX_POINTS}, [f"Opportunity is {stage}; no active selling needed."])

    acts = [(parse_date(a["activity_date"]), a["type"]) for a in activities]
    acts = [(d, t) for d, t in acts if d]
    last = max((d for d, _ in acts), default=None)
    days_since = (today - last).days if last else None

    d30, d60, d90 = (today - timedelta(days=n) for n in (30, 60, 90))
    engagement = min(20, sum(ENGAGEMENT_WEIGHTS.get(t, 0) for d, t in acts if d >= d30))
    replies = sum(1 for d, t in acts if t == "Email Replied" and d >= d60)
    opens = sum(1 for d, t in acts if t == "Email Opened" and d >= d60)
    email_eng = min(8, replies * 4 + opens * 1.5)
    meet_recent = any(t in ("Meeting", "Demo") and d >= d30 for d, t in acts)
    meet_mid = any(t in ("Meeting", "Demo") and d >= d90 for d, t in acts)
    meet_any = any(t in ("Meeting", "Demo") for _, t in acts)
    meeting = 7 if meet_recent else 4 if meet_mid else 2 if meet_any else 0
    _, fu_state = pending_followup(activities, today)
    fu_pts = 5 if fu_state in ("overdue", "due_today") else 3 if fu_state == "upcoming" else 0
    value = float(opportunity["value_inr"]) if opportunity else 0.0

    breakdown = {
        "engagement": round(engagement, 1), "recency": _recency_points(days_since), "deal_value": _value_points(value),
        "stage": STAGE_POINTS.get(stage, 2), "activity_count": min(10, len(acts)),
        "email_engagement": round(email_eng, 1), "meeting_activity": meeting, "follow_up_status": fu_pts,
    }
    score = int(round(min(100, sum(breakdown.values()))))
    reasons = []
    if breakdown["recency"] >= 16:
        reasons.append("recent interaction (today)" if days_since == 0 else f"recent interaction ({days_since} day{'' if days_since == 1 else 's'} ago)")
    elif days_since is None or days_since > 30:
        reasons.append("no contact in the last 30 days" if days_since is not None else "no contact recorded")
    if breakdown["engagement"] >= 10:
        reasons.append("high recent engagement")
    if stage in ("Proposal", "Negotiation"):
        reasons.append(f"active {stage.lower()} stage")
    if breakdown["deal_value"] >= 12:
        reasons.append("high deal value")
    if meet_recent:
        reasons.append("recent meeting/demo")
    if fu_state in ("overdue", "due_today"):
        reasons.append("follow-up is due" if fu_state == "due_today" else "follow-up is overdue")
    if lead_status == "Unqualified":
        score = min(score, 39)
        reasons.append("lead marked Unqualified (score capped at 39)")
    return ScoreResult(score, classify(score), breakdown, reasons or ["limited signals in CRM data"])
