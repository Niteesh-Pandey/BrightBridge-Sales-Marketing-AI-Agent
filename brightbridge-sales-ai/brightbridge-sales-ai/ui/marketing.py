"""Marketing tab: LinkedIn / short posts, campaign ideas, content calendar.

Drafts only. "Posting" = opening LinkedIn / X with the text pre-filled; YOU press Post. The app never publishes by itself.
"""
from __future__ import annotations

import html

import gradio as gr

from services.export_service import export_frame
from services.marketing_service import (KINDS, OBJECTIVES, PLATFORMS, STATUSES, TONES, generate_content, list_generated, save_edited,
                                        share_links, update_status)
from ui.common import Refreshable, gemini_note, safe


def build(ctx):
    gr.Markdown("### Marketing content studio\n"
                "1) Generate a draft → 2) edit it → 3) **Open in LinkedIn / X** (text is pre-filled, you press *Post*) → 4) mark it as posted. "
                "**This app never posts for you**, and uses no fake statistics or testimonials.")
    with gr.Row():
        kind = gr.Dropdown(list(KINDS), value="LinkedIn post", label="Content type")
        platform = gr.Dropdown(PLATFORMS, value="LinkedIn", label="Platform")
        tone = gr.Dropdown(TONES, value="Professional", label="Tone")
        objective = gr.Dropdown(OBJECTIVES, value="Generate leads", label="Objective")
    topic = gr.Textbox(label="Topic", placeholder="e.g. Automating invoice follow-ups for small businesses")
    audience = gr.Textbox(label="Audience", value="Small and mid-sized business owners in India")
    with gr.Row():
        gen_btn = gr.Button("Generate with Gemini", variant="primary")
        tpl_btn = gr.Button("Create from template (no AI)")
    status = gr.Markdown()
    out = gr.Textbox(label="Draft (editable — this is what will be posted)", lines=14, buttons=["copy"])
    with gr.Row():
        save_btn = gr.Button("Save edited draft")
        links_btn = gr.Button("Prepare posting links")
    links_html = gr.HTML()
    gr.Markdown("#### Saved drafts & status")
    with gr.Row():
        draft_id = gr.Number(label="Draft ID", precision=0, value=None, scale=1)
        new_status = gr.Dropdown(STATUSES, value="approved", label="Set status", scale=1)
        status_btn = gr.Button("Update status", scale=1)
    with gr.Row():
        fmt = gr.Radio(["csv", "xlsx"], value="csv", label="Export format")
        export_btn = gr.Button("Export generated content")
        file = gr.File(label="Download", interactive=False)
    table = gr.Dataframe(interactive=False, wrap=True, max_height=300)

    def _table():
        df = list_generated(ctx.require_db())
        if df.empty:
            return df
        df["body"] = df["body"].str.slice(0, 120)
        return df

    def _gen(use_ai, k, t, a, p, tn, o):
        r = generate_content(ctx.require_db(), k, t, a, p, tn, o, use_ai=use_ai)
        return f"{'✅' if r['ok'] else '⚠️'} {r['message']}", r.get("text", ""), _table(), r.get("content_id")

    @safe("Marketing", 4)
    def gen_ai(k, t, a, p, tn, o):
        return _gen(True, k, t, a, p, tn, o)

    @safe("Marketing", 4)
    def gen_tpl(k, t, a, p, tn, o):
        return _gen(False, k, t, a, p, tn, o)

    @safe("Marketing", 2)
    def save(k, t, text):
        if not (text or "").strip():
            return "Nothing to save.", _table()
        save_edited(ctx.require_db(), k, t, text)
        return "✅ Draft saved (status: draft). Not published.", _table()

    @safe("Marketing")
    def links(text):
        if not (text or "").strip():
            return "<i>Write or generate a draft first.</i>"
        l = share_links(text)
        x = (f'<a href="{html.escape(l["x"])}" target="_blank" rel="noopener">𝕏 Open in X</a>' if l["x"]
             else "<span style='color:#888'>X: text is longer than 280 characters</span>")
        return ("<div style='line-height:2'>"
                f'<a href="{html.escape(l["linkedin"])}" target="_blank" rel="noopener">🔗 Open in LinkedIn</a> &nbsp;·&nbsp; {x}<br>'
                "<small>These links open the site's composer with your text pre-filled. <b>You review it and press Post yourself.</b> "
                "If LinkedIn does not pre-fill the text, use the copy button on the draft box and paste it. "
                "Facebook / Instagram: copy and paste the text.</small></div>")

    @safe("Marketing", 2)
    def set_status(cid, st):
        if not cid:
            return "Enter the Draft ID from the table first.", _table()
        ok = update_status(ctx.require_db(), int(cid), st)
        return (f"✅ Draft {int(cid)} is now “{st}”." if ok else f"Draft {int(cid)} was not found."), _table()

    @safe("Export")
    def export(fmt_):
        df = list_generated(ctx.require_db())
        if df.empty:
            gr.Info("Nothing to export — generate a draft first.")
            return None
        return export_frame(df, "generated_content", fmt_)

    inputs = [kind, topic, audience, platform, tone, objective]
    gen_btn.click(gen_ai, inputs, [status, out, table, draft_id])
    tpl_btn.click(gen_tpl, inputs, [status, out, table, draft_id])
    save_btn.click(save, [kind, topic, out], [status, table])
    links_btn.click(links, out, links_html)
    status_btn.click(set_status, [draft_id, new_status], [status, table])
    export_btn.click(export, fmt, file)

    @safe("Marketing", 2)
    def refresh():
        return gemini_note(), _table()
    return Refreshable(refresh, [status, table])
