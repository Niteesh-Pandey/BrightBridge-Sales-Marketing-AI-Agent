"""AI Assistant tab: evidence-first chat (ANSWER / SUPPORTING DATA / SUGGESTED ACTION)."""
from __future__ import annotations

import gradio as gr

import config
from agent.agent import EXAMPLES, SalesAgent
from ui.common import Refreshable, gemini_note, lead_choices


def build(ctx):
    gr.Markdown("### AI assistant\nEvery CRM answer is built from retrieved CRM data and shows its supporting evidence. "
                "Scores are calculated by code, never by the AI.")
    note = gr.Markdown()
    chat = gr.Chatbot(height=460, label="BrightBridge assistant", render_markdown=True)
    focus = gr.Dropdown(choices=[], label="Focus lead (optional — for “this lead / this customer” questions)", filterable=True)
    with gr.Row():
        box = gr.Textbox(placeholder="Ask about leads, pipeline, campaigns… or request a draft", show_label=False, scale=5, lines=1)
        send = gr.Button("Ask", variant="primary", scale=1)
    gr.Examples([e for e in EXAMPLES if "<" not in e], inputs=box, label="Try these")

    def respond(msg, history, focus_id):
        history = list(history or [])
        if not (msg or "").strip():
            return history, ""
        try:
            reply = SalesAgent(ctx.require_db()).ask(msg, int(focus_id) if focus_id else None).to_markdown()
        except Exception as exc:
            reply = f"Sorry, something went wrong ({type(exc).__name__}). CRM pages remain available."
        history += [{"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
        return history, ""

    send.click(respond, [box, chat, focus], [chat, box])
    box.submit(respond, [box, chat, focus], [chat, box])

    def refresh():
        msg = gemini_note()
        return gr.update(choices=lead_choices(ctx.frame()), value=None), msg
    return Refreshable(refresh, [focus, note])
