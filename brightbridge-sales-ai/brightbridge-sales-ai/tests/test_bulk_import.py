import zipfile
from pathlib import Path

import pandas as pd
import pytest

from services import bulk_import_service as bi
from services.crm_service import get_lead_detail, get_lead_frame

TODAY = __import__("datetime").date(2026, 10, 1)


def write(tmp_path, name, rows):
    p = Path(tmp_path) / name
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def run(paths, db, mode="SKIP"):
    infos = bi.analyse(paths)
    return infos, bi.validate_bundle(infos, db, TODAY, mode)


@pytest.fixture()
def bundle(tmp_path):
    """Realistic 6-file bundle with different header names and a few bad rows."""
    return [
        write(tmp_path, "companies.csv", [
            {"Company Name": "Orbit Textiles", "Sector": "Manufacturing", "City": "Surat"},
            {"Company Name": "Lotus Clinic", "Sector": "Healthcare", "City": "Pune"},
            {"Company Name": "orbit textiles", "Sector": "Manufacturing", "City": "Surat"}]),                       # duplicate in file
        write(tmp_path, "contacts.csv", [
            {"Company": "Orbit Textiles", "Name": "Asha Rao", "Designation": "COO", "Email": "asha@orbit.example.com", "Mobile": "+91 90000 11111"},
            {"Company": "Lotus Clinic", "Name": "Dr Mehta", "Designation": "Director", "Email": "mehta@lotus.example.com", "Mobile": ""},
            {"Company": "Lotus Clinic", "Name": "Bad Mail", "Designation": "", "Email": "not-an-email", "Mobile": ""}]),    # bad e-mail -> warning only
        write(tmp_path, "campaigns.csv", [
            {"Campaign": "Automation Webinar", "Channel": "Webinar", "Budget": "50,000", "Impressions": "20000", "Clicks": "600"}]),
        write(tmp_path, "leads.csv", [
            {"Company": "Orbit Textiles", "Contact Name": "Asha Rao", "Email": "asha@orbit.example.com", "Source": "Webinar", "Status": "Qualified", "Campaign": "Automation Webinar", "Created": "05/03/2026"},
            {"Company": "Lotus Clinic", "Contact Name": "Dr Mehta", "Email": "", "Source": "Referral", "Status": "New", "Campaign": "", "Created": "2026-04-01"},
            {"Company": "Brand New Co", "Contact Name": "New Person", "Email": "new@brand.example.com", "Source": "LinkedIn", "Status": "Contacted", "Campaign": "Missing Campaign", "Created": "2026-05-01"}]),
        write(tmp_path, "deals.csv", [
            {"Company": "Orbit Textiles", "Deal": "Workflow Automation", "Stage": "Proposal", "Amount": "₹3,20,000", "Close Date": "2026-11-15"},
            {"Company": "Lotus Clinic", "Deal": "Reporting", "Stage": "Won", "Amount": "1.5L", "Close Date": "2026-09-01"},
            {"Company": "Ghost Corp", "Deal": "Nothing", "Stage": "Proposal", "Amount": "100", "Close Date": ""},               # unknown company -> auto-created
            {"Company": "Orbit Textiles", "Deal": "Bad Stage", "Stage": "Haggling", "Amount": "100", "Close Date": ""},          # real error
            {"Company": "Orbit Textiles", "Deal": "Bad Amount", "Stage": "Proposal", "Amount": "abc", "Close Date": ""}]),       # garbage -> 0 + warning
        write(tmp_path, "interactions.csv", [
            {"Company": "Orbit Textiles", "Email": "asha@orbit.example.com", "Type": "Meeting", "Date": "2026-09-28", "Result": "Demo completed", "Next Step Date": "2026-10-05", "Deal": "Workflow Automation"},
            {"Company": "Orbit Textiles", "Email": "asha@orbit.example.com", "Type": "email", "Date": "2026-09-20", "Result": "", "Next Step Date": "", "Deal": ""},
            {"Company": "Lotus Clinic", "Email": "", "Type": "Call", "Date": "2026-09-10", "Result": "Connected", "Next Step Date": "", "Deal": ""},
            {"Company": "Orbit Textiles", "Email": "", "Type": "Carrier pigeon", "Date": "2026-09-10", "Result": "", "Next Step Date": "", "Deal": ""},
            {"Company": "Orbit Textiles", "Email": "", "Type": "Call", "Date": "2099-01-01", "Result": "", "Next Step Date": "", "Deal": ""},
            {"Company": "Orbit Textiles", "Email": "", "Type": "Call", "Date": "31-31-2026", "Result": "", "Next Step Date": "", "Deal": ""}]),
    ]


# ------------------------------------------------------------------------------------------- classification
def test_files_detected_by_content_even_with_unusual_names(bundle, empty_db):
    infos, _ = run(bundle, empty_db)
    assert {i.name: i.table for i in infos} == {"companies.csv": "companies", "contacts.csv": "contacts", "campaigns.csv": "campaigns",
                                                "leads.csv": "leads", "deals.csv": "opportunities", "interactions.csv": "activities"}
    assert all(not i.missing and not i.error for i in infos)


@pytest.mark.parametrize("filename,columns,expected", [
    ("companies.csv", {"Company": "A", "Stage": "Proposal", "Value": "100000"}, "opportunities"),          # content beats file name
    ("export.csv", {"Org Name": "A", "Deal Size": "100000", "Stage Name": "Proposal"}, "opportunities"),
    ("pipeline_q3.csv", {"Name of Company": "A", "Opportunity Value (INR)": "100000", "Pipeline Status": "Negotiation"}, "opportunities"),
    ("deals.csv", {"Compnay Name": "A", "Deal Valeu": "100000", "Stge": "Proposal"}, "opportunities"),    # typos
    ("log.csv", {"Account Name": "A", "Person Name": "X", "Action": "Call", "Activity Date": "2026-09-01"}, "activities"),
    ("data.csv", {"Company": "A", "Type": "Meeting", "Date": "2026-09-01"}, "activities"),
    ("whatever.csv", {"dealValue": "5", "companyName": "A", "dealStage": "won"}, "opportunities"),         # camelCase
    ("leads.csv", {"Company": "A", "Name": "B", "Status": "Contacted", "Source": "Web"}, "leads"),         # lead "Status" is not a deal stage
    ("sheet1.csv", {"Organization": "A", "Industry": "Retail", "City": "Pune"}, "companies"),
    ("sheet2.csv", {"Company": "A", "Full Name": "B", "Job Title": "CEO", "Phone": "1", "Email": "b@x.example.com"}, "contacts"),
    ("sheet3.csv", {"Campaign Name": "Spring", "Channel": "Email", "Spend": "1000", "Impressions": "10", "Clicks": "1"}, "campaigns"),
])
def test_content_based_classification(tmp_path, filename, columns, expected):
    info = bi.analyse([write(tmp_path, filename, [columns])])[0]
    assert info.table == expected and not info.error


def test_unrelated_file_is_reported_with_helpful_reason(tmp_path, bundle, empty_db):
    junk = write(tmp_path, "mystery.csv", [{"foo": 1, "bar": 2}])
    infos, res = run([junk, *bundle], empty_db)
    j = next(i for i in infos if i.name == "mystery.csv")
    assert j.table is None and "Columns found" in j.error
    assert any(e["file"] == "mystery.csv" for e in res.errors) and res.ok         # other files still import


def test_stage_value_columns_inferred_from_values_when_headers_are_cryptic(tmp_path):
    p = write(tmp_path, "d.csv", [{"Account": "A Co", "col_x": "Negotiating", "col_y": "5000"}, {"Account": "B Co", "col_x": "won", "col_y": "100"}])
    assert bi.analyse([p])[0].table == "opportunities"


# ------------------------------------------------------------------------------------------- validation & import
def test_validation_counts(bundle, empty_db):
    _, res = run(bundle, empty_db)
    s = res.summary_df().set_index("file")
    assert (s.loc["companies.csv", "new"], s.loc["companies.csv", "duplicates"]) == (2, 1)
    assert s.loc["contacts.csv", "new"] == 3 and s.loc["contacts.csv", "with errors"] == 0           # bad e-mail no longer blocks the row
    assert s.loc["deals.csv", "new"] == 4 and s.loc["deals.csv", "with errors"] == 1                 # only "Haggling" is a real error
    assert s.loc["interactions.csv", "new"] == 3 and s.loc["interactions.csv", "with errors"] == 3
    problems = " | ".join(e["problem"] for e in res.errors)
    assert "Unknown stage" in problems and "Unknown type" in problems and "in the future" in problems and "invalid date" in problems
    warns = " ".join(res.warnings)
    assert "invalid e-mail" in warns and "not a number" in warns and "Missing Campaign" in warns
    notices = " ".join(res.notices)
    assert "Ghost Corp" in notices and "Brand New Co" in notices


def test_unknown_company_for_deal_is_auto_created_not_an_error(empty_db, tmp_path):
    p = write(tmp_path, "deals.csv", [{"Company": "Brand New Co", "Deal": "X", "Stage": "Proposal", "Amount": "100"}])
    _, res = run([p], empty_db)
    assert res.errors == [] and len(res.opportunities) == 1 and len(res.auto_leads) == 1 and "Brand New Co" in res.auto_companies.values()
    assert any("placeholder" in n or "created" in n for n in res.notices)
    stats = bi.import_bundle(empty_db, res, TODAY)
    assert stats["companies"] == 1 and stats["leads"] == 1 and stats["opportunities"] == 1
    assert empty_db.foreign_key_violations() == []
    assert get_lead_frame(empty_db, TODAY).iloc[0]["deal_value"] == 100


def test_activity_for_unknown_contact_creates_contact_and_lead(empty_db, tmp_path):
    p = write(tmp_path, "activities.csv", [{"Company": "Zed Co", "Person Name": "Zoe Z", "Email": "zoe@zed.example.com", "Action": "Phone call", "Date": "2026-09-10"}])
    _, res = run([p], empty_db)
    assert res.errors == [] and len(res.auto_contacts) == 1 and len(res.auto_leads) == 1
    bi.import_bundle(empty_db, res, TODAY)
    d = get_lead_detail(empty_db, 1, TODAY)
    assert d["lead"]["contact"] == "Zoe Z" and d["activities"][0]["type"] == "Call"


def test_company_id_resolves_to_name_then_falls_back(empty_db, tmp_path):
    companies = write(tmp_path, "companies.csv", [{"company_id": "C-101", "Company Name": "Orbit Textiles"}])
    deals = write(tmp_path, "deals.csv", [{"company_id": "C-101", "Stage": "Proposal", "Value": "500"},
                                         {"company_id": "C-999", "Stage": "Proposal", "Value": "700"},      # unknown id -> placeholder
                                         {"company_id": "Plain Name Ltd", "Stage": "Won", "Value": "900"}])  # text in id column = name
    _, res = run([companies, deals], empty_db)
    assert res.errors == []
    bi.import_bundle(empty_db, res, TODAY)
    names = {r["name"] for r in empty_db.query("SELECT name FROM companies")}
    assert names == {"Orbit Textiles", "Company C-999", "Plain Name Ltd"}


def test_import_links_everything_and_scores_work(bundle, empty_db):
    _, res = run(bundle, empty_db)
    stats = bi.import_bundle(empty_db, res, TODAY)
    assert (stats["campaigns"], stats["companies"], stats["contacts"], stats["leads"], stats["opportunities"], stats["activities"]) == (1, 4, 4, 4, 4, 3)
    assert empty_db.foreign_key_violations() == []
    frame = get_lead_frame(empty_db, TODAY).set_index("company")
    orbit = frame.loc["Orbit Textiles"]
    assert orbit["deal_value"] == 320000 and orbit["activity_count"] == 2 and orbit["campaign"] == "Automation Webinar" and orbit["follow_up_state"] == "upcoming"
    assert frame.loc["Lotus Clinic", "priority"] == "CLOSED"
    d = get_lead_detail(empty_db, int(orbit["lead_id"]), TODAY)
    assert any(a["opportunity_id"] for a in d["activities"])


def test_leads_only_is_enough(tmp_path, empty_db):
    p = write(tmp_path, "leads.csv", [{"company_name": "Solo Co", "contact_name": "Raj K", "contact_email": "raj@solo.example.com"}])
    _, res = run([p], empty_db)
    stats = bi.import_bundle(empty_db, res, TODAY)
    assert stats["leads"] == 1 and stats["companies"] == 1 and stats["contacts"] == 1


def test_multiple_leads_without_contact_warns(tmp_path, empty_db):
    leads = write(tmp_path, "leads.csv", [{"company_name": "Twin Co", "contact_name": "A One"}, {"company_name": "Twin Co", "contact_name": "B Two"}])
    acts = write(tmp_path, "activities.csv", [{"company_name": "Twin Co", "activity_type": "Call", "activity_date": "2026-09-01"},
                                              {"company_name": "Twin Co", "contact_name": "B Two", "activity_type": "Call", "activity_date": "2026-09-02"}])
    _, res = run([leads, acts], empty_db)
    assert len(res.activities) == 2 and any("2 leads" in w for w in res.warnings)
    bi.import_bundle(empty_db, res, TODAY)
    rows = empty_db.query("SELECT ct.full_name AS n, COUNT(*) AS c FROM activities a JOIN leads l ON l.id=a.lead_id JOIN contacts ct ON ct.id=l.contact_id GROUP BY ct.full_name")
    assert {r["n"]: r["c"] for r in rows} == {"A One": 1, "B Two": 1}


def test_xlsx_mixed_with_csv(tmp_path, empty_db):
    leads = write(tmp_path, "leads.csv", [{"company_name": "Excel Co", "contact_name": "Eva"}])
    x = tmp_path / "activities.xlsx"
    pd.DataFrame([{"company_name": "Excel Co", "activity_type": "Meeting", "activity_date": "2026-09-15"}]).to_excel(x, index=False)
    _, res = run([leads, x], empty_db)
    assert len(res.leads) == 1 and len(res.activities) == 1


def test_win_probability_formats(tmp_path, empty_db):
    pct = write(tmp_path, "deals.csv", [{"Company": "P Co", "Stage": "Proposal", "Value": "1000", "Probability": "60%"}])
    _, res = run([pct], empty_db)
    assert res.opportunities[0]["win_probability"] == 60
    frac = write(tmp_path, "deals2.csv", [{"Company": "Q Co", "Stage": "Proposal", "Value": "1000", "Win Probability": "0.4"},
                                         {"Company": "R Co", "Stage": "Proposal", "Value": "1000", "Win Probability": "0.75"}])
    _, res = run([frac], empty_db)
    assert [o["win_probability"] for o in res.opportunities] == [40, 75]


def test_downloadable_templates_import_cleanly(tmp_path, empty_db):
    z = bi.templates_zip(tmp_path)
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        zf.extractall(tmp_path / "t")
    assert names == {"companies.csv", "contacts.csv", "campaigns.csv", "leads.csv", "opportunities.csv", "activities.csv", "README_import_guide.txt"}
    _, res = run([tmp_path / "t" / n for n in names if n.endswith(".csv")], empty_db)
    assert res.errors == [] and res.duplicates == [] and res.warnings == [] and res.notices == []
    assert all(v == 1 for k, v in bi.import_bundle(empty_db, res, TODAY).items() if k in bi.IMPORT_ORDER)


def test_problem_files_rejected(tmp_path):
    with pytest.raises(bi.ImportError_):
        bi.analyse([])
    bad = tmp_path / "x.txt"; bad.write_text("hi")
    assert "Unsupported" in bi.analyse([bad])[0].error


def test_report_and_checklist(bundle, empty_db, tmp_path):
    infos, res = run(bundle, empty_db)
    text = open(bi.report_csv(res, tmp_path), encoding="utf-8-sig").read()
    assert all(w in text for w in ("error", "duplicate", "warning", "notice"))
    md = bi.checklist_markdown(infos)
    assert md.count("✅") == 6


# ------------------------------------------------------------------------------------------- duplicate modes
@pytest.fixture()
def loaded(bundle, empty_db):
    _, res = run(bundle, empty_db)
    bi.import_bundle(empty_db, res, TODAY)
    return empty_db


def counts(db):
    return {t: db.count(t) for t in bi.IMPORT_ORDER}


def test_skip_mode_reupload_changes_nothing_and_explains(bundle, loaded):
    before = counts(loaded)
    _, res = run(bundle, loaded, "SKIP")
    assert res.total_valid == 0 and res.total_updates == 0 and not res.ok
    assert any("choose UPSERT" in d["reason"] for d in res.duplicates)
    assert bi.import_bundle(loaded, res, TODAY)["leads"] == 0 and counts(loaded) == before


def test_upsert_updates_existing_records_and_never_blanks_with_empty_cells(loaded, tmp_path):
    before = counts(loaded)
    deals = write(tmp_path, "deals_new.csv", [
        {"Company": "Orbit Textiles", "Deal": "Workflow Automation", "Stage": "Negotiation", "Amount": "450000", "Close Date": ""},   # update (empty close date must stay)
        {"Company": "Orbit Textiles", "Deal": "Brand New Deal", "Stage": "Proposal", "Amount": "10000", "Close Date": "2026-12-01"}])   # insert
    companies = write(tmp_path, "companies_new.csv", [{"Company Name": "Orbit Textiles", "Sector": "", "City": "Ahmedabad"}])
    _, res = run([deals, companies], loaded, "UPSERT")
    assert res.total_updates == 2 and res.total_valid == 1 and res.ok
    stats = bi.import_bundle(loaded, res, TODAY)
    assert stats["opportunities_updated"] == 1 and stats["opportunities"] == 1 and stats["companies_updated"] == 1
    opp = loaded.query("SELECT stage, value_inr, expected_close_date FROM opportunities WHERE name='Workflow Automation'")[0]
    assert opp["stage"] == "Negotiation" and opp["value_inr"] == 450000 and opp["expected_close_date"] == "2026-11-15"   # kept
    co = loaded.query("SELECT industry, city FROM companies WHERE name='Orbit Textiles'")[0]
    assert co["city"] == "Ahmedabad" and co["industry"] == "Manufacturing"                                              # blank sector did not erase
    assert loaded.count("opportunities") == before["opportunities"] + 1 and loaded.foreign_key_violations() == []


def test_upsert_merges_duplicate_rows_inside_the_upload(empty_db, tmp_path):
    p = write(tmp_path, "leads.csv", [{"Company": "Dup Co", "Name": "A B", "Source": "", "Owner": "Rep One"},
                                      {"Company": "Dup Co", "Name": "A B", "Source": "Webinar", "Owner": ""}])
    _, res = run([p], empty_db, "UPSERT")
    assert len(res.leads) == 1 and res.leads[0]["lead_source"] == "Webinar" and res.leads[0]["owner"] == "Rep One"
    assert "merged" in res.duplicates[0]["reason"]


def test_upsert_activities_update_notes(loaded, tmp_path):
    acts = write(tmp_path, "acts.csv", [{"Company": "Orbit Textiles", "Email": "asha@orbit.example.com", "Type": "Meeting", "Date": "2026-09-28",
                                        "Result": "Demo completed", "Notes": "Updated note", "Next Step Date": "2026-10-09"}])
    _, res = run([acts], loaded, "UPSERT")
    assert res.total_updates == 1
    bi.import_bundle(loaded, res, TODAY)
    row = loaded.query("SELECT notes, follow_up_date FROM activities WHERE type='Meeting'")[0]
    assert row["notes"] == "Updated note" and row["follow_up_date"] == "2026-10-09"


def test_overwrite_replaces_only_chosen_entities_and_saves_backup(loaded, tmp_path):
    n_leads_before = loaded.count("leads")
    leads = write(tmp_path, "leads_fresh.csv", [{"Company": "Fresh Co", "Name": "Fay F", "Email": "fay@fresh.example.com", "Source": "Web"}])
    _, res = run([leads], loaded, "OVERWRITE")
    assert res.wiped == {"leads"} and res.duplicates == [] and res.total_valid == 1
    preview = bi.wipe_preview(loaded, res).set_index("table")["rows that will be deleted"]
    assert preview["leads"] == n_leads_before and preview["opportunities"] > 0 and preview["activities"] > 0     # children cascade
    stats = bi.import_bundle(loaded, res, TODAY, backup_dir=tmp_path)
    with zipfile.ZipFile(stats["backup"]) as z:
        assert {"leads.csv", "opportunities.csv", "activities.csv"} <= set(z.namelist()) and len(z.read("leads.csv")) > 50
    assert loaded.count("leads") == 1 and loaded.count("opportunities") == 0 and loaded.count("activities") == 0
    assert loaded.count("companies") >= 5 and loaded.foreign_key_violations() == []                                # companies untouched


def test_overwrite_deals_only_keeps_leads(loaded, tmp_path):
    deals = write(tmp_path, "deals_only.csv", [{"Company": "Orbit Textiles", "Deal": "Replacement", "Stage": "Proposal", "Amount": "1"}])
    n_leads = loaded.count("leads")
    _, res = run([deals], loaded, "OVERWRITE")
    assert res.wiped == {"opportunities"}
    bi.import_bundle(loaded, res, TODAY, backup_dir=tmp_path)
    assert loaded.count("opportunities") == 1 and loaded.count("leads") == n_leads and loaded.count("activities") == 3


def test_overwrite_companies_everything_rebuilt_from_upload(loaded, bundle, tmp_path):
    _, res = run(bundle, loaded, "OVERWRITE")
    assert res.duplicates and all("inside the uploaded files" in d["reason"] for d in res.duplicates)       # DB rows are not "existing" any more
    assert bi.import_bundle(loaded, res, TODAY, backup_dir=tmp_path)["leads"] == 4 and loaded.count("leads") == 4
    assert loaded.foreign_key_violations() == []


def test_invalid_mode_rejected(bundle, empty_db):
    with pytest.raises(bi.ImportError_):
        bi.validate_bundle(bi.analyse(bundle), empty_db, TODAY, "DESTROY")


# ------------------------------------------------------------------------------------------- reset + deadlock
def test_deadlock_scenario_and_reset(bundle, loaded, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "exp")
    _, res = run(bundle, loaded)                                  # everything is already there -> "0 valid" in SKIP
    assert not res.ok and "0 new" in res.headline()
    report = bi.report_csv(res)
    assert report and Path(report).exists() and bi.SESSION["result"] is res and bi.SESSION["files"]
    out = bi.reset_import_session()                                # backend reset API
    assert out == {"cleared_validation_state": True, "temp_files_removed": 1}
    assert bi.SESSION["result"] is None and bi.SESSION["files"] == [] and not Path(report).exists()
    _, res2 = run(bundle, loaded, "UPSERT")                        # the way out of the deadlock
    assert res2.ok and res2.total_updates > 0
    assert counts(loaded)["leads"] == 4                            # reset never touched the database


# ------------------------------------------------------------------------------------------- flat file adapter
def test_flat_file_goes_through_same_engine_and_modes(empty_db, tmp_path):
    from services import import_service as imp
    flat = pd.DataFrame([
        {"Company": "Flat Co", "Industry": "Retail", "City": "Pune", "Contact": "Fay F", "Email": "fay@flat.example.com", "Stage": "Proposal", "Amount": "₹2,50,000", "Last Contact": "2026-09-20"},
        {"Company": "Flat Co", "Industry": "Retail", "City": "Pune", "Contact": "Fay F", "Email": "fay@flat.example.com", "Stage": "Proposal", "Amount": "300000", "Last Contact": "2026-09-22"},
        {"Company": "", "Industry": "x", "City": "y", "Contact": "No Company", "Email": "", "Stage": "", "Amount": "", "Last Contact": ""}])
    mapping = imp.detect_columns(list(flat.columns))
    infos = bi.flat_to_infos(flat.astype(str), mapping)
    res = bi.validate_bundle(infos, empty_db, TODAY, "SKIP")
    assert any(e["field"] == "company_name" for e in res.errors)               # blank-company row reported with ORIGINAL row number
    assert {e["row"] for e in res.errors if e["field"] == "company_name"} == {4}
    stats = bi.import_bundle(empty_db, res, TODAY)
    assert stats["leads"] == 1 and stats["opportunities"] == 1 and stats["activities"] == 2
    res2 = bi.validate_bundle(bi.flat_to_infos(flat.astype(str), mapping), empty_db, TODAY, "UPSERT")
    bi.import_bundle(empty_db, res2, TODAY)
    assert empty_db.scalar("SELECT value_inr FROM opportunities") == 300000 and empty_db.count("opportunities") == 1
