"""Dashboard tab: KPI cards + six charts. Never raises: every chart/KPI has an explicit safe fallback."""
from __future__ import annotations

import gradio as gr
import pandas as pd
import plotly.express as px

from services import analytics_service as an
from services.sanitize import log, safe_call, to_number
from services.utils import format_inr
from ui.common import BRAND, PRIORITY_COLORS, Refreshable, empty_fig, kpi_html, style

KPI_DEFAULT = {"Total Leads": 0, "Active Opportunities": 0, "Pipeline Value": 0.0, "Won Revenue": 0.0, "Follow-ups Due": 0, "High Priority Leads": 0}
NO_DATA = "No data yet — open the Import Data tab (or switch to Demo mode)"


def _safe_fig(build, label: str):
    """Build one chart; on any problem log it and show a calm placeholder instead of crashing the tab."""
    try:
        return build()
    except Exception as exc:
        log.warning("chart '%s' failed (%s: %s)", label, type(exc).__name__, str(exc)[:200])
        return empty_fig(f"“{label}” is temporarily unavailable")


def make_figures(db, frame) -> list:
    def stage_chart():
        d = an.pipeline_by_stage(db)
        d = d.assign(stage=d["stage"].astype(str))
        if to_number(d["value_inr"].sum()) <= 0:
            return empty_fig(NO_DATA)
        fig = style(px.bar(d, x="stage", y="value_inr", hover_data=["opportunities"], title="Pipeline by Stage (₹)",
                           labels={"value_inr": "Value (₹)", "stage": ""}, color_discrete_sequence=[BRAND]))
        fig.update_yaxes(tickformat=",.0f", rangemode="tozero")
        return fig

    def source_chart():
        d = an.leads_by_source(db)
        return style(px.pie(d, names="source", values="leads", hole=0.5, title="Leads by Source")) if len(d) else empty_fig(NO_DATA)

    def score_chart():
        d = an.score_distribution(frame)
        if not len(d):
            return empty_fig(NO_DATA)
        fig = style(px.histogram(d, x="score", color="priority", nbins=20, color_discrete_map=PRIORITY_COLORS,
                                 title="Lead Score Distribution (open leads)", labels={"score": "Score"}))
        fig.update_xaxes(range=[0, 100], rangemode="tozero")
        fig.update_yaxes(rangemode="tozero", tickformat=",d")
        return fig

    def industry_chart():
        d = an.opportunities_by_industry(db)
        if not len(d):
            return empty_fig(NO_DATA)
        fig = style(px.bar(d.sort_values("value_inr"), x="value_inr", y="industry", orientation="h", hover_data=["opportunities"],
                           title="Opportunities by Industry (₹)", labels={"value_inr": "Value (₹)", "industry": ""},
                           color_discrete_sequence=[BRAND]))
        fig.update_xaxes(tickformat=",.0f", rangemode="tozero")
        return fig

    def monthly_chart():
        d = an.monthly_pipeline(db)
        if not len(d):
            return empty_fig(NO_DATA)
        fig = style(px.line(d, x="month", y="value_inr", markers=True, title="Monthly Pipeline Created (₹)",
                            labels={"value_inr": "Value (₹)", "month": ""}))
        # clean month ticks ("Sep 2026") in every Plotly version + readable ₹ axis (no 741.499k-style ticks)
        fig.update_xaxes(tickformat="%b %Y", dtick="M1", ticklabelmode="period", tickangle=-30)
        fig.update_yaxes(tickformat=",.0f", rangemode="tozero")
        return fig

    def campaign_chart():
        d = an.campaign_performance(db)
        if d.empty:
            return empty_fig(NO_DATA)
        d = d.head(10).assign(cost_per_lead=lambda x: x["cost_per_lead"].fillna(0.0))
        fig = style(px.bar(d, x="leads", y="campaign", orientation="h", color="channel", hover_data=["clicks", "cost_per_lead"],
                           title="Campaign Performance — Leads (top 10)", labels={"leads": "Leads", "campaign": ""}))
        # keep the axis anchored at 0 — never a -1..1 negative range when counts are tiny
        fig.update_xaxes(rangemode="tozero", tickformat=",d")
        return fig

    return [_safe_fig(f, n) for f, n in ((stage_chart, "Pipeline by Stage"), (source_chart, "Leads by Source"), (score_chart, "Lead Score Distribution"),
                                         (industry_chart, "Opportunities by Industry"), (monthly_chart, "Monthly Pipeline"), (campaign_chart, "Campaign Performance"))]


def extras_html(db) -> str:
    m = safe_call(an.extra_metrics, {}, "extra metrics", db)
    if not m:
        return ""
    weighted_note = " (stage-based estimate)" if m.get("Weighted Pipeline Is Estimate") else ""
    return ("<div class='bb-note' style='margin:2px 0 8px'>"
            f"Average deal size <b>{format_inr(m['Average Deal Size'])}</b> · Win rate <b>{m['Win Rate %']}%</b> (closed deals) · "
            f"Weighted pipeline <b>{format_inr(m['Weighted Pipeline'])}</b>{weighted_note}</div>")


def build(ctx):
    gr.Markdown("### Sales performance at a glance\n<span class='bb-note'>All figures are calculated from CRM data by deterministic code.</span>")
    kpis = gr.HTML()
    extras = gr.HTML()
    plots = []
    for pair in ((0, 1), (2, 3), (4, 5)):
        with gr.Row():
            for _ in pair:
                plots.append(gr.Plot(show_label=False))
    refresh_btn = gr.Button("Refresh dashboard", size="sm")

    def refresh():
        """Always returns 8 values (KPIs, extras, 6 charts) — falls back to zeros/placeholders on any failure."""
        try:
            db = ctx.require_db()
        except Exception:
            return (kpi_html(KPI_DEFAULT), "", *[empty_fig("Database unavailable — see Settings") for _ in range(6)])
        frame = safe_call(ctx.frame, pd.DataFrame(columns=["priority", "score"]), "lead table")
        k = safe_call(an.kpis, dict(KPI_DEFAULT), "kpis", db, frame)
        return (kpi_html(k), extras_html(db), *make_figures(db, frame))

    outputs = [kpis, extras, *plots]
    refresh_btn.click(refresh, None, outputs)
    return Refreshable(refresh, outputs)
