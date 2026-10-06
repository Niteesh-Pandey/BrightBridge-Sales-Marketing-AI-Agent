"""AI Email tab: draft only; the user edits and sends from their own email client."""
from __future__ import annotations

import urllib.parse

import gradio as gr

import config
from services import crm_service as crm
from services.email_service import GOALS, generate_email, save_draft
from ui.common import Refreshable, gemini_note, lead_choices, safe


def build(ctx):
    gr.Markdown("### AI email drafts\nPick a lead. Only that lead's relevant facts are sent to Gemini. **Nothing is ever sent automatically.**")
    with gr.Row():
        lead = gr.Dropdown(choices=[], label="Lead (sorted by priority)", filterable=True, scale=3)
        goal = gr.Dropdown(GOALS, value=GOALS[0], label="Goal", scale=2)
    extra = gr.Textbox(label="Extra instructions (optional)", placeholder="e.g. keep it very short; mention the Pune office", max_lines=2)
    with gr.Row():
        ai_btn = gr.Button("Generate with Gemini", variant="primary")
        tpl_btn = gr.Button("Use plain CRM template (no AI)")
    status = gr.Markdown()
    facts = gr.Markdown(label="Facts used")
    subject = gr.Textbox(label="Subject (editable)")
    body = gr.Textbox(label="Email body (editable)", lines=12)
    cta = gr.Textbox(label="Suggested CTA")
    with gr.Row():
        save_btn = gr.Button("Save edited draft")
        mail_btn = gr.Button("Prepare 'open in my email app' link")
    mail_html = gr.HTML()

    def _facts(lid):
        d = crm.get_lead_detail(ctx.require_db(), int(lid))
        if not d:
            return ""
        o = d["best_opportunity"]
        return (f"**Facts used:** {d['lead']['contact']} ({d['lead']['job_title']}), {d['company']['name']} · "
                f"{(o['stage'] + ' — ' + o['name']) if o else 'no opportunity'} · priority {d['priority']} · recent activities: "
                f"{', '.join(a['type'] for a in d['activities'][:5]) or 'none'}")

    def _lead_id(lid) -> int | None:
        """Dropdown value is the numeric lead id; anything else (None/label/empty) is rejected before any API call."""
        try:
            return int(lid) if lid not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _run(lid, goal_, extra_, use_ai):
        lid = _lead_id(lid)
        if lid is None:
            gr.Warning("Please choose a lead from the dropdown first — no request was made.")
            return "Please choose a lead from the dropdown first.", "", "", "", ""
        r = generate_email(ctx.require_db(), lid, goal_, extra_ or "", use_ai=use_ai)
        if not r["ok"]:
            return f"⚠️ {r['message']}", _facts(lid), "", "", ""
        return f"✅ {r['message']}" + ("" if r["ai"] else " (plain template — no AI used)"), _facts(lid), r["subject"], r["body"], r["cta"]

    @safe("Email", 5)
    def gen_ai(lid, g, e):
        return _run(lid, g, e, True)

    @safe("Email", 5)
    def gen_tpl(lid, g, e):
        return _run(lid, g, e, False)

    @safe("Email")
    def save(lid, s, b, c):
        if not (s and b):
            gr.Info("Nothing to save yet — generate or write a draft first.")
            return "Nothing to save yet."
        save_draft(ctx.require_db(), "email", s, f"Subject: {s}\n\n{b}\n\nSuggested CTA: {c}", _lead_id(lid))
        return "✅ Draft saved (status: draft). It has not been sent."

    @safe("Email")
    def mailto(lid, s, b):
        if not ((s or "").strip() and (b or "").strip()):
            gr.Info("Generate or write a draft first — the email link needs a subject and body.")
            return ""
        lid = _lead_id(lid)
        d = crm.get_lead_detail(ctx.require_db(), lid) if lid is not None else None
        to = (d["lead"]["email"] if d else "") or ""
        # always URL-encode — an empty draft or special characters must never break the link
        url = f"mailto:{urllib.parse.quote(to)}?subject={urllib.parse.quote(s)}&body={urllib.parse.quote(b)}"
        return f'<a href="{url}" target="_blank" rel="noopener">✉️ Open this draft in your email app (you send it yourself)</a>'

    outs = [status, facts, subject, body, cta]
    ai_btn.click(gen_ai, [lead, goal, extra], outs)
    tpl_btn.click(gen_tpl, [lead, goal, extra], outs)
    save_btn.click(save, [lead, subject, body, cta], status)
    mail_btn.click(mailto, [lead, subject, body], mail_html)

    @safe("Email", 2)
    def refresh():
        note = gemini_note()
        try:
            choices = lead_choices(ctx.frame())
        except Exception:
            choices = []
        if not choices:
            note = (note + "\n\n" if note else "") + "⚠️ No leads available yet — import data (or switch to Demo mode) to draft emails."
        return gr.update(choices=choices, value=None), note
    return Refreshable(refresh, [lead, status])
