import pandas as pd
import pytest

import config
from services import import_service as imp
from services.crm_service import get_lead_frame

GOOD = pd.DataFrame({
    "Company": ["Orbit Textiles", "Lotus Clinic", "Orbit Textiles", "Bad Mail Co", "Bad Value Co", "Bad Date Co", "", "Odd Stage Co"],
    "Contact Name": ["Asha Rao", "Dr Mehta", "Asha Rao", "X Y", "P Q", "R S", "Nobody", "T U"],
    "Email": ["asha@orbit.example.com", "mehta@lotus.example.com", "asha@orbit.example.com", "not-an-email", "p@q.example.com", "r@s.example.com", "n@n.example.com", "t@u.example.com"],
    "Deal Stage": ["Proposal", "Closed Won", "Proposal", "Proposal", "Proposal", "Proposal", "Proposal", "Haggling"],
    "Amount": ["₹3,20,000", "1.5L", "320000", "100", "abc", "100", "100", "100"],
    "Last Contact": ["2026-09-28", "15/09/2026", "2026-09-28", "2026-09-01", "2026-09-01", "31-31-2026", "2026-09-01", "2026-09-01"],
    "Industry": ["Retail", "Healthcare", "Retail", "IT", "IT", "IT", "IT", "IT"],
})


@pytest.fixture()
def csv_path(tmp_path):
    p = tmp_path / "leads.csv"
    GOOD.to_csv(p, index=False)
    return p


def test_column_detection_maps_synonyms():
    m = imp.detect_columns(list(GOOD.columns))
    assert m["company_name"] == "Company" and m["deal_value"] == "Amount" and m["last_contact_date"] == "Last Contact"
    assert m["phone"] is None and imp.missing_required(m) == []


def test_missing_required_column_blocks_validation(csv_path):
    df = imp.read_file(csv_path)
    m = imp.detect_columns(list(df.columns)); m["company_name"] = None
    with pytest.raises(imp.ImportError_):
        imp.validate(df, m)


def test_validation_reports_errors_and_duplicates(csv_path, empty_db):
    df = imp.read_file(csv_path)
    res = imp.validate(df, imp.detect_columns(list(df.columns)), empty_db)
    assert len(res.valid) == 2                       # Orbit Textiles (first only) + Lotus Clinic
    bad_fields = set(res.errors["field"])
    assert {"email", "deal_value", "last_contact_date", "company_name", "deal_stage"} <= bad_fields
    assert len(res.duplicates) == 1 and "within the uploaded file" in res.duplicates.iloc[0]["reason"]
    assert res.valid.set_index("company_name").loc["Orbit Textiles", "deal_value"] == 320000
    assert res.valid.set_index("company_name").loc["Lotus Clinic", "deal_value"] == 150000
    assert res.valid.set_index("company_name").loc["Lotus Clinic", "last_contact_date"] == "2026-09-15"


def test_import_writes_rows_and_fks_hold(csv_path, empty_db, today):
    df = imp.read_file(csv_path)
    res = imp.validate(df, imp.detect_columns(list(df.columns)), empty_db)
    stats = imp.import_rows(empty_db, res.valid, today)
    assert stats["leads_created"] == 2 and stats["opportunities_created"] == 2 and stats["activities_created"] == 2
    assert empty_db.foreign_key_violations() == []
    frame = get_lead_frame(empty_db, today)
    assert set(frame["company"]) == {"Orbit Textiles", "Lotus Clinic"} and frame["score"].between(0, 100).all()


def test_reimport_never_overwrites_existing(csv_path, empty_db, today):
    df = imp.read_file(csv_path)
    m = imp.detect_columns(list(df.columns))
    imp.import_rows(empty_db, imp.validate(df, m, empty_db).valid, today)
    before = empty_db.count("leads")
    res2 = imp.validate(df, m, empty_db)
    assert res2.valid.empty and len(res2.duplicates) >= 2
    assert any("Already exists" in r for r in res2.duplicates["reason"])
    imp.import_rows(empty_db, res2.valid, today)
    assert empty_db.count("leads") == before


def test_xlsx_import(tmp_path, empty_db, today):
    p = tmp_path / "leads.xlsx"
    GOOD.head(2).to_excel(p, index=False)
    df = imp.read_file(p)
    res = imp.validate(df, imp.detect_columns(list(df.columns)), empty_db)
    assert len(res.valid) == 2 and imp.import_rows(empty_db, res.valid, today)["leads_created"] == 2


def test_unsupported_type_rejected(tmp_path):
    p = tmp_path / "x.txt"; p.write_text("hello")
    with pytest.raises(imp.ImportError_, match="Unsupported"):
        imp.read_file(p)


def test_oversize_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    p = tmp_path / "x.csv"; p.write_text("a,b\n1,2\n")
    with pytest.raises(imp.ImportError_, match="larger"):
        imp.read_file(p)


def test_empty_and_garbage_files_rejected(tmp_path):
    e = tmp_path / "e.csv"; e.write_text("")
    with pytest.raises(imp.ImportError_):
        imp.read_file(e)
    g = tmp_path / "g.xlsx"; g.write_bytes(b"not really excel")
    with pytest.raises(imp.ImportError_):
        imp.read_file(g)
    h = tmp_path / "h.csv"; h.write_text("Company,Email\n")
    with pytest.raises(imp.ImportError_):
        imp.read_file(h)


def test_error_report_written(csv_path, empty_db, tmp_path):
    df = imp.read_file(csv_path)
    res = imp.validate(df, imp.detect_columns(list(df.columns)), empty_db)
    path = imp.error_report_csv(res, tmp_path)
    assert path and "problem" in open(path).read() or "detail" in open(path).read()


def test_numeric_parsing_edge_cases():
    assert imp._num("₹1,50,000") == 150000 and imp._num("2k") == 2000 and imp._num("") is None
    for bad in ("nan", "inf", "-5", "ten"):
        with pytest.raises(ValueError):
            imp._num(bad)
