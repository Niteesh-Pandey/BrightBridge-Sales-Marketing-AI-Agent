"""Settings tab: system health, data mode, persistence and privacy notes."""
from __future__ import annotations

import gradio as gr

import config
from services.health_service import check_health
from ui.common import Refreshable, safe


def build(ctx):
    """Returns (Refreshable, [events that should trigger a full-app refresh afterwards])."""
    gr.Markdown("### System health")
    health = gr.Dataframe(interactive=False, wrap=True, label=None)
    check_btn = gr.Button("Run health check", size="sm")
    gr.Markdown("### Gemini AI")
    with gr.Row():
        test_btn = gr.Button("Test Gemini connection (makes one small real request)", variant="primary")
    gemini_msg = gr.Markdown("A key being present does not prove it works. Press the button to verify it for real.")
    gr.Markdown("### Data mode")
    with gr.Row():
        mode = gr.Radio([("Demo (synthetic data)", "demo"), ("Real (import your own CSV/XLSX)", "real")], value=ctx.mode, label="Active workspace")
        apply_btn = gr.Button("Switch mode")
        regen_btn = gr.Button("Regenerate demo data")
    msg = gr.Markdown()
    gr.Markdown(
        "### Notes\n"
        "- **Gemini key:** set `GEMINI_API_KEY` as an environment variable (Hugging Face: *Settings → Variables and secrets → New secret*). It is never shown here.\n"
        "- **Persistence:** free Hugging Face Spaces have an *ephemeral* disk. Demo data is regenerated on restart; imported real data on a free Space can be lost. "
        "For real production data set `DATABASE_URL` to a persistent PostgreSQL database.\n"
        "- **Privacy:** only the selected lead's relevant facts are sent to Gemini; the full CRM is never sent.\n"
        "- **Human approval:** emails and posts are drafts. The app never sends or publishes anything.")

    @safe("Health")
    def refresh():
        return check_health(ctx.db, ctx.mode)

    @safe("Mode", 2)
    def switch(m):
        if config.os.getenv("DATABASE_URL"):
            ctx.mode = m
            note = "DATABASE_URL is set, so the same database is used; only the label changed."
        else:
            ctx.activate(m)
            note = ctx.error or f"Switched to {m.upper()} mode."
        return note, check_health(ctx.db, ctx.mode)

    @safe("Demo data", 2)
    def regen():
        m = ctx.regenerate_demo()
        return m, check_health(ctx.db, ctx.mode)

    @safe("Gemini test", 2)
    def test_gemini():
        from agent import gemini_client
        r = gemini_client.test_connection()
        return ("✅ " if r["ok"] else "❌ ") + r["message"], check_health(ctx.db, ctx.mode)

    ev_test = test_btn.click(test_gemini, None, [gemini_msg, health])
    check_btn.click(refresh, None, health)
    ev_apply = apply_btn.click(switch, mode, [msg, health])
    ev_regen = regen_btn.click(regen, None, [msg, health])
    return Refreshable(refresh, [health]), [ev_apply, ev_regen, ev_test]
