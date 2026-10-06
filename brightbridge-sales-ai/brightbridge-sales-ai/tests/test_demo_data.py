import re

from scripts.generate_demo_data import generate, main


def test_counts_match_parameters():
    d = generate(30, 80, 40, 200, 5, seed=1)
    assert [len(d[k]) for k in ("companies", "leads", "opportunities", "activities", "campaigns")] == [30, 80, 40, 200, 5]


def test_deterministic_for_seed():
    assert generate(10, 20, 10, 40, 3, seed=5) == generate(10, 20, 10, 40, 3, seed=5)


def test_all_emails_are_synthetic():
    d = generate(20, 30, 10, 50, 3)
    assert all(re.search(r"@[\w.]+\.example\.com$", c["email"]) for c in d["contacts"])


def test_seeded_db_has_no_fk_violations(demo_db):
    assert demo_db.foreign_key_violations() == []
    assert demo_db.count("leads") == 200 and demo_db.count("activities") == 600


def test_indian_context(demo_db):
    states = {r["state"] for r in demo_db.query("SELECT DISTINCT state FROM companies")}
    assert "Maharashtra" in states or "Karnataka" in states
    assert demo_db.scalar("SELECT MIN(value_inr) FROM opportunities") > 0


def test_cli_writes_db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    assert main(["--companies", "10", "--leads", "20", "--opportunities", "10", "--activities", "30",
                 "--campaigns", "2", "--database-url", url, "--csv-dir", str(tmp_path / "csv")]) == 0
