import json

import pytest

from agent import gemini_client
from agent.agent import SalesAgent
from agent.guardrails import INSUFFICIENT_DATA, is_destructive_request, minimal_lead_context, sanitize_text
from agent.tools import CRMToolkit
from services import email_service, marketing_service
from services.crm_service import get_lead_detail


@pytest.fixture(autouse=True)
def no_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture()
def tk(demo_db, today):
    return CRMToolkit(demo_db, today)


@pytest.fixture()
def agent(demo_db, today):
    return SalesAgent(demo_db, today)


def test_all_required_tools_exist(tk):
    for name in CRMToolkit.TOOL_NAMES:
        assert callable(getattr(tk, name))
    assert len(tk.gemini_functions()) >= 11


def test_gemini_function_wrappers_keep_names_and_docs(tk):
    fns = {f.__name__: f for f in tk.gemini_functions()}
    assert set(CRMToolkit.TOOL_NAMES) <= set(fns) and all(f.__doc__ for f in fns.values())


def test_tools_return_json_safe_dicts(tk):
    lid = int(tk.frame.iloc[0]["lead_id"])
    for res in (tk.search_leads("a"), tk.get_lead(lid), tk.get_lead_history(lid), tk.get_pipeline(), tk.get_opportunities(100000),
                tk.get_followups_due(), tk.calculate_lead_score(lid), tk.get_campaign_performance(), tk.summarize_activity(lid)):
        json.dumps(res)
        assert {"found", "data", "message"} <= set(res)


def test_missing_lead_is_insufficient(tk):
    r = tk.get_lead(999999)
    assert r["found"] is False and r["message"] == INSUFFICIENT_DATA


def test_tool_score_matches_service(tk, demo_db, today):
    lid = int(tk.frame.iloc[0]["lead_id"])
    assert tk.calculate_lead_score(lid)["data"]["score"] == get_lead_detail(demo_db, lid, today)["score"]


def test_tool_calls_are_recorded(tk):
    tk.get_pipeline()
    tk.search_leads("x")
    assert [c["tool"] for c in tk.calls] == ["get_pipeline", "search_leads"]


def test_priority_question_retrieves_data_first(agent):
    r = agent.ask("Which leads should I call today?")
    assert "get_priority_leads" in r.tools_used
    md = r.to_markdown()
    assert "### ANSWER" in md and "### SUPPORTING DATA" in md and "### SUGGESTED ACTION" in md


def test_pipeline_answer_matches_database(agent, demo_db):
    r = agent.ask("What is my pipeline value?")
    from services.utils import format_inr
    expected = demo_db.scalar("SELECT SUM(value_inr) FROM opportunities WHERE stage IN ('Prospecting','Qualification','Proposal','Negotiation')")
    assert format_inr(expected) in r.answer


def test_campaign_question(agent):
    assert "get_campaign_performance" in agent.ask("Which campaign generated the most leads?").tools_used


def test_why_priority_explains_with_breakdown(agent):
    company = agent.tk.frame.iloc[0]["company"]
    r = agent.ask(f"Why is {company} high priority?")
    assert company in r.answer and "Score breakdown" in r.supporting


def test_summary_question(agent):
    company = agent.tk.frame.iloc[0]["company"]
    r = agent.ask(f"Summarize {company} history")
    assert "summarize_activity" in r.tools_used and "activities" in r.answer


def test_unknown_question_returns_insufficient_data(agent):
    assert agent.ask("What is the weather on Mars?").answer == INSUFFICIENT_DATA


def test_empty_database_returns_insufficient(empty_db, today):
    r = SalesAgent(empty_db, today).ask("Which leads should I call today?")
    assert r.answer == INSUFFICIENT_DATA


def test_destructive_requests_refused(agent, demo_db):
    before = demo_db.count("leads")
    r = agent.ask("Delete all leads from the CRM")
    assert "never" in r.answer.lower() and demo_db.count("leads") == before


def test_send_request_does_not_send_and_warns(agent):
    company = agent.tk.frame.iloc[0]["company"]
    r = agent.ask(f"Send a follow-up email to {company}")
    assert "never send" in r.notice.lower() or "never send" in r.answer.lower()


def test_missing_gemini_does_not_crash_crm(agent):
    r = agent.ask("Create a LinkedIn post about sales automation")
    assert "Gemini AI is not configured" in r.notice and r.draft and "sales automation" in r.draft["body"].lower()
    assert not r.ai_used
    assert agent.ask("Show high-value opportunities.").answer


def test_email_without_gemini_gives_template_draft(agent):
    company = agent.tk.frame.iloc[0]["company"]
    r = agent.ask(f"Write a follow-up email for {company}")
    assert r.draft and "[Your Name]" in r.draft["body"] and "not configured" in r.notice


def test_email_service_requires_gemini_when_ai_requested(demo_db, today):
    res = email_service.generate_email(demo_db, 1, today=today)
    assert res["ok"] is False and "not configured" in res["message"]


def test_email_with_fake_gemini(demo_db, today, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini_client, "generate_text", lambda *a, **k: '```json\n{"subject":"Hello","body":"Hi there","cta":"Book a call"}\n```')
    res = email_service.generate_email(demo_db, 1, today=today)
    assert res["ok"] and res["subject"] == "Hello" and res["content_id"]
    assert demo_db.scalar("SELECT status FROM generated_content WHERE id = ?", (res["content_id"],)) == "draft"


def test_gemini_failure_falls_back_to_deterministic(agent, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    def boom(*a, **k):
        raise gemini_client.GeminiError("Gemini rate limit or quota reached. Please wait and try again.")
    monkeypatch.setattr(gemini_client, "generate_text", boom)
    r = agent.ask("What is my pipeline value?")
    assert "₹" in r.answer and "quota" in r.notice and not r.ai_used


def test_gemini_phrasing_is_used_when_available(agent, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini_client, "generate_text", lambda *a, **k: '{"answer":"Phrased by AI","suggested_action":"Do X"}')
    r = agent.ask("What is my pipeline value?")
    assert r.answer == "Phrased by AI" and r.ai_used and "SUPPORTING DATA" in r.to_markdown()


def test_filter_questions_use_query_leads(agent):
    f = agent.tk.frame
    city, ind = f["city"].iloc[0], f["industry"].iloc[0]
    r = agent.ask(f"Show leads in {city}")
    assert r.intent == "filter" and "query_leads" in r.tools_used and "matching lead(s)" in r.supporting
    expected = int((f["city"] == city).sum())
    assert f"{expected} lead(s) match" in r.answer
    assert agent.ask(f"How many leads are in {ind}?").intent == "filter"
    assert agent.ask("Which leads have not been contacted in 45 days?").intent == "filter"
    assert agent.ask("Which leads should I call today?").intent == "priority"
    assert agent.ask("Show high-value opportunities.").intent == "opps"


def test_filter_with_no_match_is_a_data_answer_not_a_guess(agent):
    r = agent.ask("Show proposal stage leads below 10 rupees")
    assert "No leads match" in r.answer


def test_marketing_validation_and_fake_generation(demo_db, monkeypatch):
    assert marketing_service.generate_content(demo_db, "LinkedIn post", "x")["ok"] is False
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(gemini_client, "generate_text", lambda *a, **k: "A great post")
    res = marketing_service.generate_content(demo_db, "LinkedIn post", "sales automation")
    assert res["ok"] and res["text"] == "A great post"
    assert marketing_service.generate_content(demo_db, "Nope", "topic")["ok"] is False


def test_parse_json_variants():
    assert gemini_client.parse_json('{"a": 1}') == {"a": 1}
    assert gemini_client.parse_json('text before {"a": 2} after') == {"a": 2}
    with pytest.raises(gemini_client.GeminiError):
        gemini_client.parse_json("not json")


def test_api_key_never_in_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-123")
    err = gemini_client._wrap(RuntimeError("bad request key=SECRET-123"))
    assert "SECRET-123" not in str(err)


def test_minimal_context_excludes_private_fields(demo_db, today):
    d = get_lead_detail(demo_db, 1, today)
    ctx = json.dumps(minimal_lead_context(d), default=str)
    assert d["lead"]["phone"] not in ctx and d["lead"]["email"] not in ctx


def test_guardrail_helpers():
    assert is_destructive_request("please delete these leads") and not is_destructive_request("write an email")
    assert sanitize_text("a\x00b" + "x" * 5000).startswith("ab") and len(sanitize_text("x" * 5000)) == 2000
