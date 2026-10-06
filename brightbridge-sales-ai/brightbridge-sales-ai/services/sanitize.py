"""Defensive data sanitisation + safe-call helpers.

Every numeric/monetary/probability value that reaches a calculation or a chart goes through here, so a single
bad cell (text, None, NaN, "₹3,20,000", "1.5L") can never raise a TypeError in the UI.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable, Iterable

import pandas as pd

log = logging.getLogger("brightbridge")

_STRIP = re.compile(r"[₹$€£\s,]|(?i:rs\.?|inr|usd|rupees?)")
_SUFFIX = {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5, "l": 1e5, "k": 1e3, "m": 1e6, "mn": 1e6}
_NUM = re.compile(r"^\(?-?\d*\.?\d+(?:e[+-]?\d+)?\)?(crore|cr|lakh|lac|l|k|mn|m)?$", re.I)


def to_number(value: Any, default: float = 0.0, non_negative: bool = False) -> float:
    """Robustly convert anything to a finite float. Never raises; falls back to `default`."""
    try:
        if value is None or isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            out = float(value)
        else:
            s = _STRIP.sub("", str(value)).strip().lower()
            if s in ("", "nan", "none", "null", "n/a", "na", "-", "--"):
                return default
            m = _NUM.match(s)
            if not m:
                return default
            neg = s.startswith("(") or s.startswith("-")
            suffix = m.group(1)
            core = re.sub(r"[()\-]", "", s[: len(s) - len(suffix)] if suffix else s)
            out = float(core) * _SUFFIX.get(suffix or "", 1.0)
            out = -out if neg else out
        if math.isnan(out) or math.isinf(out):
            return default
        return max(out, 0.0) if non_negative else out
    except Exception:
        return default


def to_probability(value: Any, default: float = 0.0) -> float:
    """Percent value 0-100 ("60%", 0.6 -> handled by the column-level helper)."""
    return min(100.0, max(0.0, to_number(str(value).replace("%", ""), default)))


def clean_numeric_series(s: pd.Series, default: float = 0.0, non_negative: bool = False) -> pd.Series:
    """Strip currency symbols/commas/spaces, coerce to numbers, replace NaN/None with `default`."""
    return pd.Series([to_number(v, default, non_negative) for v in s], index=s.index, dtype="float64")


def clean_numeric_columns(df: pd.DataFrame, columns: Iterable[str], default: float = 0.0, non_negative: bool = False) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = clean_numeric_series(out[col], default, non_negative)
    return out


def safe_str(value: Any, default: str = "") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return str(value)


def safe_call(fn: Callable, default: Any, label: str = "calculation", *args, **kwargs) -> Any:
    """Run fn; on ANY error log it internally and return the safe default (no TypeError reaches the UI)."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.warning("%s failed (%s: %s) — using fallback value", label, type(exc).__name__, str(exc)[:200])
        return default


def safe_div(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    n, d = to_number(numerator), to_number(denominator)
    return n / d if d else default
