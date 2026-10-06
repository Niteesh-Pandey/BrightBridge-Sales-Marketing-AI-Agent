"""Safe CSV/XLSX import: upload -> preview -> detect -> map -> validate -> report -> confirm -> import.

Rules: never silently overwrite. Duplicates are skipped and reported; invalid rows are
reported and skipped; only rows that pass validation are written, inside one transaction-like batch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import config
from database.db import Database
from database.models import OPEN_STAGES, STAGES
from services.utils import parse_date

# canonical field -> (label, required, synonyms)
FIELDS = {
    "company_name": ("Company name", True, ["company", "company name", "organisation", "organization", "account", "business name", "firm"]),
    "industry": ("Industry", False, ["industry", "sector", "vertical"]),
    "city": ("City", False, ["city", "location", "town"]),
    "contact_name": ("Contact name", False, ["contact", "contact name", "name", "full name", "person", "lead name"]),
    "job_title": ("Job title", False, ["title", "job title", "designation", "role", "position"]),
    "email": ("Email", False, ["email", "e-mail", "email address", "mail"]),
    "phone": ("Phone", False, ["phone", "mobile", "phone number", "contact number", "telephone"]),
    "lead_source": ("Lead source", False, ["source", "lead source", "channel", "origin"]),
    "lead_status": ("Lead status", False, ["status", "lead status"]),
    "deal_name": ("Deal name", False, ["deal", "deal name", "opportunity", "opportunity name", "project"]),
    "deal_stage": ("Deal stage", False, ["stage", "deal stage", "pipeline stage", "opportunity stage"]),
    "deal_value": ("Deal value (INR)", False, ["value", "deal value", "amount", "deal amount", "revenue", "deal value inr", "value inr"]),
    "expected_close_date": ("Expected close date", False, ["expected close", "close date", "expected close date", "closing date"]),
    "last_contact_date": ("Last contact date", False, ["last contact", "last contacted", "last contact date", "last activity date", "last activity"]),
    "created_date": ("Created date", False, ["created", "created date", "date added", "created at"]),
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STAGE_ALIASES = {"won": "Closed Won", "closed won": "Closed Won", "lost": "Closed Lost", "closed lost": "Closed Lost",
                 "prospect": "Prospecting", "qualified": "Qualification", "proposal sent": "Proposal", "negotiating": "Negotiation"}
STATUS_ALIASES = {"new": "New", "contacted": "Contacted", "qualified": "Qualified", "unqualified": "Unqualified"}


class ImportError_(Exception):
    """User-facing import problem (bad file, too large, wrong type)."""


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    errors: pd.DataFrame            # row, field, problem
    duplicates: pd.DataFrame        # row, company, reason
    warnings: list = field(default_factory=list)
    total_rows: int = 0

    @property
    def ok(self) -> bool:
        return len(self.valid) > 0

    def summary(self) -> str:
        return (f"{self.total_rows} rows read: {len(self.valid)} valid, {self.errors['row'].nunique() if len(self.errors) else 0} with errors, "
                f"{len(self.duplicates)} duplicates (skipped).")


# ------------------------------------------------------------------ reading
def validate_upload(path: str | Path) -> Path:
    p = Path(path)
    if not p.exists():
        raise ImportError_("No file was uploaded.")
    if p.suffix.lower() not in config.ALLOWED_UPLOAD_EXTENSIONS:
        raise ImportError_("Unsupported file type. Please upload a .csv or .xlsx file.")
    if p.stat().st_size > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ImportError_(f"File is larger than the {config.MAX_UPLOAD_MB} MB limit.")
    if p.stat().st_size == 0:
        raise ImportError_("The file is empty.")
    return p


def read_file(path: str | Path) -> pd.DataFrame:
    p = validate_upload(path)
    try:
        if p.suffix.lower() == ".csv":
            df = None
            for enc in ("utf-8-sig", "latin-1"):
                try:
                    df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding=enc, nrows=config.MAX_IMPORT_ROWS + 1)
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                raise ImportError_("Could not decode the CSV file. Save it as UTF-8 and try again.")
        else:
            df = pd.read_excel(p, dtype=str, engine="openpyxl", nrows=config.MAX_IMPORT_ROWS + 1).fillna("")
    except ImportError_:
        raise
    except Exception as exc:
        raise ImportError_("The file could not be read. Make sure it is a valid CSV/XLSX with a header row.") from exc
    df.columns = [str(c).strip() for c in df.columns]
    if df.empty or len(df.columns) == 0:
        raise ImportError_("The file has no data rows.")
    if len(df) > config.MAX_IMPORT_ROWS:
        raise ImportError_(f"Too many rows (limit {config.MAX_IMPORT_ROWS}). Split the file and import in parts.")
    return df.astype(str)


def preview(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    return df.head(n)


# ------------------------------------------------------------------ mapping
def detect_columns(columns: list[str]) -> dict[str, str | None]:
    """Auto-map file columns to canonical fields using normalised synonym matching."""
    norm = {re.sub(r"[^a-z0-9]+", " ", c.lower()).strip(): c for c in columns}
    mapping, used = {}, set()
    for key, (_, _, synonyms) in FIELDS.items():
        hit = next((norm[s] for s in [key.replace("_", " ")] + synonyms if s in norm and norm[s] not in used), None)
        mapping[key] = hit
        if hit:
            used.add(hit)
    return mapping


def missing_required(mapping: dict) -> list[str]:
    return [FIELDS[k][0] for k, (_, req, _) in FIELDS.items() if req and not mapping.get(k)]


# ------------------------------------------------------------------ validation
def _num(v: str):
    s = re.sub(r"[₹,\s]|rs\.?|inr", "", str(v).lower())
    if s == "":
        return None
    mult = 1
    if s.endswith("l") or s.endswith("lakh"):
        mult, s = 100000, s.rstrip("lakh")
    elif s.endswith("k"):
        mult, s = 1000, s[:-1]
    try:
        n = float(s) * mult
    except ValueError:
        raise ValueError("not a number")
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError("not a number")
    if n < 0:
        raise ValueError("negative value")
    return n


def _date(v: str) -> str | None:
    s = str(v).strip()
    if s == "":
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("invalid date (use YYYY-MM-DD or DD/MM/YYYY)")


def validate(df: pd.DataFrame, mapping: dict, db: Database | None = None) -> ValidationResult:
    """Apply mapping, convert types, and split rows into valid / error / duplicate."""
    miss = missing_required(mapping)
    if miss:
        raise ImportError_("Required column not mapped: " + ", ".join(miss))
    bad_cols = [c for c in mapping.values() if c and c not in df.columns]
    if bad_cols:
        raise ImportError_("Mapped column not found in file: " + ", ".join(bad_cols))

    existing_pairs, existing_emails = set(), set()
    if db is not None:
        for r in db.query("SELECT c.name AS company, ct.full_name AS contact FROM leads l JOIN companies c ON c.id=l.company_id "
                          "LEFT JOIN contacts ct ON ct.id=l.contact_id"):
            existing_pairs.add(((r["company"] or "").lower(), (r["contact"] or "").lower()))
        existing_emails = {(r["email"] or "").lower() for r in db.query("SELECT email FROM contacts WHERE email IS NOT NULL")}

    valid_rows, errors, dups, seen_pairs, seen_emails = [], [], [], set(), set()
    for i, raw in df.iterrows():
        rown = int(i) + 2  # spreadsheet row number (header = row 1)
        rec, row_errors = {}, []
        for key in FIELDS:
            col = mapping.get(key)
            rec[key] = str(raw[col]).strip() if col else ""
        if not rec["company_name"]:
            row_errors.append(("company_name", "Company name is empty"))
        if rec["email"] and not EMAIL_RE.match(rec["email"]):
            row_errors.append(("email", f"Invalid email '{rec['email'][:40]}'"))
        try:
            rec["deal_value"] = _num(rec["deal_value"])
        except ValueError as e:
            row_errors.append(("deal_value", f"Invalid number '{raw[mapping['deal_value']][:30]}' ({e})")); rec["deal_value"] = None
        for dkey in ("expected_close_date", "last_contact_date", "created_date"):
            try:
                rec[dkey] = _date(rec[dkey])
            except ValueError as e:
                row_errors.append((dkey, f"'{raw[mapping[dkey]][:20]}': {e}")); rec[dkey] = None
        if rec["last_contact_date"] and rec["last_contact_date"] > date.today().isoformat():
            row_errors.append(("last_contact_date", "Date is in the future"))
        stage = rec["deal_stage"]
        if stage:
            canon = next((s for s in STAGES if s.lower() == stage.lower()), STAGE_ALIASES.get(stage.lower()))
            if not canon:
                row_errors.append(("deal_stage", f"Unknown stage '{stage[:30]}' (allowed: {', '.join(STAGES)})"))
            rec["deal_stage"] = canon or ""
        if rec["deal_value"] and not rec["deal_stage"] and not row_errors:
            rec["deal_stage"] = "Prospecting"
        if rec["lead_status"]:
            rec["lead_status"] = STATUS_ALIASES.get(rec["lead_status"].lower(), "")
        rec["lead_status"] = rec["lead_status"] or "New"

        if row_errors:
            errors.extend({"row": rown, "field": f, "problem": p} for f, p in row_errors)
            continue
        pair = (rec["company_name"].lower(), rec["contact_name"].lower())
        email = rec["email"].lower()
        if pair in existing_pairs or (email and email in existing_emails):
            dups.append({"row": rown, "company": rec["company_name"], "reason": "Already exists in CRM (skipped, not overwritten)"})
            continue
        if pair in seen_pairs or (email and email in seen_emails):
            dups.append({"row": rown, "company": rec["company_name"], "reason": "Duplicate within the uploaded file (skipped)"})
            continue
        seen_pairs.add(pair)
        if email:
            seen_emails.add(email)
        rec["_row"] = rown
        valid_rows.append(rec)
    warnings = []
    unmapped = [FIELDS[k][0] for k, c in mapping.items() if not c]
    if unmapped:
        warnings.append("Not mapped (left empty): " + ", ".join(unmapped))
    return ValidationResult(pd.DataFrame(valid_rows), pd.DataFrame(errors, columns=["row", "field", "problem"]),
                            pd.DataFrame(dups, columns=["row", "company", "reason"]), warnings, len(df))


# ------------------------------------------------------------------ import
def import_rows(db: Database, valid: pd.DataFrame, today: date | None = None) -> dict:
    """Write validated rows. Existing companies/contacts are reused, never modified."""
    today = (today or date.today()).isoformat()
    stats = {"companies_created": 0, "companies_reused": 0, "contacts_created": 0, "leads_created": 0,
             "opportunities_created": 0, "activities_created": 0}
    if valid is None or valid.empty:
        return stats
    with db.connect() as conn:
        cur = conn.cursor()
        ph = "%s" if db.is_postgres else "?"

        def insert(sql: str, params) -> int:
            sql = sql.replace("?", ph)
            if db.is_postgres:
                cur.execute(sql + " RETURNING id", params)
                return cur.fetchone()[0]
            cur.execute(sql, params)
            return cur.lastrowid

        def one(sql: str, params):
            cur.execute(sql.replace("?", ph), params)
            r = cur.fetchone()
            return r[0] if r else None

        for _, r in valid.iterrows():
            created = r.get("created_date") or today
            cid = one("SELECT id FROM companies WHERE LOWER(name) = LOWER(?)", (r["company_name"],))
            if cid:
                stats["companies_reused"] += 1
            else:
                cid = insert("INSERT INTO companies (name, industry, city, created_at) VALUES (?,?,?,?)",
                             (r["company_name"], r["industry"] or None, r["city"] or None, created))
                stats["companies_created"] += 1
            ctid = None
            if r["contact_name"] or r["email"]:
                if r["email"]:
                    ctid = one("SELECT id FROM contacts WHERE LOWER(email) = LOWER(?)", (r["email"],))
                if not ctid:
                    ctid = insert("INSERT INTO contacts (company_id, full_name, job_title, email, phone, created_at) VALUES (?,?,?,?,?,?)",
                                  (cid, r["contact_name"] or r["email"], r["job_title"] or None, r["email"] or None, r["phone"] or None, created))
                    stats["contacts_created"] += 1
            lid = insert("INSERT INTO leads (company_id, contact_id, source, status, owner, created_at) VALUES (?,?,?,?,?,?)",
                         (cid, ctid, r["lead_source"] or "Imported", r["lead_status"], None, created))
            stats["leads_created"] += 1
            oid = None
            if r["deal_stage"] or r["deal_name"] or r["deal_value"]:
                stage = r["deal_stage"] or "Prospecting"
                closed = r.get("expected_close_date") if stage not in OPEN_STAGES else None
                oid = insert("INSERT INTO opportunities (lead_id, name, stage, value_inr, expected_close_date, created_at, closed_at) VALUES (?,?,?,?,?,?,?)",
                             (lid, r["deal_name"] or f"Deal - {r['company_name']}", stage, float(r["deal_value"] or 0),
                              r.get("expected_close_date"), created, closed))
                stats["opportunities_created"] += 1
            if r.get("last_contact_date"):
                insert("INSERT INTO activities (lead_id, opportunity_id, type, activity_date, outcome, notes) VALUES (?,?,?,?,?,?)",
                       (lid, oid, "Note", r["last_contact_date"], "Imported last contact", "Created from imported 'last contact date'."))
                stats["activities_created"] += 1
    return stats


def error_report_csv(result: ValidationResult, out_dir: Path | None = None) -> str | None:
    """Write errors + duplicates to a CSV the user can download; returns path or None."""
    if result.errors.empty and result.duplicates.empty:
        return None
    out_dir = out_dir or config.EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"import_report_{datetime.now():%Y%m%d_%H%M%S}.csv"
    parts = []
    if not result.errors.empty:
        parts.append(result.errors.assign(type="error").rename(columns={"problem": "detail"}))
    if not result.duplicates.empty:
        parts.append(result.duplicates.assign(type="duplicate", field="").rename(columns={"reason": "detail"})[["row", "field", "detail", "type"]])
    pd.concat(parts, ignore_index=True).to_csv(path, index=False)
    return str(path)


def sample_template() -> pd.DataFrame:
    return pd.DataFrame([{k: "" for k in FIELDS}]).assign(company_name="Example Pvt Ltd", industry="Logistics", city="Pune",
        contact_name="Asha Rao", email="asha@example.com", deal_stage="Proposal", deal_value="250000", last_contact_date="2026-09-20")
