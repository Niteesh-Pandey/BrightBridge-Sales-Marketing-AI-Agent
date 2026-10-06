"""CRM tab: search + filters, lead details, activity history, opportunities, export."""
from __future__ import annotations

import gradio as gr
import pandas as pd

from services import crm_service as crm
from services.export_service import export_frame
from services.utils import ago, format_inr
from ui.common import Refreshable, lead_choices, safe, show_table  # noqa: F401


def detail_markdown(d: dict) -> str:
    lead, comp, best = d["lead"], d["company"], d["best_opportunity"]
    opp = f"{best['name']} — **{best['stage']}**, {format_inr(best['value_inr'])} (expected close {best['expected_close_date'] or '—'})" if best else "No opportunity recorded"
    fu = d["follow_up"]
    fu_txt = f"{fu['state'].replace('_', ' ')} ({fu['date']})" if fu["date"] else "none pending"
    n = d["next_action"]
    return (f"#### {comp['name']} · {comp['industry'] or '—'} · {comp['city'] or '—'}\n"
            f"**Contact:** {lead['contact'] or '—'} ({lead['job_title'] or '—'}) · {lead['email'] or '—'} · {lead['phone'] or '—'}\n\n"
            f"**Lead:** {lead['status']} · Source: {lead['source'] or '—'} · Campaign: {lead['campaign'] or '—'}\n\n"
            f"**Opportunity:** {opp}\n\n**Last contact:** {ago(d['days_since_contact'])} · **Follow-up:** {fu_txt}\n\n"
            f"**Priority:** {d['priority']} (score {d['score']}/100) — {'; '.join(d['score_reasons'])}\n\n"
            f"**Next action:** {n['action']} — *Why:* {n['why']}")


def build(ctx):
    gr.Markdown("### Customer relationship manager\nFilter your leads, click a row to see its full history.")
    with gr.Row():
        search = gr.Textbox(label="Search", placeholder="Company, contact, email, city…", scale=3)
        industry = gr.Dropdown(["All"], value="All", label="Industry")
        stage = gr.Dropdown(["All"], value="All", label="Stage")
        source = gr.Dropdown(["All"], value="All", label="Source")
        priority = gr.Dropdown(["All"], value="All", label="Priority")
    with gr.Row():
        min_v = gr.Number(label="Min deal value (₹) — 0 = no limit", value=None, minimum=0)
        max_v = gr.Number(label="Max deal value (₹) — 0 = no limit", value=None, minimum=0)
        d_from = gr.Textbox(label="Created from (YYYY-MM-DD)", placeholder="2026-01-01")
        d_to = gr.Textbox(label="Created to (YYYY-MM-DD)", placeholder="2026-12-31")
        apply_btn = gr.Button("Apply filters", variant="primary")
    count_md = gr.Markdown()
    table = gr.Dataframe(interactive=False, wrap=True, max_height=420, label="Leads")
    ids_state = gr.State([])
    with gr.Row():
        fmt = gr.Radio(["csv", "xlsx"], value="csv", label="Export format", scale=1)
        export_btn = gr.Button("Export filtered leads", scale=1)
        export_file = gr.File(label="Download", interactive=False, scale=2)
    gr.Markdown("#### Lead details")
    detail = gr.Markdown("*Select a row above.*")
    with gr.Row():
        acts = gr.Dataframe(interactive=False, label="Activity history", max_height=300)
        opps = gr.Dataframe(interactive=False, label="Opportunities", max_height=300)

    filters = [search, industry, stage, source, priority, min_v, max_v, d_from, d_to]

    def _filtered(*vals):
        frame = ctx.frame()
        return frame, crm.filter_leads(frame, *vals)

    @safe("CRM", 3)
    def apply(*vals):
        _, f = _filtered(*vals)
        return show_table(f), [int(i) for i in f["lead_id"]] if len(f) else [], f"**{len(f)}** lead(s) match."

    @safe("CRM", 7)
    def refresh():
        frame = ctx.frame()
        o = crm.filter_options(frame)
        upd = [gr.update(choices=o[k], value="All") for k in ("industry", "stage", "source", "priority")]
        return (*upd, show_table(frame), [int(i) for i in frame["lead_id"]] if len(frame) else [], f"**{len(frame)}** lead(s) match.")

    @safe("Lead detail", 3)
    def on_select(ids, evt: gr.SelectData):
        if evt is None or evt.index is None:
            return "*Select a row above.*", gr.update(), gr.update()
        row = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        if not ids or row >= len(ids):
            return "*Select a row above.*", gr.update(), gr.update()
        d = crm.get_lead_detail(ctx.require_db(), ids[row])
        if not d:
            return "Insufficient data to determine this.", pd.DataFrame(), pd.DataFrame()
        a = pd.DataFrame(d["activities"])
        a = a[["activity_date", "type", "outcome", "follow_up_date"]].rename(columns={"activity_date": "Date", "type": "Type", "outcome": "Outcome", "follow_up_date": "Follow-up"}) if len(a) else pd.DataFrame(columns=["Date", "Type", "Outcome", "Follow-up"])
        o_ = pd.DataFrame(d["opportunities"])
        o_ = o_[["name", "stage", "value_inr", "expected_close_date"]].assign(value_inr=lambda x: x["value_inr"].map(format_inr)).rename(
            columns={"name": "Opportunity", "stage": "Stage", "value_inr": "Value", "expected_close_date": "Expected close"}) if len(o_) else pd.DataFrame(columns=["Opportunity", "Stage", "Value", "Expected close"])
        return detail_markdown(d), a, o_

    @safe("Export")
    def export(fmt_, *vals):
        _, f = _filtered(*vals)
        path = export_frame(f.drop(columns=["next_action_why"], errors="ignore"), "filtered_leads", fmt_)
        if not path:
            gr.Info("Nothing to export for the current filters.")
        return path

    apply_btn.click(apply, filters, [table, ids_state, count_md])
    search.submit(apply, filters, [table, ids_state, count_md])
    table.select(on_select, ids_state, [detail, acts, opps])
    export_btn.click(export, [fmt, *filters], export_file)
    outputs = [industry, stage, source, priority, table, ids_state, count_md]
    return Refreshable(refresh, outputs)
