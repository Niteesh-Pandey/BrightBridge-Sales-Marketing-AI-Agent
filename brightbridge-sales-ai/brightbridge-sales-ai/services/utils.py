"""Shared formatting helpers."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd


def format_inr(value) -> str:
    """Indian digit grouping: 320000 -> ₹3,20,000."""
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return "—"
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) <= 3:
        return f"{sign}₹{s}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return f"{sign}₹{','.join(parts)},{tail}"


def parse_date(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def ago(days) -> str:
    if days is None or (isinstance(days, float) and pd.isna(days)):
        return "No contact recorded"
    days = int(days)
    return "today" if days == 0 else "1 day ago" if days == 1 else f"{days} days ago"
