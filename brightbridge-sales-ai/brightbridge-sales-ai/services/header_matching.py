"""Fuzzy header matching + value-based column inference + smart vocabulary normalisation.

Headers are matched to canonical fields with: exact → same-words-any-order → subset → typo-tolerant fuzzy match
(RapidFuzz when installed, difflib otherwise). A global greedy assignment makes sure each column is used once and
the best match wins (so "Company Size" never steals the company_name slot).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import pandas as pd

try:  # optional, faster and better
    from rapidfuzz import fuzz as _fuzz

    def _ratio(a: str, b: str) -> float:
        return max(_fuzz.ratio(a, b), _fuzz.token_sort_ratio(a, b))
except Exception:  # pragma: no cover - fallback keeps the app working without the package
    def _ratio(a: str, b: str) -> float:
        ta, tb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
        return 100 * max(SequenceMatcher(None, a, b).ratio(), SequenceMatcher(None, ta, tb).ratio())

ACCEPT = 85
_NOISE = {"inr", "rs", "usd", "in", "of", "the", "a", "an", "total"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_header(h: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(h))
    s = re.sub(r"[^A-Za-z0-9]+", " ", s).lower().strip()
    return " ".join(t for t in s.split() if t not in _NOISE)


def header_score(col: str, syn: str) -> float:
    c, s = clean_header(col), clean_header(syn)
    if not c or not s:
        return 0.0
    if c == s:
        return 100.0
    ct, st = c.split(), s.split()
    if set(ct) == set(st):
        return 97.0
    if set(st) <= set(ct) and len(ct) - len(st) <= 2:
        return 90.0 - 3 * (len(ct) - len(st))
    if set(ct) <= set(st) and len(st) - len(ct) <= 1 and len(c) >= 4:
        return 86.0
    if len(c) >= 4 and len(s) >= 4:
        r = _ratio(c, s)
        return r if r >= ACCEPT else 0.0
    return 0.0


def match_headers(columns: list[str], fields: dict[str, list[str]]) -> dict[str, str | None]:
    """fields: canonical -> synonyms. Returns canonical -> original column (or None). Global greedy best-first."""
    cands = []
    for order, (fld, syns) in enumerate(fields.items()):
        names = [fld.replace("_", " ")] + list(syns)
        for col in columns:
            best = max((header_score(col, n) for n in names), default=0.0)
            if best >= ACCEPT:
                cands.append((best, -order, fld, col))
    cands.sort(reverse=True)
    mapping: dict[str, str | None] = {f: None for f in fields}
    used_cols: set = set()
    for score, _, fld, col in cands:
        if mapping[fld] is None and col not in used_cols:
            mapping[fld] = col
            used_cols.add(col)
    return mapping


# ------------------------------------------------------------------------------ vocabulary normalisers
def normalize_stage(raw: str, strict: bool = False) -> str | None:
    """strict=True ignores ambiguous words (new/lead/qualified) so lead *statuses* are not mistaken for deal stages."""
    s = re.sub(r"[^a-z]+", " ", str(raw).lower()).strip()
    if not s:
        return None
    if strict:
        for key, stage in (("won", "Closed Won"), ("lost", "Closed Lost"), ("negotiat", "Negotiation"), ("propos", "Proposal"),
                           ("qualification", "Qualification"), ("prospect", "Prospecting")):
            if key in s:
                return stage
        return None
    rules = [("won", "Closed Won"), ("lost", "Closed Lost"), ("negotiat", "Negotiation"), ("propos", "Proposal"), ("quot", "Proposal"),
             ("qualif", "Qualification"), ("prospect", "Prospecting"), ("discover", "Qualification"), ("new", "Prospecting"), ("lead", "Prospecting")]
    for key, stage in rules:
        if key in s:
            return stage
    if len(s) >= 5:
        best = max(("Prospecting", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"), key=lambda c: _ratio(s, c.lower()))
        if _ratio(s, best.lower()) >= 85:
            return best
    return None


def normalize_activity(raw: str) -> str | None:
    s = re.sub(r"[^a-z]+", " ", str(raw).lower()).strip()
    if not s:
        return None
    rules = [("repl", "Email Replied"), ("respon", "Email Replied"), ("open", "Email Opened"), ("meet", "Meeting"), ("visit", "Meeting"),
             ("zoom", "Meeting"), ("video", "Meeting"), ("demo", "Demo"), ("present", "Demo"), ("call", "Call"), ("phone", "Call"), ("dial", "Call"),
             ("whatsapp", "Note"), ("sms", "Note"), ("note", "Note"), ("remark", "Note"), ("comment", "Note"), ("mail", "Email Sent"), ("sent", "Email Sent")]
    for key, kind in rules:
        if key in s:
            return kind
    return None


def normalize_status(raw: str) -> str | None:
    s = re.sub(r"[^a-z]+", " ", str(raw).lower()).strip()
    for key, val in (("unqualif", "Unqualified"), ("disqualif", "Unqualified"), ("qualif", "Qualified"), ("contact", "Contacted"), ("new", "New"), ("open", "New")):
        if key in s:
            return val
    return None


def vocab_ratio(values: pd.Series, normalizer, sample: int = 200) -> float:
    vals = [str(v).strip() for v in values.head(sample) if str(v).strip() and str(v).strip().lower() != "nan"]
    if not vals:
        return 0.0
    return sum(1 for v in vals if normalizer(v)) / len(vals)


def email_ratio(values: pd.Series, sample: int = 200) -> float:
    vals = [str(v).strip() for v in values.head(sample) if str(v).strip() and str(v).strip().lower() != "nan"]
    return (sum(1 for v in vals if EMAIL_RE.match(v)) / len(vals)) if vals else 0.0
