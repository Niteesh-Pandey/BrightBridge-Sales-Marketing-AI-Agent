import pandas as pd
import pytest

from services.export_service import export_frame
from services.health_service import check_health
from ui.context import AppContext


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")


def test_demo_context_autogenerates_data():
    ctx = AppContext("demo")
    assert ctx.error is None and ctx.db.count("leads") == 750
    assert ctx.banner == "DEMO MODE — Synthetic Business Data"


def test_real_context_starts_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'real.db').as_posix()}")
    ctx = AppContext("real")
    assert ctx.db.is_empty() and "REAL" in ctx.banner
    assert ctx.frame().empty


def test_bad_database_url_does_not_crash(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://nope")
    ctx = AppContext("demo")
    assert ctx.db is None and "unavailable" in ctx.error and "mysql" not in ctx.error


def test_health_never_reveals_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SUPER-SECRET-KEY")
    report = check_health(AppContext("demo").db, "demo")
    assert "SUPER-SECRET-KEY" not in report.to_csv()
    # a key that has never been verified must NOT be reported as connected
    assert "not verified" in report.set_index("Component").loc["Gemini API", "Status"]


def test_health_without_key_and_without_db():
    report = check_health(AppContext("demo").db, "demo").set_index("Component")
    assert "Not configured" in report.loc["Gemini API", "Status"] and report.loc["Database", "Status"].startswith("✓")
    assert check_health(None, "demo").set_index("Component").loc["Database", "Status"].startswith("✗")


def test_app_builds_without_gemini_key():
    import app
    assert app.build_app(AppContext("demo")) is not None


def test_app_builds_when_database_broken(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://nope")
    import app
    assert app.build_app(AppContext("demo")) is not None


def test_export_csv_xlsx_and_formula_protection(tmp_path):
    df = pd.DataFrame({"name": ["=HYPERLINK(\"http://x\")", "Safe"], "v": [1, 2]})
    csv = export_frame(df, "t", "csv", tmp_path)
    xlsx = export_frame(df, "t", "xlsx", tmp_path)
    assert "'=HYPERLINK" in open(csv, encoding="utf-8-sig").read()
    assert pd.read_excel(xlsx).iloc[0]["name"].startswith("'=")
    assert export_frame(pd.DataFrame(), "t", "csv", tmp_path) is None
    with pytest.raises(ValueError):
        export_frame(df, "t", "pdf", tmp_path)


def test_regenerate_demo_only_in_demo_mode(monkeypatch, tmp_path):
    ctx = AppContext("demo")
    assert "regenerated" in ctx.regenerate_demo()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    assert "only" in AppContext("real").regenerate_demo()


def test_header_pill_is_honest_about_gemini(monkeypatch):
    from agent import gemini_client as g
    from ui.common import gemini_note, gemini_pill
    g.reset_state()
    assert "not configured" in gemini_pill()
    monkeypatch.setenv("GEMINI_API_KEY", "some-key")
    assert "not verified" in gemini_pill() and "connected" not in gemini_pill().lower().replace("not verified", "")
    g._set_status("ok", "Connected", "gemini-x")
    assert "connected ✓" in gemini_pill() and gemini_note() == ""
    g._set_status("error", "Gemini rejected the API key")
    assert "problem" in gemini_pill() and "rejected" in gemini_note()
    monkeypatch.setenv("GEMINI_API_KEY", "a-different-key")      # key changed -> must be re-verified
    assert "not verified" in gemini_pill()
    g.reset_state()


def test_empty_real_workspace_shows_guidance(monkeypatch, tmp_path):
    import app
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'e.db').as_posix()}")
    html = app.empty_state_html(AppContext("real"))
    assert "Import Data" in html and "No business data yet" in html
    assert app.empty_state_html(AppContext("demo")) == ""
