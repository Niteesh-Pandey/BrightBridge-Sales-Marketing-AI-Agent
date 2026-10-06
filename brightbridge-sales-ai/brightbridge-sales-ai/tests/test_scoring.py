from datetime import date, timedelta

import pytest

from services.crm_service import filter_leads, get_followups, get_lead_detail, get_lead_frame
from services.lead_scoring import CALL_NOW, CLOSED, LOW, NURTURE, classify, pending_followup, score_lead
from services.sales_service import recommend_next_action
from services.utils import format_inr

T = date(2026, 10, 1)


def act(days_ago, typ="Call", fu=None):
    return {"type": typ, "activity_date": (T - timedelta(days=days_ago)).isoformat(),
            "follow_up_date": (T + timedelta(days=fu)).isoformat() if fu is not None else None}


HOT_OPP = {"stage": "Negotiation", "value_inr": 420000}


def test_classification_bands():
    assert [classify(s) for s in (100, 80, 79, 60, 59, 40, 39, 0)] == [
        "CALL NOW", "CALL NOW", "FOLLOW UP SOON", "FOLLOW UP SOON", "NURTURE", "NURTURE", "LOW PRIORITY", "LOW PRIORITY"]


def test_hot_lead_scores_call_now():
    acts = [act(1, "Meeting"), act(2, "Email Replied"), act(3, "Call"), act(4, "Email Opened"), act(6, "Demo", fu=-1)]
    r = score_lead(acts, HOT_OPP, "Qualified", T)
    assert r.priority == CALL_NOW and 80 <= r.score <= 100


def test_cold_lead_is_low_priority():
    r = score_lead([act(120, "Email Sent")], {"stage": "Prospecting", "value_inr": 40000}, "Contacted", T)
    assert r.priority == LOW and r.score < 40


def test_score_is_deterministic_and_bounded():
    acts = [act(i, "Meeting") for i in range(15)]
    a, b = score_lead(acts, HOT_OPP, "New", T), score_lead(acts, HOT_OPP, "New", T)
    assert a == b and 0 <= a.score <= 100


def test_breakdown_sums_to_score():
    r = score_lead([act(2, "Call"), act(5, "Email Opened")], HOT_OPP, "New", T)
    assert round(sum(r.breakdown.values())) == r.score


def test_closed_opportunity_is_closed():
    assert score_lead([act(1)], {"stage": "Closed Won", "value_inr": 1}, "Qualified", T).priority == CLOSED


def test_unqualified_is_capped():
    acts = [act(1, "Meeting"), act(1, "Email Replied"), act(2, "Call")]
    assert score_lead(acts, HOT_OPP, "Unqualified", T).score <= 39


def test_more_value_never_lowers_score():
    base = [act(5, "Call")]
    low = score_lead(base, {"stage": "Proposal", "value_inr": 40000}, "New", T).score
    high = score_lead(base, {"stage": "Proposal", "value_inr": 600000}, "New", T).score
    assert high >= low


def test_pending_followup_states():
    assert pending_followup([act(5, fu=-2)], T)[1] == "overdue"
    assert pending_followup([act(5, fu=0)], T)[1] == "due_today"
    assert pending_followup([act(1, fu=5)], T)[1] == "upcoming"
    assert pending_followup([act(5, fu=-2), act(1, "Note")], T)[1] == "none"   # later contact supersedes
    assert pending_followup([], T)[1] == "none"


def row(**kw):
    base = {"stage": "Qualification", "deal_value": 100000, "days_since_contact": 10, "last_activity_type": "Call",
            "follow_up_date": None, "follow_up_state": "none"}
    return {**base, **kw}


@pytest.mark.parametrize("kw,expected", [
    ({"follow_up_state": "overdue", "follow_up_date": "2026-09-20"}, "Contact immediately"),
    ({"stage": "Negotiation", "deal_value": 320000}, "Prioritize sales call"),
    ({"last_activity_type": "Meeting", "days_since_contact": 1}, "Send meeting follow-up"),
    ({"stage": "Proposal", "days_since_contact": 4}, "Follow up on proposal"),
    ({"days_since_contact": 75}, "Start re-engagement"),
])
def test_next_action_rules(kw, expected):
    assert recommend_next_action(row(**kw))["action"] == expected


def test_every_recommendation_has_why_and_source():
    r = recommend_next_action(row())
    assert r["why"] and r["source_data"]["Stage"]


def test_format_inr_indian_grouping():
    assert format_inr(320000) == "₹3,20,000" and format_inr(4200000) == "₹42,00,000" and format_inr(950) == "₹950"
    assert format_inr(None) == "—"


def test_frame_covers_all_leads(demo_db, today):
    df = get_lead_frame(demo_db, today)
    assert len(df) == demo_db.count("leads")
    assert df["score"].between(0, 100).all() and set(df["priority"]) <= {"CALL NOW", "FOLLOW UP SOON", "NURTURE", "LOW PRIORITY", "CLOSED"}
    assert (df["next_action_why"].str.len() > 0).all()


def test_filters(demo_db, today):
    df = get_lead_frame(demo_db, today)
    ind = df["industry"].iloc[0]
    assert (filter_leads(df, industry=ind)["industry"] == ind).all()
    assert (filter_leads(df, min_value=300000)["deal_value"] >= 300000).all()
    assert filter_leads(df, search="zzzz-no-match").empty
    assert (filter_leads(df, priority="CALL NOW")["priority"] == "CALL NOW").all()


def test_detail_and_followups(demo_db, today):
    df = get_lead_frame(demo_db, today)
    d = get_lead_detail(demo_db, int(df.iloc[0]["lead_id"]), today)
    assert d["score"] == int(df.iloc[0]["score"]) and d["activities"] is not None
    assert get_lead_detail(demo_db, 99999, today) is None
    fu = get_followups(df, today=today)
    assert fu["follow_up_state"].isin(["overdue", "due_today"]).all()
