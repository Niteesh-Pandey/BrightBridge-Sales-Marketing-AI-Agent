"""Multi-file import engine.

* Upload ALL files at once; each is classified by CONTENT (fuzzy headers + value inference), not by file name.
* Strict dependency order: campaigns → companies → contacts & leads → deals → activities.
* Foreign keys are resolved by company id → normalised company name; unknown companies/contacts/leads are
  created automatically (with an informational notice) instead of failing.
* Duplicate handling modes: SKIP (keep existing), UPSERT (update existing), OVERWRITE (replace entity data, with backup).
* Nothing is written until the user confirms; everything is one atomic transaction.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import config
from database.db import Database
from database.models import STAGES
from services.header_matching import (EMAIL_RE, email_ratio, match_headers, normalize_activity, normalize_stage, normalize_status,
                                      vocab_ratio)
from services.import_service import ImportError_, read_file
from services.sanitize import to_number

MAX_FILES = 12
IMPORT_ORDER = ["campaigns", "companies", "contacts", "leads", "opportunities", "activities"]
MODES = ("SKIP", "UPSERT", "OVERWRITE")
MODE_HELP = {"SKIP": "Keep existing records; ignore uploaded rows that already exist (safest).",
             "UPSERT": "Update existing records with the uploaded values (empty cells never erase data); add new ones.",
             "OVERWRITE": "DELETE existing data of the uploaded entity types first (a backup ZIP is saved), then insert the uploaded data."}
CASCADE = {"companies": {"contacts", "leads", "opportunities", "activities"}, "leads": {"opportunities", "activities"}}

_CO = ["company", "company name", "organisation", "organization", "org name", "org", "account", "account name", "business name", "firm",
       "client", "client name", "customer", "customer name", "name of company", "company title"]
_CO_ID = ["company id", "account id", "org id", "organization id", "organisation id", "customer id", "client id"]
_PERSON = ["contact", "contact name", "name", "full name", "person", "person name", "first last name", "lead name", "contact person"]
_EMAIL = ["email", "e-mail", "contact email", "email address", "mail", "email id"]
_VALUE = ["value", "deal value", "amount", "deal amount", "revenue", "value inr", "deal size", "opportunity value", "price", "worth",
          "contract value", "order value", "expected revenue", "deal worth"]

SPECS: dict[str, dict] = {
    "companies": {"file": "companies.csv", "required": ["company_name"], "purpose": "Company details (industry, city). Optional: companies are auto-created from the other files.",
                  "fields": {"company_name": _CO + ["name", "company name"], "company_id": _CO_ID,
                             "industry": ["industry", "sector", "vertical", "business type", "segment"], "city": ["city", "location", "town", "hq city"],
                             "state": ["state", "region", "province"], "size": ["size", "company size", "employees", "employee count", "headcount"],
                             "website": ["website", "url", "web", "site", "domain"], "created_date": ["created", "created date", "date added", "created at"]}},
    "contacts": {"file": "contacts.csv", "required": ["company_name", "contact_name"], "purpose": "People at each company (name, title, e-mail, phone). Optional: auto-created from leads.",
                 "fields": {"company_name": _CO, "company_id": _CO_ID, "contact_name": _PERSON,
                            "job_title": ["title", "job title", "designation", "role", "position"], "email": _EMAIL,
                            "phone": ["phone", "mobile", "phone number", "contact number", "telephone", "mobile number", "cell"]}},
    "campaigns": {"file": "campaigns.csv", "required": ["campaign_name"], "purpose": "Marketing campaigns (channel, budget, clicks). Needed for campaign charts and 'which campaign gave most leads'.",
                  "fields": {"campaign_name": ["campaign", "campaign name", "name", "title"], "channel": ["channel", "medium", "platform"],
                             "start_date": ["start", "start date", "begin", "launch date"], "end_date": ["end", "end date", "finish"],
                             "budget_inr": ["budget", "budget inr", "spend", "cost", "amount spent"], "impressions": ["impressions", "views", "reach"],
                             "clicks": ["clicks", "click count"]}},
    "leads": {"file": "leads.csv", "required": ["company_name"], "purpose": "THE MAIN FILE. One row per lead. With only this file you already get the lead list, CRM and basic scoring.",
              "fields": {"company_name": _CO, "company_id": _CO_ID, "contact_name": _PERSON, "contact_email": _EMAIL,
                         "lead_source": ["source", "lead source", "origin", "channel", "lead origin"], "lead_status": ["status", "lead status"],
                         "campaign_name": ["campaign", "campaign name"], "owner": ["owner", "assigned to", "sales rep", "rep", "account owner", "lead owner"],
                         "created_date": ["created", "created date", "date added", "created at", "lead date"], "notes": ["notes", "remarks", "comment", "comments"]}},
    "opportunities": {"file": "opportunities.csv", "required": ["company_name", "stage"], "purpose": "Deals / pipeline (stage, value). Needed for pipeline value, won revenue and deal value in the score.",
                      "fields": {"company_name": _CO, "company_id": _CO_ID, "contact_name": ["contact", "contact name", "person", "person name"],
                                 "contact_email": _EMAIL, "deal_name": ["deal", "deal name", "opportunity", "opportunity name", "project", "name", "title"],
                                 "stage": ["stage", "deal stage", "pipeline stage", "opportunity stage", "status", "stage name", "pipeline status"],
                                 "value_inr": _VALUE, "win_probability": ["win probability", "probability", "stage probability", "close probability", "likelihood", "win rate", "chance"],
                                 "expected_close_date": ["expected close", "close date", "expected close date", "closing date", "expected closing"],
                                 "created_date": ["created", "created date", "date added", "created at"], "closed_date": ["closed", "closed date", "closed at", "won date"]}},
    "activities": {"file": "activities.csv", "required": ["company_name", "activity_type", "activity_date"], "purpose": "Calls, meetings, e-mails, demos with dates. Needed for engagement, recency, follow-ups and AI summaries.",
                   "fields": {"company_name": _CO, "company_id": _CO_ID, "contact_name": ["contact", "contact name", "person", "person name"],
                              "contact_email": _EMAIL, "deal_name": ["deal", "deal name", "opportunity", "opportunity name", "project"],
                              "activity_type": ["type", "activity type", "activity", "interaction", "interaction type", "kind", "action", "touch type", "communication type"],
                              "activity_date": ["date", "activity date", "when", "interaction date", "contacted on", "done on", "activity on"],
                              "outcome": ["outcome", "result", "disposition"],
                              "notes": ["notes", "remarks", "comment", "comments", "description", "summary"],
                              "follow_up_date": ["follow up", "follow up date", "followup date", "next follow up", "next step date", "next action date"]}},
}
LABELS = {t: s["file"] for t, s in SPECS.items()}
SIGNATURE = {"companies": {"industry", "city", "state", "size", "website"}, "contacts": {"job_title", "phone"},
             "campaigns": {"channel", "budget_inr", "impressions", "clicks", "start_date", "end_date"},
             "leads": {"lead_source", "lead_status", "owner", "notes"},
             "opportunities": {"stage", "value_inr", "win_probability", "expected_close_date", "closed_date"},
             "activities": {"activity_type", "activity_date", "outcome", "follow_up_date"}}
_FILENAME_HINTS = [("campaigns", ("campaign", "marketing")), ("opportunities", ("opportunit", "deal", "pipeline")),
                   ("activities", ("activit", "interaction", "touchpoint", "call_log", "calls", "communication")),
                   ("contacts", ("contact", "people", "person")), ("leads", ("lead", "prospect")),
                   ("companies", ("compan", "account", "organi", "client"))]

SESSION: dict = {"result": None, "files": []}      # server-side import state (cleared by reset_import_session)


# ------------------------------------------------------------------------------------------ detection
def _company_ok(m: dict) -> bool:
    return bool(m.get("company_name") or m.get("company_id"))


def _missing_required(table: str, mapping: dict) -> list[str]:
    miss = []
    for f in SPECS[table]["required"]:
        if f == "company_name":
            if not _company_ok(mapping):
                miss.append("company_name")
        elif not mapping.get(f):
            miss.append(f)
    return miss


def detect_mapping(table: str, columns: list[str], df: pd.DataFrame | None = None) -> dict[str, str | None]:
    """Fuzzy header → canonical field mapping, validated/extended by what the column VALUES look like."""
    m = match_headers(columns, SPECS[table]["fields"])
    if df is not None:
        from services.header_matching import clean_header
        st = m.get("stage")
        if st and "stage" not in clean_header(st) and vocab_ratio(df[st], lambda v: normalize_stage(v, strict=True)) < 0.5:
            m["stage"] = None                                    # e.g. a lead "Status" column is not a deal stage
        at = m.get("activity_type")
        if at and not any(w in clean_header(at) for w in ("activity", "interaction")) and vocab_ratio(df[at], normalize_activity) < 0.5:
            m["activity_type"] = None
        used = {v for v in m.values() if v}
        free = [c for c in columns if c not in used]
        if "stage" in m and not m["stage"]:
            hit = next((c for c in free if vocab_ratio(df[c], lambda v: normalize_stage(v, strict=True)) >= 0.6), None)
            if hit:
                m["stage"] = hit; free.remove(hit)
        if "activity_type" in m and not m["activity_type"]:
            hit = next((c for c in free if vocab_ratio(df[c], normalize_activity) >= 0.6), None)
            if hit:
                m["activity_type"] = hit; free.remove(hit)
        for emailf in ("contact_email", "email"):
            if emailf in m and not m[emailf]:
                hit = next((c for c in free if email_ratio(df[c]) >= 0.6), None)
                if hit:
                    m[emailf] = hit; free.remove(hit)
    return m


def _filename_hint(filename: str) -> str | None:
    stem = re.sub(r"[^a-z0-9]+", "_", Path(filename).stem.lower())
    return next((t for t, kws in _FILENAME_HINTS if any(k in stem for k in kws)), None)


def classify(filename: str, df: pd.DataFrame) -> tuple[str | None, dict, str]:
    """Pick the table type from the CONTENT. File name is only a tie-breaker (+12) — two table-specific
    columns (e.g. stage + value) outweigh it, so a 'companies.csv' that holds deals is treated as deals."""
    cols, hint = list(df.columns), _filename_hint(filename)
    best, partial = None, None
    for t in SPECS:
        m = detect_mapping(t, cols, df)
        hits = sorted(f for f in SIGNATURE[t] if m.get(f))
        cov = sum(1 for v in m.values() if v) / len(m)
        score = 10 * len(hits) + 5 * cov + (12 if t == hint else 0)
        miss = _missing_required(t, m)
        if miss:
            if any(m.values()) and (partial is None or score > partial[0]):      # only guess when some column really matched
                partial = (score, t, miss)
            continue
        if best is None or score > best[0]:
            best = (score, t, m, hits)
    if best:
        _, t, m, hits = best
        why = f"matched {len(hits)} {t}-specific column(s): {', '.join(hits)}" if hits else f"only generic columns found; file name suggests {t}" if t == hint else "generic columns only"
        if hint and hint != t:
            why += f" (content overrides the file name “{filename}”)"
        return t, m, why
    if partial:
        _, t, miss = partial
        return None, {}, f"Looks like {LABELS[t]} but required column(s) not found: {', '.join(miss)}. Columns in file: {', '.join(cols[:12])}"
    return None, {}, "Could not tell what this file contains. Columns found: " + ", ".join(cols[:12])


@dataclass
class FileInfo:
    name: str
    table: str | None = None
    df: pd.DataFrame | None = None
    mapping: dict = field(default_factory=dict)
    error: str = ""
    reason: str = ""

    @property
    def missing(self) -> list[str]:
        return _missing_required(self.table, self.mapping) if self.table else []

    @property
    def ignored(self) -> list[str]:
        used = {v for v in self.mapping.values() if v}
        return [c for c in (self.df.columns if self.df is not None else []) if c not in used]


def analyse(paths: list[str | Path]) -> list[FileInfo]:
    """Read and classify every uploaded file. One bad file never breaks the others."""
    if not paths:
        raise ImportError_("Please upload at least one CSV/XLSX file.")
    if len(paths) > MAX_FILES:
        raise ImportError_(f"Too many files (max {MAX_FILES}).")
    infos = []
    for p in paths:
        info = FileInfo(Path(p).name)
        try:
            info.df = read_file(p)
            info.table, info.mapping, info.reason = classify(info.name, info.df)
            if not info.table:
                info.error = info.reason
        except ImportError_ as exc:
            info.error = str(exc)
        infos.append(info)
    SESSION["files"] = [i.name for i in infos]
    return infos


def detection_table(infos: list[FileInfo]) -> pd.DataFrame:
    rows = []
    for i in infos:
        rows.append({"File": i.name, "Detected as": LABELS.get(i.table, "— not recognised —"), "Rows": len(i.df) if i.df is not None else 0,
                     "Why": i.reason or i.error or "—",
                     "Columns matched": ", ".join(f"{f}←{c}" for f, c in i.mapping.items() if c) or "—",
                     "Ignored columns": ", ".join(i.ignored[:8]) or "—"})
    return pd.DataFrame(rows)


def checklist_markdown(infos: list[FileInfo]) -> str:
    have = {i.table for i in infos if i.table and not i.error and not i.missing}
    lines = ["| File | Status | What it gives you |", "|---|---|---|"]
    for t in ["leads", "companies", "contacts", "opportunities", "activities", "campaigns"]:
        s = SPECS[t]
        lines.append(f"| `{s['file']}` | {'✅ found' if t in have else '⬜ not uploaded'} | {s['purpose']} |")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------ result container
@dataclass
class BundleResult:
    mode: str = "SKIP"
    campaigns: list = field(default_factory=list)
    companies: list = field(default_factory=list)
    contacts: list = field(default_factory=list)
    leads: list = field(default_factory=list)
    opportunities: list = field(default_factory=list)
    activities: list = field(default_factory=list)
    campaigns_updates: list = field(default_factory=list)
    companies_updates: list = field(default_factory=list)
    contacts_updates: list = field(default_factory=list)
    leads_updates: list = field(default_factory=list)
    opportunities_updates: list = field(default_factory=list)
    activities_updates: list = field(default_factory=list)
    auto_companies: dict = field(default_factory=dict)      # lower -> display name
    auto_contacts: list = field(default_factory=list)
    auto_leads: list = field(default_factory=list)
    wiped: set = field(default_factory=set)                 # tables explicitly overwritten
    errors: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notices: list = field(default_factory=list)             # informational (auto-created links etc.)
    summary: list = field(default_factory=list)

    @property
    def total_valid(self) -> int:
        """New records that will be inserted."""
        return sum(len(getattr(self, t)) for t in IMPORT_ORDER)

    @property
    def total_updates(self) -> int:
        return sum(len(getattr(self, f"{t}_updates")) for t in IMPORT_ORDER)

    @property
    def ok(self) -> bool:
        return bool(self.total_valid or self.total_updates or self.auto_leads)

    def errors_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.errors, columns=["file", "row", "field", "problem"])

    def duplicates_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.duplicates, columns=["file", "row", "record", "reason"])

    def summary_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.summary, columns=["file", "type", "rows", "new", "updated", "with errors", "duplicates"])

    def headline(self) -> str:
        n_err = len({(e["file"], e["row"]) for e in self.errors})
        dup_word = "skipped" if self.mode == "SKIP" else "merged"
        parts = [f"Mode {self.mode}", f"{self.total_valid} new", f"{self.total_updates} to update", f"{n_err} row(s) with errors (skipped)", f"{len(self.duplicates)} duplicate(s) ({dup_word})"]
        auto = len(self.auto_companies) + len(self.auto_contacts) + len(self.auto_leads)
        if auto:
            parts.append(f"{auto} linked record(s) will be created automatically")
        return " · ".join(parts)


# ------------------------------------------------------------------------------------------ value parsing
def parse_number(raw, label: str, notes: list, non_negative: bool = True):
    """Blank -> None. Garbage text -> 0 with a warning (never an exception)."""
    s = str(raw if raw is not None else "").strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return None
    v = to_number(s, default=float("nan"), non_negative=non_negative)
    if v != v:
        notes.append(f"{label} “{s[:20]}” is not a number — stored as 0")
        return 0.0
    return v


def parse_date(raw) -> str | None:
    s = str(raw if raw is not None else "").strip()
    if s == "" or s.lower() in ("nan", "none", "nat"):
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    ts = pd.to_datetime(s, dayfirst=True, errors="coerce")
    if pd.isna(ts):
        raise ValueError("invalid date (use YYYY-MM-DD or DD/MM/YYYY)")
    return ts.date().isoformat()


def _clean(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "").strip())


def _idlike(ref: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]{0,5}[-_ ]?\d+", ref.strip()))


def _canon(email: str, name: str) -> str:
    return (email or name or "").strip().lower()


# ------------------------------------------------------------------------------------------ existing data
def closure(tables: set) -> set:
    out = set(tables)
    for t in tables:
        out |= CASCADE.get(t, set())
    return out


def _load_existing(db: Database | None, wiped: set) -> dict:
    ex = {"companies": set(), "campaigns": set(), "contact_idx": {}, "contacts": set(), "leads": set(), "opportunities": set(),
          "activities": set(), "leads_by_company": {}}
    if db is None:
        return ex
    blank_contacts = "contacts" in wiped
    if "companies" not in wiped:
        ex["companies"] = {r["n"] for r in db.query("SELECT LOWER(name) AS n FROM companies")}
    if "campaigns" not in wiped:
        ex["campaigns"] = {r["n"] for r in db.query("SELECT LOWER(name) AS n FROM campaigns")}
    if "contacts" not in wiped:
        for r in db.query("SELECT LOWER(c.name) AS co, ct.email AS em, ct.full_name AS nm FROM contacts ct JOIN companies c ON c.id = ct.company_id"):
            canon = _canon((r["em"] or "").lower(), (r["nm"] or "").lower())
            ex["contacts"].add((r["co"], canon))
            if r["em"]:
                ex["contact_idx"][(r["co"], "e", r["em"].lower())] = canon
            ex["contact_idx"][(r["co"], "n", (r["nm"] or "").lower())] = canon
    base = ("FROM {t} x JOIN leads l ON l.id = x.lead_id JOIN companies c ON c.id = l.company_id LEFT JOIN contacts ct ON ct.id = l.contact_id")

    def ckey(r):
        return "" if blank_contacts else _canon((r["em"] or "").lower(), (r["nm"] or "").lower())
    if "leads" not in wiped:
        for r in db.query("SELECT LOWER(c.name) AS co, ct.email AS em, ct.full_name AS nm FROM leads l JOIN companies c ON c.id = l.company_id "
                          "LEFT JOIN contacts ct ON ct.id = l.contact_id"):
            canon = ckey(r)
            ex["leads"].add((r["co"], canon))
            lst = ex["leads_by_company"].setdefault(r["co"], [])
            if canon not in lst:
                lst.append(canon)
        if "opportunities" not in wiped:
            for r in db.query("SELECT LOWER(c.name) AS co, ct.email AS em, ct.full_name AS nm, LOWER(x.name) AS deal " + base.format(t="opportunities")):
                ex["opportunities"].add((r["co"], ckey(r), r["deal"]))
        if "activities" not in wiped:
            for r in db.query("SELECT LOWER(c.name) AS co, ct.email AS em, ct.full_name AS nm, x.type AS ty, x.activity_date AS dt, LOWER(COALESCE(x.outcome,'')) AS oc "
                              + base.format(t="activities")):
                ex["activities"].add((r["co"], ckey(r), r["ty"], str(r["dt"])[:10], r["oc"]))
    return ex


# ------------------------------------------------------------------------------------------ validation
def validate_bundle(infos: list[FileInfo], db: Database | None = None, today: date | None = None, mode: str = "SKIP") -> BundleResult:
    mode = mode.upper() if mode else "SKIP"
    if mode not in MODES:
        raise ImportError_(f"Unknown duplicate mode '{mode}'. Use one of: {', '.join(MODES)}.")
    today = today or date.today()
    res = BundleResult(mode=mode)
    by_table: dict[str, list[FileInfo]] = {}
    for i in infos:
        if i.table and i.df is not None and not i.missing:
            by_table.setdefault(i.table, []).append(i)
    if mode == "OVERWRITE":
        res.wiped = set(by_table) & set(IMPORT_ORDER)
    ex = _load_existing(db, closure(res.wiped))

    known_companies, known_campaigns = set(ex["companies"]), set(ex["campaigns"])
    contact_idx, known_contacts = dict(ex["contact_idx"]), set(ex["contacts"])
    known_leads, leads_by_company = set(ex["leads"]), {k: list(v) for k, v in ex["leads_by_company"].items()}
    known_opps = set(ex["opportunities"])
    pending: dict[str, dict] = {t: {} for t in IMPORT_ORDER}
    ext_names: dict[str, str] = {}
    notified: set = set()
    stats: dict[str, dict] = {}

    for i in infos:                                           # unreadable / unrecognised files
        if not (i.table and i.df is not None and not i.missing):
            res.errors.append({"file": i.name, "row": 0, "field": "(file)", "problem": i.error or "Missing required column(s): " + ", ".join(i.missing)})
            res.summary.append({"file": i.name, "type": LABELS.get(i.table, "unrecognised"), "rows": len(i.df) if i.df is not None else 0,
                                "new": 0, "updated": 0, "with errors": len(i.df) if i.df is not None else 0, "duplicates": 0})

    # company id -> name lookup from any companies file (resolved by id first, then by name)
    for info in by_table.get("companies", []):
        idc, nmc = info.mapping.get("company_id"), info.mapping.get("company_name")
        if idc and nmc:
            for _, raw in info.df.iterrows():
                if _clean(raw[idc]) and _clean(raw[nmc]):
                    ext_names[_clean(raw[idc]).lower()] = _clean(raw[nmc])

    def rows_of(info):
        fields = SPECS[info.table]["fields"]
        for idx, raw in info.df.iterrows():
            yield int(idx) + 2, {f: (str(raw[info.mapping[f]]).strip() if info.mapping.get(f) else "") for f in fields}

    def start(info):
        stats[info.name] = {"file": info.name, "type": LABELS[info.table], "rows": len(info.df), "new": 0, "updated": 0, "with errors": set(), "duplicates": 0}
        return stats[info.name]

    def err(info, st, row, fld, msg):
        res.errors.append({"file": info.name, "row": row, "field": fld, "problem": msg})
        st["with errors"].add(row)

    def notice(msg, key=None):
        if key is not None:
            if key in notified:
                return
            notified.add(key)
        res.notices.append(msg)

    def warn(msg):
        res.warnings.append(msg)

    def date_field(rec, key, errs):
        try:
            rec[key] = parse_date(rec.get(key, ""))
        except ValueError as e:
            errs.append((key, f"'{str(rec.get(key))[:20]}': {e}"))
            rec[key] = None

    def clean_email(info, row, rec, key):
        e = rec.get(key, "").strip()
        if e and not EMAIL_RE.match(e):
            warn(f"{info.name} row {row}: invalid e-mail “{e[:40]}” ignored (row still imported without it).")
            e = ""
        rec[key] = e
        return e

    def company_of(info, row, rec) -> str:
        name, ref = _clean(rec.get("company_name", "")), _clean(rec.get("company_id", ""))
        if not name and ref:
            mapped = ext_names.get(ref.lower())
            if mapped:
                return mapped
            if _idlike(ref):
                notice(f"{info.name}: company id “{ref}” has no matching company in a companies file — placeholder company “Company {ref}” is used.", ("id", ref.lower()))
                return f"Company {ref}"
            return ref
        return name

    def add_company(display, source):
        lo = display.lower()
        if lo not in known_companies:
            known_companies.add(lo)
            res.auto_companies[lo] = display
            notice(f"Company “{display}” (used in {source}) was not found — created automatically so the records could be linked.", ("co", lo))
        return lo

    def resolve_contact(co, email, name):
        if email:
            return contact_idx.get((co, "e", email.lower()))
        if name:
            return contact_idx.get((co, "n", name.lower()))
        return ""

    def register_contact(co, name, email):
        canon = (email or name).lower()
        known_contacts.add((co, canon))
        if email:
            contact_idx[(co, "e", email.lower())] = canon
        contact_idx[(co, "n", name.lower())] = canon
        return canon

    def make_auto_lead(co, display, canon, why):
        res.auto_leads.append({"company_name": display, "_co": co, "_canon": canon, "lead_source": "Imported", "lead_status": "New"})
        known_leads.add((co, canon))
        lst = leads_by_company.setdefault(co, [])
        if canon not in lst:
            lst.append(canon)
        notice(why, ("lead", co, canon))

    def ensure_lead(info, row, rec, display) -> str:
        """Foreign-key fallback: find the lead for this company/contact, creating company/contact/lead if needed."""
        co = add_company(display, info.name)
        email, cname = rec.get("contact_email", ""), _clean(rec.get("contact_name", ""))
        cands = leads_by_company.get(co, [])
        if email or cname:
            canon = resolve_contact(co, email, cname)
            if canon is None:
                canon = register_contact(co, cname or email, email)
                res.auto_contacts.append({"company_name": display, "contact_name": cname or email, "email": email, "job_title": "", "phone": "", "_co": co, "_canon": canon})
                notice(f"{info.name} row {row}: contact “{cname or email}” was not found — created under “{display}”.")
            if canon not in cands:
                make_auto_lead(co, display, canon, f"{info.name} row {row}: no lead existed for “{cname or email}” at “{display}” — a lead was created automatically.")
            return canon
        if cands:
            if len(cands) > 1:
                warn(f"{info.name} row {row}: “{display}” has {len(cands)} leads and no contact given — linked to the first. Add contact_email to be exact.")
            return cands[0]
        make_auto_lead(co, display, "", f"{info.name}: “{display}” had no lead — a placeholder lead was created automatically for its deals/activities.")
        return ""

    ex_sets = {"companies": ex["companies"], "campaigns": ex["campaigns"], "contacts": ex["contacts"], "leads": ex["leads"],
               "opportunities": ex["opportunities"], "activities": ex["activities"]}
    SKIPPED = object()

    def place(table, key, rec, info, st, row, label, merge_fields):
        """Dedup decision for one valid row according to the selected mode."""
        if key in pending[table]:
            prev = pending[table][key]
            if mode == "SKIP" or prev is SKIPPED:
                res.duplicates.append({"file": info.name, "row": row, "record": label, "reason": "Duplicate inside the uploaded files (skipped)"})
            else:
                for f in merge_fields:
                    if rec.get(f) not in (None, ""):
                        prev[f] = rec[f]
                res.duplicates.append({"file": info.name, "row": row, "record": label, "reason": "Duplicate inside the uploaded files (merged into the earlier row)"})
            st["duplicates"] += 1
            return "dup"
        if key in ex_sets[table]:
            if mode == "UPSERT":
                getattr(res, f"{table}_updates").append(rec)
                pending[table][key] = rec
                st["updated"] += 1
                return "updated"
            pending[table][key] = SKIPPED
            res.duplicates.append({"file": info.name, "row": row, "record": label, "reason": "Already exists in the CRM (skipped — choose UPSERT to update it)"})
            st["duplicates"] += 1
            return "dup"
        getattr(res, table).append(rec)
        pending[table][key] = rec
        st["new"] += 1
        return "new"

    # ---- campaigns
    for info in by_table.get("campaigns", []):
        st = start(info)
        for row, r in rows_of(info):
            errs, notes = [], []
            name = _clean(r["campaign_name"])
            if not name:
                errs.append(("campaign_name", "Campaign name is empty"))
            for k in ("start_date", "end_date"):
                date_field(r, k, errs)
            for k in ("budget_inr", "impressions", "clicks"):
                r[k] = parse_number(r[k], k, notes)
            if errs:
                for f_, m in errs:
                    err(info, st, row, f_, m)
                continue
            for n in notes:
                warn(f"{info.name} row {row}: {n}")
            known_campaigns.add(name.lower())
            place("campaigns", name.lower(), {**r, "campaign_name": name}, info, st, row, name, ["channel", "start_date", "end_date", "budget_inr", "impressions", "clicks"])

    # ---- companies
    for info in by_table.get("companies", []):
        st = start(info)
        for row, r in rows_of(info):
            errs = []
            name = company_of(info, row, r)
            if not name:
                errs.append(("company_name", "Company name is empty"))
            date_field(r, "created_date", errs)
            if errs:
                for f_, m in errs:
                    err(info, st, row, f_, m)
                continue
            lo = name.lower()
            known_companies.add(lo)
            res.auto_companies.pop(lo, None)
            place("companies", lo, {**r, "company_name": name}, info, st, row, name, ["industry", "city", "state", "size", "website"])

    # ---- contacts
    for info in by_table.get("contacts", []):
        st = start(info)
        for row, r in rows_of(info):
            errs = []
            company, cname = company_of(info, row, r), _clean(r["contact_name"])
            if not company:
                errs.append(("company_name", "Company name is empty"))
            if not cname:
                errs.append(("contact_name", "Contact name is empty"))
            if errs:
                for f_, m in errs:
                    err(info, st, row, f_, m)
                continue
            email = clean_email(info, row, r, "email")
            co = add_company(company, info.name)
            canon = contact_idx.get((co, "e", email.lower())) if email else None
            if canon is None:                                   # same person already known by name (maybe with a new e-mail)
                canon = contact_idx.get((co, "n", cname.lower()))
            if canon is None:
                canon = _canon(email.lower(), cname.lower())
            rec = {**r, "company_name": company, "contact_name": cname, "_co": co, "_canon": canon}
            place("contacts", (co, canon), rec, info, st, row, f"{cname} @ {company}", ["job_title", "phone", "email"])
            known_contacts.add((co, canon))
            contact_idx[(co, "n", cname.lower())] = canon
            if email:
                contact_idx[(co, "e", email.lower())] = canon

    # ---- leads
    for info in by_table.get("leads", []):
        st = start(info)
        for row, r in rows_of(info):
            errs = []
            company, cname = company_of(info, row, r), _clean(r["contact_name"])
            if not company:
                errs.append(("company_name", "Company name is empty"))
            date_field(r, "created_date", errs)
            if errs:
                for f_, m in errs:
                    err(info, st, row, f_, m)
                continue
            email = clean_email(info, row, r, "contact_email")
            status = ""
            if r["lead_status"]:
                status = normalize_status(r["lead_status"]) or ""
                if not status:
                    notice(f"{info.name} row {row}: unknown lead status “{r['lead_status'][:20]}” — set to New.")
                    status = "New"
            co = add_company(company, info.name)
            canon = ""
            if email or cname:
                canon = resolve_contact(co, email, cname)
                if canon is None:
                    canon = register_contact(co, cname or email, email)
                    res.auto_contacts.append({"company_name": company, "contact_name": cname or email, "email": email, "job_title": "", "phone": "", "_co": co, "_canon": canon})
            camp = _clean(r["campaign_name"])
            if camp and camp.lower() not in known_campaigns:
                warn(f"{info.name} row {row}: campaign “{camp}” is not in campaigns.csv or the CRM — lead imported without a campaign.")
                camp = ""
            rec = {**r, "company_name": company, "_co": co, "_canon": canon, "lead_status": status, "campaign_name": camp}
            place("leads", (co, canon), rec, info, st, row, f"{cname or email or '(no contact)'} @ {company}", ["lead_source", "lead_status", "owner", "notes", "campaign_name"])
            known_leads.add((co, canon))
            lst = leads_by_company.setdefault(co, [])
            if canon not in lst:
                lst.append(canon)

    # ---- opportunities
    for info in by_table.get("opportunities", []):
        st = start(info)
        prob_vals = [to_number(str(v).replace("%", ""), default=float("nan")) for v in (info.df[info.mapping["win_probability"]] if info.mapping.get("win_probability") else [])]
        prob_vals = [v for v in prob_vals if v == v]
        fractions = bool(prob_vals) and max(prob_vals) <= 1.0
        for row, r in rows_of(info):
            errs, notes = [], []
            company = company_of(info, row, r)
            if not company:
                errs.append(("company_name", "Company name is empty"))
            stage = None
            if r["stage"].strip():
                stage = normalize_stage(r["stage"])
                if not stage:
                    errs.append(("stage", f"Unknown stage '{r['stage'][:30]}' (allowed: {', '.join(STAGES)})"))
            else:
                stage = "Prospecting"
                notice(f"{info.name} row {row}: empty stage — set to Prospecting.")
            value = parse_number(r["value_inr"], "deal value", notes)
            prob = parse_number(str(r["win_probability"]).replace("%", ""), "win probability", notes)
            if prob is not None:
                prob = min(100.0, max(0.0, prob * 100 if fractions else prob))
            for k in ("expected_close_date", "created_date", "closed_date"):
                date_field(r, k, errs)
            if errs:
                for f_, m in errs:
                    err(info, st, row, f_, m)
                continue
            for n in notes:
                warn(f"{info.name} row {row}: {n}")
            clean_email(info, row, r, "contact_email")
            canon = ensure_lead(info, row, r, company)
            co = company.lower()
            deal = _clean(r["deal_name"]) or f"Deal - {company}"
            known_opps_key = (co, canon, deal.lower())
            rec = {**r, "company_name": company, "_co": co, "_canon": canon, "deal_name": deal, "stage": stage, "value_inr": value, "win_probability": prob}
            place("opportunities", known_opps_key, rec, info, st, row, f"{deal} @ {company}",
                  ["stage", "value_inr", "win_probability", "expected_close_date", "closed_date"])
            known_opps.add(known_opps_key)

    # ---- activities
    for info in by_table.get("activities", []):
        st = start(info)
        for row, r in rows_of(info):
            errs = []
            company = company_of(info, row, r)
            if not company:
                errs.append(("company_name", "Company name is empty"))
            atype = normalize_activity(r["activity_type"])
            if not atype:
                errs.append(("activity_type", f"Unknown type '{r['activity_type'][:25]}' (use: Email Sent, Email Opened, Email Replied, Call, Meeting, Demo, Note)"))
            date_field(r, "activity_date", errs)
            date_field(r, "follow_up_date", errs)
            if r.get("activity_date") is None and not any(f == "activity_date" for f, _ in errs):
                errs.append(("activity_date", "Activity date is empty"))
            if r.get("activity_date") and r["activity_date"] > today.isoformat():
                errs.append(("activity_date", "Date is in the future"))
            if errs:
                for f_, m in errs:
                    err(info, st, row, f_, m)
                continue
            clean_email(info, row, r, "contact_email")
            canon = ensure_lead(info, row, r, company)
            co = company.lower()
            deal = _clean(r["deal_name"])
            if deal and (co, canon, deal.lower()) not in known_opps:
                notice(f"{info.name} row {row}: deal “{deal}” was not found — activity linked to the lead only.")
                deal = ""
            key = (co, canon, atype, r["activity_date"], r["outcome"].lower())
            rec = {**r, "company_name": company, "_co": co, "_canon": canon, "activity_type": atype, "deal_name": deal}
            place("activities", key, rec, info, st, row, f"{atype} {r['activity_date']} @ {company}", ["notes", "follow_up_date"])

    for s in stats.values():
        res.summary.append({**s, "with errors": len(s["with errors"])})
    if not by_table.get("leads") and not res.leads and not res.auto_leads and not res.leads_updates and (res.companies or res.contacts or res.campaigns):
        warn("No leads uploaded — only reference data (companies/contacts/campaigns) will be saved; Lead Priority and AI features need leads.")
    SESSION["result"] = res
    return res


# ------------------------------------------------------------------------------------------ import
def affected_tables(res: BundleResult) -> list[str]:
    return [t for t in IMPORT_ORDER if t in closure(res.wiped)]


def wipe_preview(db: Database, res: BundleResult) -> pd.DataFrame:
    """What OVERWRITE will delete (rows per table, including cascaded children)."""
    rows = [{"table": t, "rows that will be deleted": db.count(t)} for t in affected_tables(res)]
    return pd.DataFrame(rows, columns=["table", "rows that will be deleted"])


def backup_tables(db: Database, tables: list[str], out_dir: Path | None = None) -> str | None:
    out_dir = out_dir or config.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"backup_before_overwrite_{datetime.now():%Y%m%d_%H%M%S}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            z.writestr(f"{t}.csv", db.query_df(f"SELECT * FROM {t}").to_csv(index=False))
    return str(path)


def _set_clause(values: dict) -> tuple[str, list]:
    cols = [(c, v) for c, v in values.items() if v not in (None, "")]
    return ", ".join(f"{c} = ?" for c, _ in cols), [v for _, v in cols]


def import_bundle(db: Database, res: BundleResult, today: date | None = None, backup_dir: Path | None = None) -> dict:
    """Write validated records in dependency order inside ONE transaction (all-or-nothing)."""
    today_s = (today or date.today()).isoformat()
    stats = {f"{t}": 0 for t in IMPORT_ORDER}
    stats.update({f"{t}_updated": 0 for t in IMPORT_ORDER})
    if not res.ok and not res.wiped:
        return stats
    if res.wiped:
        stats["backup"] = backup_tables(db, affected_tables(res), backup_dir)
    with db.connect() as conn:
        cur = conn.cursor()
        ph = "%s" if db.is_postgres else "?"

        def run(sql, params=()):
            cur.execute(sql.replace("?", ph), params)

        def insert(sql, params):
            sql = sql.replace("?", ph)
            if db.is_postgres:
                cur.execute(sql + " RETURNING id", params)
                return cur.fetchone()[0]
            cur.execute(sql, params)
            return cur.lastrowid

        def fetch(sql):
            cur.execute(sql)
            return cur.fetchall()

        for t in reversed(IMPORT_ORDER):                      # OVERWRITE: wipe chosen entity types (children cascade)
            if t in res.wiped:
                run(f"DELETE FROM {t}")
        company_id = {r[1]: r[0] for r in fetch("SELECT id, LOWER(name) FROM companies")}
        campaign_id = {r[1]: r[0] for r in fetch("SELECT id, LOWER(name) FROM campaigns")}
        contact_id, lead_id, opp_id, act_id = {}, {}, {}, {}
        for r in fetch("SELECT ct.id, LOWER(c.name), ct.email, ct.full_name FROM contacts ct JOIN companies c ON c.id = ct.company_id"):
            contact_id[(r[1], _canon((r[2] or "").lower(), (r[3] or "").lower()))] = r[0]
        for r in fetch("SELECT l.id, LOWER(c.name), ct.email, ct.full_name FROM leads l JOIN companies c ON c.id = l.company_id LEFT JOIN contacts ct ON ct.id = l.contact_id"):
            lead_id.setdefault((r[1], _canon((r[2] or "").lower(), (r[3] or "").lower())), r[0])
        for r in fetch("SELECT o.id, LOWER(c.name), ct.email, ct.full_name, LOWER(o.name) FROM opportunities o JOIN leads l ON l.id = o.lead_id "
                       "JOIN companies c ON c.id = l.company_id LEFT JOIN contacts ct ON ct.id = l.contact_id"):
            opp_id.setdefault((r[1], _canon((r[2] or "").lower(), (r[3] or "").lower()), r[4]), r[0])
        if res.activities_updates:
            for r in fetch("SELECT a.id, LOWER(c.name), ct.email, ct.full_name, a.type, a.activity_date, LOWER(COALESCE(a.outcome,'')) FROM activities a "
                           "JOIN leads l ON l.id = a.lead_id JOIN companies c ON c.id = l.company_id LEFT JOIN contacts ct ON ct.id = l.contact_id"):
                act_id.setdefault((r[1], _canon((r[2] or "").lower(), (r[3] or "").lower()), r[4], str(r[5])[:10], r[6]), r[0])

        # campaigns
        for r in res.campaigns:
            campaign_id[r["campaign_name"].lower()] = insert(
                "INSERT INTO campaigns (name, channel, start_date, end_date, budget_inr, impressions, clicks, conversions) VALUES (?,?,?,?,?,?,?,0)",
                (r["campaign_name"], r["channel"] or None, r["start_date"], r["end_date"], r["budget_inr"] or 0, int(r["impressions"] or 0), int(r["clicks"] or 0)))
            stats["campaigns"] += 1
        for r in res.campaigns_updates:
            sets, vals = _set_clause({"channel": r["channel"], "start_date": r["start_date"], "end_date": r["end_date"], "budget_inr": r["budget_inr"],
                                      "impressions": None if r["impressions"] is None else int(r["impressions"]),
                                      "clicks": None if r["clicks"] is None else int(r["clicks"])})
            if sets:
                run(f"UPDATE campaigns SET {sets} WHERE id = ?", (*vals, campaign_id[r["campaign_name"].lower()]))
            stats["campaigns_updated"] += 1
        # companies
        for r in res.companies:
            company_id[r["company_name"].lower()] = insert(
                "INSERT INTO companies (name, industry, city, state, size, website, created_at) VALUES (?,?,?,?,?,?,?)",
                (r["company_name"], r["industry"] or None, r["city"] or None, r["state"] or None, r["size"] or None, r["website"] or None, r["created_date"] or today_s))
            stats["companies"] += 1
        for r in res.companies_updates:
            sets, vals = _set_clause({k: r[k] for k in ("industry", "city", "state", "size", "website")})
            if sets:
                run(f"UPDATE companies SET {sets} WHERE id = ?", (*vals, company_id[r["company_name"].lower()]))
            stats["companies_updated"] += 1
        for lo, display in res.auto_companies.items():
            if lo not in company_id:
                company_id[lo] = insert("INSERT INTO companies (name, created_at) VALUES (?,?)", (display, today_s))
                stats["companies"] += 1
        # contacts
        for r in res.contacts + res.auto_contacts:
            contact_id[(r["_co"], r["_canon"])] = insert(
                "INSERT INTO contacts (company_id, full_name, job_title, email, phone, created_at) VALUES (?,?,?,?,?,?)",
                (company_id[r["_co"]], r["contact_name"], r.get("job_title") or None, r.get("email") or None, r.get("phone") or None, today_s))
            stats["contacts"] += 1
        for r in res.contacts_updates:
            sets, vals = _set_clause({"job_title": r["job_title"], "phone": r["phone"], "email": r["email"], "full_name": r["contact_name"]})
            if sets:
                run(f"UPDATE contacts SET {sets} WHERE id = ?", (*vals, contact_id[(r["_co"], r["_canon"])]))
            stats["contacts_updated"] += 1
        # leads
        for r in res.leads + res.auto_leads:
            cid = contact_id.get((r["_co"], r["_canon"])) if r["_canon"] else None
            camp = campaign_id.get(r.get("campaign_name", "").lower()) if r.get("campaign_name") else None
            lead_id[(r["_co"], r["_canon"])] = insert(
                "INSERT INTO leads (company_id, contact_id, campaign_id, source, status, owner, notes, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (company_id[r["_co"]], cid, camp, r.get("lead_source") or "Imported", r.get("lead_status") or "New", r.get("owner") or None,
                 r.get("notes") or None, r.get("created_date") or today_s))
            stats["leads"] += 1
        for r in res.leads_updates:
            camp = campaign_id.get(r["campaign_name"].lower()) if r["campaign_name"] else None
            sets, vals = _set_clause({"source": r["lead_source"], "status": r["lead_status"], "owner": r["owner"], "notes": r["notes"], "campaign_id": camp})
            if sets:
                run(f"UPDATE leads SET {sets} WHERE id = ?", (*vals, lead_id[(r["_co"], r["_canon"])]))
            stats["leads_updated"] += 1
        # opportunities
        for r in res.opportunities:
            lid = lead_id[(r["_co"], r["_canon"])]
            closed = r["closed_date"] or (r["expected_close_date"] if r["stage"] in ("Closed Won", "Closed Lost") else None)
            opp_id[(r["_co"], r["_canon"], r["deal_name"].lower())] = insert(
                "INSERT INTO opportunities (lead_id, name, stage, value_inr, expected_close_date, created_at, closed_at, win_probability) VALUES (?,?,?,?,?,?,?,?)",
                (lid, r["deal_name"], r["stage"], r["value_inr"] or 0.0, r["expected_close_date"], r["created_date"] or today_s, closed, r["win_probability"]))
            stats["opportunities"] += 1
        for r in res.opportunities_updates:
            closed = r["closed_date"] or (r["expected_close_date"] if r["stage"] in ("Closed Won", "Closed Lost") else None)
            sets, vals = _set_clause({"stage": r["stage"], "value_inr": r["value_inr"], "expected_close_date": r["expected_close_date"],
                                      "closed_at": closed, "win_probability": r["win_probability"]})
            if sets:
                run(f"UPDATE opportunities SET {sets} WHERE id = ?", (*vals, opp_id[(r["_co"], r["_canon"], r["deal_name"].lower())]))
            stats["opportunities_updated"] += 1
        # activities
        for r in res.activities:
            lid = lead_id[(r["_co"], r["_canon"])]
            oid = opp_id.get((r["_co"], r["_canon"], r["deal_name"].lower())) if r["deal_name"] else None
            run("INSERT INTO activities (lead_id, opportunity_id, type, activity_date, outcome, notes, follow_up_date) VALUES (?,?,?,?,?,?,?)",
                (lid, oid, r["activity_type"], r["activity_date"], r["outcome"] or None, r["notes"] or None, r["follow_up_date"]))
            stats["activities"] += 1
        for r in res.activities_updates:
            key = (r["_co"], r["_canon"], r["activity_type"], r["activity_date"], r["outcome"].lower())
            sets, vals = _set_clause({"notes": r["notes"], "follow_up_date": r["follow_up_date"]})
            if sets and key in act_id:
                run(f"UPDATE activities SET {sets} WHERE id = ?", (*vals, act_id[key]))
            stats["activities_updated"] += 1
    SESSION["result"] = None
    return stats


# ------------------------------------------------------------------------------------------ session reset
def reset_import_session(export_dir: Path | None = None) -> dict:
    """Clear server-side import state: cached validation result, duplicate/file registries and temporary report files.
    Database content is NOT touched."""
    export_dir = export_dir or config.EXPORT_DIR
    removed = 0
    if export_dir.exists():
        for f in export_dir.glob("import_report_*.csv"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    had = SESSION["result"] is not None or bool(SESSION["files"])
    SESSION["result"], SESSION["files"] = None, []
    return {"cleared_validation_state": had, "temp_files_removed": removed}


# ------------------------------------------------------------------------------------------ flat-file adapter
def flat_to_infos(df: pd.DataFrame, mapping: dict) -> list[FileInfo]:
    """Turn ONE flat sheet (company, contact, deal, last contact...) into companies/contacts/leads/deals/activities
    so it goes through the same validation, link-resolution and duplicate modes as multi-file uploads."""
    def col(key):
        c = mapping.get(key)
        return df[c].astype(str).str.strip() if c and c in df.columns else pd.Series([""] * len(df), index=df.index)
    if not mapping.get("company_name"):
        raise ImportError_("Required column not mapped: Company name")
    infos = []

    def add(table, frame, label):
        infos.append(FileInfo(f"flat file → {label}", table, frame, {c: c for c in frame.columns}, reason="built from the single flat sheet"))
    co = col("company_name")
    add("companies", pd.DataFrame({"company_name": co, "industry": col("industry"), "city": col("city")}), "companies")
    if mapping.get("contact_name") or mapping.get("email"):
        has = (col("contact_name") != "") | (col("email") != "")
        add("contacts", pd.DataFrame({"company_name": co, "contact_name": col("contact_name").where(col("contact_name") != "", col("email")),
                                      "job_title": col("job_title"), "email": col("email"), "phone": col("phone")})[has], "contacts")
    add("leads", pd.DataFrame({"company_name": co, "contact_name": col("contact_name"), "contact_email": col("email"), "lead_source": col("lead_source"),
                               "lead_status": col("lead_status"), "created_date": col("created_date")}), "leads")
    has_deal = (col("deal_stage") != "") | (col("deal_name") != "") | (col("deal_value") != "")
    if has_deal.any():
        add("opportunities", pd.DataFrame({"company_name": co, "contact_name": col("contact_name"), "contact_email": col("email"), "deal_name": col("deal_name"),
                                           "stage": col("deal_stage"), "value_inr": col("deal_value"), "expected_close_date": col("expected_close_date"),
                                           "created_date": col("created_date")})[has_deal], "deals")
    has_last = col("last_contact_date") != ""
    if has_last.any():
        add("activities", pd.DataFrame({"company_name": co, "contact_name": col("contact_name"), "contact_email": col("email"), "activity_type": "Note",
                                        "activity_date": col("last_contact_date"), "outcome": "Imported last contact"})[has_last], "activities")
    return infos


# ------------------------------------------------------------------------------------------ helpers
def report_csv(res: BundleResult, out_dir: Path | None = None) -> str | None:
    if not (res.errors or res.duplicates or res.warnings or res.notices):
        return None
    out_dir = out_dir or config.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"import_report_{datetime.now():%Y%m%d_%H%M%S}.csv"
    parts = []
    if res.errors:
        parts.append(res.errors_df().assign(type="error").rename(columns={"problem": "detail"})[["type", "file", "row", "field", "detail"]])
    if res.duplicates:
        parts.append(res.duplicates_df().assign(type="duplicate", field="").rename(columns={"reason": "detail"})[["type", "file", "row", "field", "detail"]])
    for kind, items in (("warning", res.warnings), ("notice", res.notices)):
        if items:
            parts.append(pd.DataFrame({"type": kind, "file": "", "row": "", "field": "", "detail": items}))
    pd.concat(parts, ignore_index=True).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


GUIDE_TEXT = """BRIGHTBRIDGE — IMPORT GUIDE
===========================
Upload as many of these files as you have, ALL AT ONCE (CSV or XLSX, max 5 MB each).
Files are recognised by their CONTENT (columns + values), not by their name, and headers are matched fuzzily
("Org Name", "Deal Size", "Stage Name", "Person Name", "Action" all work). Files are linked by company name
(or company_id when a companies file provides ids).

MINIMUM:   leads.csv  (company + contact name/email)
BEST:      leads.csv + opportunities.csv + activities.csv   (scores, pipeline, follow-ups, AI answers)
FULL:      + companies.csv + contacts.csv + campaigns.csv   (industry/city filters, campaign charts)

FILE              NEEDS (any header spelling)                     USED FOR
companies.csv     company                                         industry/city filters (optional)
contacts.csv      company, contact name                           contact details (optional)
campaigns.csv     campaign name                                   campaign charts
leads.csv         company                                         the lead list (main file)
opportunities.csv company, stage (+ value, win probability)       pipeline, revenue, deal value in score
activities.csv    company, type, date                             engagement, recency, follow-ups

ALLOWED VALUES (loose spelling is understood: "won", "negotiating", "phone call", "zoom" ...)
  stage          : Prospecting, Qualification, Proposal, Negotiation, Closed Won, Closed Lost
  activity type  : Email Sent, Email Opened, Email Replied, Call, Meeting, Demo, Note
  dates          : YYYY-MM-DD or DD/MM/YYYY      amounts: 320000, 3,20,000, ₹3.2L, 250k, $1,500

WHEN DATA ALREADY EXISTS — choose a mode
  SKIP       keep existing records, ignore matching rows (default)
  UPSERT     update existing records with your values (empty cells never erase data), add new ones
  OVERWRITE  delete the uploaded entity types first (backup ZIP saved), then insert your data

RULES
  * Missing companies/contacts/leads referenced by deals or activities are created automatically (you get a notice).
  * Rows with real problems (no company, unknown stage/type, bad date) are skipped and listed with file + row.
  * Nothing is saved until you confirm. "Reset validation" clears everything and lets you start again.
"""


def templates_zip(out_dir: Path | None = None) -> str:
    out_dir = out_dir or config.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    samples = {
        "companies": [{"company_name": "Example Textiles Pvt Ltd", "industry": "Manufacturing", "city": "Surat", "state": "Gujarat", "size": "51-200", "website": "https://example.com", "created_date": "2026-01-10"}],
        "contacts": [{"company_name": "Example Textiles Pvt Ltd", "contact_name": "Asha Rao", "job_title": "Operations Head", "email": "asha@example.com", "phone": "+91 90000 00000"}],
        "campaigns": [{"campaign_name": "Automation Webinar", "channel": "Webinar", "start_date": "2026-03-01", "end_date": "2026-03-31", "budget_inr": 50000, "impressions": 20000, "clicks": 600}],
        "leads": [{"company_name": "Example Textiles Pvt Ltd", "contact_name": "Asha Rao", "contact_email": "asha@example.com", "lead_source": "Webinar", "lead_status": "Qualified", "campaign_name": "Automation Webinar", "owner": "Sales Rep", "created_date": "2026-03-05", "notes": ""}],
        "opportunities": [{"company_name": "Example Textiles Pvt Ltd", "contact_email": "asha@example.com", "deal_name": "Workflow Automation", "stage": "Proposal", "value_inr": 320000, "win_probability": 60, "expected_close_date": "2026-11-15", "created_date": "2026-03-20", "closed_date": ""}],
        "activities": [{"company_name": "Example Textiles Pvt Ltd", "contact_email": "asha@example.com", "deal_name": "Workflow Automation", "activity_type": "Meeting", "activity_date": "2026-09-25", "outcome": "Demo completed", "notes": "", "follow_up_date": "2026-10-05"}],
    }
    path = out_dir / "brightbridge_import_templates.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for t, rows in samples.items():
            z.writestr(SPECS[t]["file"], pd.DataFrame(rows).to_csv(index=False))
        z.writestr("README_import_guide.txt", GUIDE_TEXT)
    return str(path)


def requirements_markdown() -> str:
    return ("**Which files do I need?**\n\n"
            "- **Minimum:** `leads.csv` (company + contact).\n"
            "- **Best results:** `leads.csv` + `opportunities.csv` + `activities.csv` — lead scores, pipeline, follow-ups and useful AI answers.\n"
            "- **Full:** also `companies.csv`, `contacts.csv`, `campaigns.csv` — industry/city filters and campaign charts.\n\n"
            "Files are recognised by their **content**, not their file name, and headers are matched flexibly. "
            "They are linked by **company name** (or company id). Missing companies/leads are created automatically. Upload everything together, in any order.")
