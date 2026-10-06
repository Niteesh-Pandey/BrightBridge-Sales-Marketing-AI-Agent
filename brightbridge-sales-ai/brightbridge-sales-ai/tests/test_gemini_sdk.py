"""Exercises the REAL google-genai SDK against a local fake server (no internet, no real key)."""
import pytest

from agent import gemini_client as g
from agent.agent import SalesAgent
from agent.tools import CRMToolkit
from tests.fake_gemini import FakeGemini


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    g.reset_state()
    yield
    g.reset_state()


def serve(monkeypatch, **kw):
    srv = FakeGemini(**kw)
    monkeypatch.setenv("GEMINI_API_BASE_URL", srv.url)
    return srv


def test_status_unknown_until_verified_then_ok(monkeypatch):
    assert g.status()["state"] == "unknown"             # key present but NOT verified
    with serve(monkeypatch):
        r = g.test_connection()
    assert r["ok"] and g.status()["state"] == "ok" and "Connected" in g.status()["message"]


def test_status_not_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    assert g.status()["state"] == "not_configured" and g.test_connection()["ok"] is False


def test_invalid_key_reports_error_not_connected(monkeypatch):
    with serve(monkeypatch, bad_key=True):
        r = g.test_connection()
    assert r["ok"] is False and "rejected the API key" in r["message"]
    assert g.status()["state"] == "error" and "test-key-123" not in g.status()["message"]


def test_model_fallback_when_first_model_missing(monkeypatch):
    with serve(monkeypatch, missing_models=["gemini-flash-latest"]) as srv:
        assert g.generate_text("hi") == "Hello from fake Gemini"
    assert [r["model"] for r in srv.posts()][:2] == ["gemini-flash-latest", "gemini-3.8-flash"]


def test_model_discovery_when_all_known_models_missing(monkeypatch):
    with serve(monkeypatch, missing_models=g.MODEL_FALLBACKS) as srv:
        assert g.generate_text("hi") == "Hello from fake Gemini"
    assert srv.posts()[-1]["model"] == "gemini-9-flash"   # discovered via models.list, image variant skipped


def test_function_calling_round_trip_with_real_toolkit(monkeypatch, demo_db, today):
    tk = CRMToolkit(demo_db, today)
    with serve(monkeypatch, text="Pipeline looks healthy.", tool_call=("get_pipeline", {})) as srv:
        out = g.generate_with_tools("what is my pipeline?", tk.gemini_functions(), "system")
    assert out == "Pipeline looks healthy."
    assert [c["tool"] for c in tk.calls] == ["get_pipeline"]          # the SDK really executed our tool
    assert len(srv.posts()) == 2                                       # request -> functionCall -> functionResponse -> answer
    decls = srv.posts()[0]["body"]["tools"][0]["functionDeclarations"]
    assert {d["name"] for d in decls} >= set(CRMToolkit.TOOL_NAMES)


def test_tool_arguments_are_converted_and_executed(monkeypatch, demo_db, today):
    tk = CRMToolkit(demo_db, today)
    with serve(monkeypatch, text="done", tool_call=("get_opportunities", {"min_value": 300000, "stage": "Proposal", "limit": 3})) as srv:
        g.generate_with_tools("big proposals?", tk.gemini_functions(), "system")
    call = tk.calls[0]
    assert call["tool"] == "get_opportunities" and call["args"] == {"min_value": 300000, "stage": "Proposal", "limit": 3}
    assert call["result"]["found"] and all(r["stage"] == "Proposal" for r in call["result"]["data"])
    sent = str(srv.posts()[1]["body"]["contents"])
    assert "error" not in sent.lower() or "found" in sent


def test_agent_free_form_question_uses_gemini_tools(monkeypatch, demo_db, today):
    agent = SalesAgent(demo_db, today)
    with serve(monkeypatch, text="Answer from tools", tool_call=("get_campaign_performance", {"limit": 3})):
        r = agent.ask("Tell me something interesting about zzqqxx")
    assert r.ai_used and "get_campaign_performance" in r.tools_used and "SUPPORTING DATA" in r.to_markdown()


def test_agent_discards_gemini_answer_without_tool_calls(monkeypatch, demo_db, today):
    with serve(monkeypatch, text="I made this up"):            # no tool_call -> no CRM retrieval
        r = SalesAgent(demo_db, today).ask("Tell me something interesting about zzqqxx")
    assert r.answer == "Insufficient data to determine this."


def test_agent_shows_real_gemini_error_to_user(monkeypatch, demo_db, today):
    with serve(monkeypatch, bad_key=True):
        r = SalesAgent(demo_db, today).ask("Tell me something interesting about zzqqxx")
    assert "rejected the API key" in r.notice


def test_email_and_post_generation_through_sdk(monkeypatch, demo_db, today):
    from services import email_service, marketing_service
    reply = '{"subject":"Next steps","body":"Hi Asha, following up.","cta":"Book 20 minutes"}'
    with serve(monkeypatch, text=reply):
        e = email_service.generate_email(demo_db, 1, today=today)
    assert e["ok"] and e["subject"] == "Next steps"
    with serve(monkeypatch, text="Automation saves hours."):
        m = marketing_service.generate_content(demo_db, "LinkedIn post", "automation")
    assert m["ok"] and "Automation" in m["text"]
