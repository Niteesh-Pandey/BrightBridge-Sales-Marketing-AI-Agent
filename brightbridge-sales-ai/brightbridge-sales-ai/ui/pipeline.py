"""Pipeline tab: open opportunities by stage with funnel chart and export."""
from __future__ import annotations

import gradio as gr
import pandas as pd
import plotly.graph_objects as go

from database.models import OPEN_STAGES, STAGES
from services import analytics_service as an
from services.export_service import export_frame
from services.utils import format_inr
from ui.common import Refreshable, empty_fig, safe, style

COLS = {"company": "Company", "opp_name": "Opportunity", "stage": "Stage", "deal_value": "Deal Value", "expected_close": "Expected Close",
        "score": "Score", "priority": "Priority", "next_action": "Next Action"}


def build(ctx):
    gr.Markdown("### Sales pipeline\nOpen and closed opportunities. Pipeline value counts only open stages.")
    with gr.Row():
        stage = gr.Dropdown(["All"] + STAGES, value="All", label="Stage", scale=1)
        fmt = gr.Radio(["csv", "xlsx"], value="csv", label="Export format", scale=1)
        export_btn = gr.Button("Export pipeline")
        file = gr.File(label="Download", interactive=False)
    summary = gr.Markdown()
    funnel = gr.Plot(show_label=False)
    table = gr.Dataframe(interactive=False, wrap=True, max_height=420)

    def _data(stage_):
        f = ctx.frame()
        f = f[f["opp_id"].notna()]
        if stage_ != "All":
            f = f[f["stage"] == stage_]
        return f.sort_values("deal_value", ascending=False)

    @safe("Pipeline", 3)
    def refresh(stage_="All"):
        db, f = ctx.require_db(), _data(stage_)
        d = an.pipeline_by_stage(db)
        # sort descending by value: a funnel's "percent initial" is only meaningful (≤100%) when the
        # first stage is the largest — otherwise stages show impossible percentages like 131%
        open_stages = (d[~d["stage"].astype(str).isin(["Closed Won", "Closed Lost"])]
                       .sort_values("value_inr", ascending=False).reset_index(drop=True))
        open_stages = open_stages[open_stages["value_inr"] > 0] if len(open_stages) else open_stages
        if len(open_stages):
            palette = ["#1F4E8C", "#2E86AB", "#59A5D8", "#F18F01"]
            fig = style(go.Figure(go.Funnel(y=open_stages["stage"].astype(str), x=open_stages["value_inr"],
                                            textinfo="value+percent initial", texttemplate="%{value:,.0f} · %{percentInitial:.0%}",
                                            marker=dict(color=[palette[i % len(palette)] for i in range(len(open_stages))]))))
            fig.update_layout(title="Open pipeline value by stage (₹)")
        else:
            fig = empty_fig()
        shown = f[list(COLS)].copy()
        shown["deal_value"] = shown["deal_value"].map(format_inr)
        # headline numbers come from ALL opportunities (same source as the funnel) and never mix open with closed
        open_rows = d[d["stage"].astype(str).isin(OPEN_STAGES)]
        won_val = float(d.loc[d["stage"].astype(str) == "Closed Won", "value_inr"].sum())
        open_n, open_val = int(open_rows["opportunities"].sum()), float(open_rows["value_inr"].sum())
        summary = (f"**{open_n}** open opportunities · open pipeline **{format_inr(open_val)}**"
                   f"  ·  closed-won (not in pipeline) **{format_inr(won_val)}**"
                   + (f"  ·  showing stage “{stage_}”: **{len(f)}** deal(s)" if stage_ != "All" else ""))
        return summary, fig, shown.rename(columns=COLS).fillna("—")

    @safe("Export")
    def export(stage_, fmt_):
        f = _data(stage_)
        if f.empty:
            gr.Info("Nothing to export — no opportunities for the current filter.")
            return None
        return export_frame(f[list(COLS)].rename(columns=COLS), "pipeline", fmt_)

    stage.change(refresh, stage, [summary, funnel, table])
    export_btn.click(export, [stage, fmt], file)
    return Refreshable(lambda: refresh("All"), [summary, funnel, table])
