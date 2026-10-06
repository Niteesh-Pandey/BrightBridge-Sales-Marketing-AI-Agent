"""Shared UI helpers: styling, table formatting, safe handler wrapper."""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Callable

import gradio as gr
import pandas as pd
import plotly.graph_objects as go

import config
from services.utils import ago, format_inr

BRAND, BRAND2, ACCENT, GREY = "#1F4E8C", "#2E86AB", "#F18F01", "#6B7280"
COLORWAY = [BRAND, BRAND2, "#59A5D8", ACCENT, "#7BB662", "#9B5DE5", "#E4572E", "#17BEBB"]
PRIORITY_COLORS = {"CALL NOW": "#D64545", "FOLLOW UP SOON": ACCENT, "NURTURE": BRAND2, "LOW PRIORITY": "#9CA3AF", "CLOSED": "#D1D5DB"}

CSS = """
.bb-header{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;padding:14px 18px;border-radius:12px;
  background:linear-gradient(120deg,#1F4E8C,#2E86AB);color:#fff}
.bb-header h1{margin:0;font-size:1.35rem;color:#fff !important}.bb-header p{margin:2px 0 0;font-size:.9rem;color:#fff !important}
.bb-pill{display:inline-block;padding:4px 12px;border-radius:999px;font-size:.78rem;font-weight:600;margin-left:6px}
.bb-demo{background:#FFF3CD;color:#7A5200;border:1px solid #F1C40F}.bb-real{background:#D9F2E3;color:#14532D;border:1px solid #59B77D}
.bb-ai-on{background:#D9F2E3;color:#14532D;border:1px solid #59B77D}.bb-ai-off{background:#EEE;color:#555}
.bb-ai-warn{background:#FFF3CD;color:#7A5200;border:1px solid #F1C40F}.bb-ai-err{background:#FDE2E2;color:#8A1C1C;border:1px solid #E57373}
.bb-empty{border:2px dashed #2E86AB;border-radius:12px;padding:16px 20px;margin:10px 0;background:var(--block-background-fill)}
.bb-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:8px 0}
.bb-kpi{background:var(--block-background-fill);border:1px solid var(--border-color-primary);border-radius:12px;padding:14px 16px}
.bb-kpi .l{font-size:.78rem;color:var(--body-text-color-subdued);text-transform:uppercase;letter-spacing:.04em}
.bb-kpi .v{font-size:1.5rem;font-weight:700;margin-top:4px}
.bb-welcome{border:1px solid var(--border-color-primary);border-radius:14px;padding:18px;background:var(--block-background-fill)}
.bb-note{font-size:.85rem;color:var(--body-text-color-subdued)}
"""


def header_html(ctx) -> str:
    cls = "bb-demo" if ctx.mode == "demo" else "bb-real"
    ai = gemini_pill()
    return (f'<div class="bb-header"><div><h1>{config.APP_NAME}</h1><p>{config.COMPANY_NAME} — {config.TAGLINE}</p></div>'
            f'<div><span class="bb-pill {cls}">{ctx.banner}</span>{ai}</div></div>')


def gemini_pill() -> str:
    """Honest status: a key being present is NOT shown as 'connected' until a real test call succeeded."""
    from agent import gemini_client
    st = gemini_client.status()
    state = st["state"]
    if state == "ok":
        return f'<span class="bb-pill bb-ai-on" title="{st["message"]}">Gemini: connected ✓ ({st["model"]})</span>'
    if state == "error":
        return '<span class="bb-pill bb-ai-err" title="Open Settings → Test Gemini connection for details">Gemini: connection problem — see Settings</span>'
    if state == "unknown":
        return '<span class="bb-pill bb-ai-warn">Gemini: key found, not verified yet</span>'
    return '<span class="bb-pill bb-ai-off">Gemini: not configured</span>'


def gemini_note() -> str:
    from agent import gemini_client
    st = gemini_client.status()
    if st["state"] == "ok":
        return ""
    if st["state"] == "error":
        return f"⚠️ **Gemini problem:** {st['message']} (CRM features still work; test again in Settings.)"
    if st["state"] == "unknown":
        return "ℹ️ Gemini key found but not verified yet. Open **Settings → Test Gemini connection**."
    return f"ℹ️ {config.GEMINI_MISSING_MSG}"


def kpi_html(k: dict) -> str:
    from services.sanitize import to_number
    money = {"Pipeline Value", "Won Revenue"}
    cards = "".join(f'<div class="bb-kpi"><div class="l">{n}</div><div class="v">{format_inr(to_number(v)) if n in money else f"{int(to_number(v)):,}"}</div></div>'
                    for n, v in k.items())
    return f'<div class="bb-kpis">{cards}</div>'


def empty_fig(msg="No data yet — open the Import Data tab (or switch to Demo mode)") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(size=14, color=GREY))
    fig.update_layout(template="plotly_white", xaxis=dict(visible=False), yaxis=dict(visible=False), height=320)
    return fig


def style(fig: go.Figure, height=340) -> go.Figure:
    fig.update_layout(template="plotly_white", height=height, margin=dict(l=10, r=10, t=45, b=10), colorway=COLORWAY,
                      title_font_size=15, legend_title_text="")
    return fig


def show_table(df: pd.DataFrame, cols: dict[str, str] | None = None) -> pd.DataFrame:
    """Human-friendly lead table (Indian rupee format, 'x days ago')."""
    cols = cols or {"company": "Company", "contact": "Contact", "industry": "Industry", "source": "Source", "stage": "Stage",
                    "deal_value": "Deal Value", "days_since_contact": "Last Contact", "score": "Score", "priority": "Priority",
                    "next_action": "Next Action"}
    if df is None or df.empty:
        return pd.DataFrame(columns=list(cols.values()))
    out = df[[c for c in cols if c in df.columns]].copy()
    if "deal_value" in out:
        out["deal_value"] = out["deal_value"].map(lambda v: format_inr(v) if v else "—")
    if "days_since_contact" in out:
        out["days_since_contact"] = out["days_since_contact"].map(ago)
    return out.rename(columns=cols).fillna("—")


def lead_choices(frame: pd.DataFrame, limit: int = 1500) -> list[tuple[str, int]]:
    if frame is None or frame.empty:
        return []
    return [(f"{r.company} — {r.contact or 'no contact'} · {r.priority} ({r.score})", int(r.lead_id)) for r in frame.head(limit).itertuples()]


@dataclass
class Refreshable:
    """A tab's refresh function and the components it updates (used after mode switch/import)."""
    fn: Callable
    outputs: list


def chain_refresh(event, refreshables: list[Refreshable]):
    for r in refreshables:
        event = event.then(r.fn, None, r.outputs, show_progress="hidden")
    return event


def safe(area: str, n_outputs: int = 1):
    """Decorator: show a friendly message instead of a stack trace when a handler fails."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except Exception as exc:
                gr.Warning(f"{area}: {type(exc).__name__}. Please try again or check Settings → System Health.")
                return tuple([gr.update()] * n_outputs) if n_outputs > 1 else gr.update()
        return wrapper
    return deco
