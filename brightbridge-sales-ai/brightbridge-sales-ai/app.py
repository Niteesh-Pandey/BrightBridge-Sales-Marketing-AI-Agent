"""BrightBridge Sales Intelligence — Gradio entry point.

Run locally:  python app.py          (opens http://127.0.0.1:7860)
Hugging Face: this file is the Space entry point; GEMINI_API_KEY comes from Space Secrets.
"""
from __future__ import annotations

import os

import gradio as gr

import config
from agent import gemini_client
from ui import ai_assistant, crm, dashboard, email_generator, import_data, lead_priority, marketing, pipeline, settings
from ui.common import CSS, Refreshable, header_html
from ui.context import AppContext


def empty_state_html(ctx: AppContext) -> str:
    """Friendly guidance when there is no data yet (instead of silent empty tables)."""
    try:
        empty = ctx.db is not None and ctx.db.is_empty()
    except Exception:
        empty = False
    if not empty:
        return ""
    return ("<div class='bb-empty'><b>📥 No business data yet.</b> This is your <b>Real data</b> workspace and it is empty.<br>"
            "① Open the <b>Import Data</b> tab → ② download the templates (or use your own CSV/XLSX files) → "
            "③ upload <b>all files at once</b> → ④ validate → ⑤ import.<br>"
            "Just want to explore? Go to <b>Settings → Data mode → Demo</b>.</div>") if ctx.mode == "real" else ""


def build_app(ctx: AppContext | None = None) -> gr.Blocks:
    ctx = ctx or AppContext()
    refreshables: list[Refreshable] = []

    with gr.Blocks(title=config.APP_NAME) as demo:
        header = gr.HTML(header_html(ctx))
        db_error = gr.Markdown(f"⚠️ **{ctx.error}**" if ctx.error else "")
        empty_banner = gr.HTML(empty_state_html(ctx))

        with gr.Group(visible=True, elem_classes="bb-welcome") as welcome:
            gr.Markdown(f"## Welcome to {config.APP_NAME}\n"
                        "Turn your CRM data into clear sales priorities, evidence-based answers and ready-to-edit drafts.\n\n"
                        "**Choose how to start:**")
            with gr.Row():
                demo_btn = gr.Button("Demo Mode — explore with synthetic data", variant="primary")
                real_btn = gr.Button("Import Real Data — use your own CSV/XLSX")
            gr.Markdown("<span class='bb-note'>Demo mode loads fictional BrightBridge Solutions data instantly. "
                        "Real mode starts empty; you import your own file with validation and a confirmation step.</span>")

        with gr.Tabs() as tabs:
            with gr.Tab("Dashboard", id="dashboard"):
                refreshables.append(dashboard.build(ctx))
            with gr.Tab("CRM", id="crm"):
                refreshables.append(crm.build(ctx))
            with gr.Tab("Lead Priority", id="priority"):
                refreshables.append(lead_priority.build_priority(ctx))
            with gr.Tab("Pipeline", id="pipeline"):
                refreshables.append(pipeline.build(ctx))
            with gr.Tab("Follow-ups", id="followups"):
                refreshables.append(lead_priority.build_followups(ctx))
            with gr.Tab("AI Email", id="email"):
                refreshables.append(email_generator.build(ctx))
            with gr.Tab("Marketing", id="marketing"):
                refreshables.append(marketing.build(ctx))
            with gr.Tab("AI Assistant", id="assistant"):
                refreshables.append(ai_assistant.build(ctx))
            with gr.Tab("Import Data", id="import"):
                imp_ref, import_events, switch_btn, switch_real = import_data.build(ctx)
                refreshables.append(imp_ref)
            with gr.Tab("Settings", id="settings"):
                set_ref, settings_events = settings.build(ctx)
                refreshables.append(set_ref)

        all_outputs = [header, db_error, empty_banner] + [o for r in refreshables for o in r.outputs]

        def refresh_all():
            gemini_client.verify_in_background()
            vals = [header_html(ctx), f"⚠️ **{ctx.error}**" if ctx.error else "", empty_state_html(ctx)]
            for r in refreshables:
                if ctx.db is None:
                    vals += [gr.update()] * len(r.outputs)
                    continue
                out = r.fn()
                vals += list(out) if len(r.outputs) > 1 else [out]
            return vals

        def start_demo():
            ctx.activate("demo")
            return gr.update(visible=False), gr.Tabs(selected="dashboard")

        def start_real():
            ctx.activate("real")
            return gr.update(visible=False), gr.Tabs(selected="import")

        demo_btn.click(start_demo, None, [welcome, tabs]).then(refresh_all, None, all_outputs, show_progress="hidden")
        real_btn.click(start_real, None, [welcome, tabs]).then(refresh_all, None, all_outputs, show_progress="hidden")
        # run the full refresh AFTER the triggering action finishes (chained, not parallel)
        for ev in [*settings_events, *import_events]:
            ev.then(refresh_all, None, all_outputs, show_progress="hidden")
        switch_btn.click(switch_real, None, None).then(refresh_all, None, all_outputs, show_progress="hidden")
        demo.load(refresh_all, None, all_outputs, show_progress="hidden")

        gr.Markdown(f"<span class='bb-note'>{config.APP_NAME} v{config.APP_VERSION} · built by {config.OWNER} · "
                    "AI output is a draft for human review. Scores and metrics are deterministic.</span>")
    return demo


def main() -> None:
    app = build_app()
    gemini_client.verify_in_background()
    port = int(os.getenv("PORT") or os.getenv("GRADIO_SERVER_PORT") or 7860)
    host = os.getenv("GRADIO_SERVER_NAME", "0.0.0.0")
    print(f"\n{config.APP_NAME} v{config.APP_VERSION} starting…")
    print(f"Open in your browser: http://127.0.0.1:{port}\n")
    app.queue().launch(server_name=host, server_port=port, theme=gr.themes.Soft(primary_hue="blue"), css=CSS, show_error=False)


if __name__ == "__main__":
    main()
