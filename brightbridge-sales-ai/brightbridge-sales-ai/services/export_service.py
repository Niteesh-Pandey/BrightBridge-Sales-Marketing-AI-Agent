"""CSV / XLSX export with spreadsheet-formula-injection protection."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

import config

_FORMULA = re.compile(r"^[=+\-@\t\r]")


def _protect(df: pd.DataFrame) -> pd.DataFrame:
    """Prefix text cells that start with = + - @ so Excel/Sheets never run them as formulas."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or str(out[col].dtype).startswith("str"):
            out[col] = out[col].map(lambda v: "'" + v if isinstance(v, str) and _FORMULA.match(v) else v)
    return out


def export_frame(df: pd.DataFrame, name: str, fmt: str = "csv", out_dir: Path | None = None) -> str | None:
    """Write df to data/exports and return the file path (None when there is nothing to export)."""
    if df is None or len(df) == 0:
        return None
    fmt = (fmt or "csv").lower()
    if fmt not in ("csv", "xlsx"):
        raise ValueError("Format must be csv or xlsx")
    out_dir = out_dir or config.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_") or "export"
    path = out_dir / f"{safe}_{datetime.now():%Y%m%d_%H%M%S}.{fmt}"
    data = _protect(df)
    if fmt == "csv":
        data.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        data.to_excel(path, index=False, engine="openpyxl")
    return str(path)
