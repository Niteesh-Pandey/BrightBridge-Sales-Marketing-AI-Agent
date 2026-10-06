"""Lead Priority and Follow-ups tabs (deterministic scores; AI never calculates them)."""
from __future__ import annotations

import gradio as gr
import pandas as pd

from services import crm_service as crm
from services.export_service import export_frame
from ui.common import Refreshable, safe, show_table

PRIORITY_COLS = {"company": "Company", "contact": "Contact", "stage": "Stage", "deal_value": "Deal Value",
                 "days_since_contact": "Last Contact", "score": "Score", "priority": "Priority", "next_action": "Next Action",
                 "next_action_why": "Why"}
FOLLOW_COLS = {"company": "Company", "contact": "Contact", "stage": "Stage", "deal_value": "Deal Value", "follow_up_date": "Follow-up Due",
               "follow_up_state": "State", "score": "Score", "next_action": "Next Action"}


def build_priority(ctx):
    gr.Markdown("### Lead priority\nScore 0–100 from engagement, recency, deal value, stage, activity count, email engagement, meetings and follow-up status. "
                "**80–100 CALL NOW · 60–79 FOLLOW UP SOON · 40–59 NURTURE · 0–39 LOW PRIORITY.**")
    with gr.Row():
        level = gr.Dropdown(["CALL NOW", "FOLLOW UP SOON", "NURTURE", "LOW PRIORITY", "All open"], value="CALL NOW", label="Priority level", scale=2)
        limit = gr.Slider(5, 100, value=25, step=5, label="Rows", scale=3)
    with gr.Row():
        fmt = gr.Radio(["csv", "xlsx"], value="csv", label="Export format", scale=1)
        export_btn = gr.Button("Export call priority list", scale=1)
        file = gr.File(label="Download", interactive=False, scale=2)
    info = gr.Markdown()
    table = gr.Dataframe(interactive=False, wrap=True, max_height=480)

    def _data(level_, limit_):
        f = ctx.frame()
        f = f[f["priority"] != "CLOSED"] if level_ == "All open" else f[f["priority"] == level_]
        return f.sort_values(["score", "deal_value"], ascending=False).head(int(limit_))

    @safe("Lead priority", 2)
    def refresh(level_="CALL NOW", limit_=25):
        f = _data(level_, limit_)
        if len(f):
            return f"Showing **{len(f)}** lead(s). Open a lead in the CRM tab for full evidence.", show_table(f, PRIORITY_COLS)
        counts = ctx.frame()["priority"].value_counts().to_dict()
        counts.pop("CLOSED", None)
        breakdown = " · ".join(f"{k}: **{v}**" for k, v in counts.items() if v) or "no open leads yet"
        return (f"No **{level_}** leads right now ({breakdown}). Try another priority level or **All open**."), show_table(f, PRIORITY_COLS)

    @safe("Export")
    def export(level_, limit_, fmt_):
        f = _data(level_, limit_)
        if f.empty:
            gr.Info(f"Nothing to export — no {level_} leads right now.")
            return None
        return export_frame(f[list(PRIORITY_COLS)].rename(columns=PRIORITY_COLS), "call_priority_list", fmt_)

    for c in (level, limit):
        c.change(refresh, [level, limit], [info, table])
    export_btn.click(export, [level, limit, fmt], file)
    return Refreshable(lambda: refresh("CALL NOW", 25), [info, table])


def build_followups(ctx):
    gr.Markdown("### Follow-ups\nA follow-up is pending until a later activity is logged for that lead.")
    with gr.Row():
        horizon = gr.Slider(0, 30, value=0, step=1, label="Also include upcoming (days ahead)", scale=3)
    with gr.Row():
        fmt = gr.Radio(["csv", "xlsx"], value="csv", label="Export format", scale=1)
        export_btn = gr.Button("Export follow-ups", scale=1)
        file = gr.File(label="Download", interactive=False, scale=2)
    info = gr.Markdown()
    table = gr.Dataframe(interactive=False, wrap=True, max_height=480)

    def _data(h):
        return crm.get_followups(ctx.frame(), int(h)).sort_values(["follow_up_state", "score"], ascending=[True, False])

    @safe("Follow-ups", 2)
    def refresh(h=0):
        f = _data(h)
        n_over = int((f["follow_up_state"] == "overdue").sum())
        msg = (f"**{len(f)}** follow-up(s): {n_over} overdue, {int((f['follow_up_state'] == 'due_today').sum())} due today."
               if len(f) else "No follow-ups due. Move the **upcoming** slider above to look ahead, or log calls with follow-up dates.")
        return msg, show_table(f, FOLLOW_COLS)

    @safe("Export")
    def export(h, fmt_):
        f = _data(h)
        if f.empty:
            gr.Info("Nothing to export — no follow-ups for the current setting.")
            return None
        return export_frame(f[list(FOLLOW_COLS)].rename(columns=FOLLOW_COLS), "followups", fmt_)

    horizon.change(refresh, horizon, [info, table])
    export_btn.click(export, [horizon, fmt], file)
    return Refreshable(lambda: refresh(horizon.value), [info, table])
