import pytest

from services import marketing_service as ms


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_template_mode_works_without_gemini_for_every_kind(demo_db):
    for kind in ms.KINDS:
        r = ms.generate_content(demo_db, kind, "Automating invoice follow-ups", use_ai=False)
        assert r["ok"] and r["ai"] is False and len(r["text"]) > 40 and "template" in r["message"].lower()
    assert demo_db.count("generated_content") == 4


def test_templates_contain_no_invented_claims(demo_db):
    text = ms.template_content("LinkedIn post", "invoice automation", "SME owners", "LinkedIn", "Bold", "Generate leads")
    assert "%" not in text and not any(ch.isdigit() for ch in text)
    assert "testimonial" not in text.lower()


def test_short_post_fits_in_280_chars():
    assert len(ms.template_content("Short social post", "x" * 250, "a", "X", "Bold", "Generate leads")) <= 280


def test_ai_mode_without_key_points_to_template(demo_db):
    r = ms.generate_content(demo_db, "LinkedIn post", "topic here")
    assert r["ok"] is False and "template" in r["message"].lower()


def test_share_links_prefill_and_encode():
    l = ms.share_links("Hello & welcome #1")
    assert l["linkedin"].startswith("https://www.linkedin.com/feed/?shareActive=true&text=Hello%20%26%20welcome")
    assert l["x"].startswith("https://x.com/intent/post?text=")
    assert ms.share_links("y" * 281)["x"] is None and ms.share_links("")["x"] is None


def test_status_workflow_draft_approved_posted(demo_db):
    r = ms.generate_content(demo_db, "LinkedIn post", "topic here", use_ai=False)
    cid = r["content_id"]
    assert demo_db.scalar("SELECT status FROM generated_content WHERE id=?", (cid,)) == "draft"
    assert ms.update_status(demo_db, cid, "approved") and ms.update_status(demo_db, cid, "posted")
    assert demo_db.scalar("SELECT status FROM generated_content WHERE id=?", (cid,)) == "posted"
    assert ms.update_status(demo_db, 99999, "posted") is False
    with pytest.raises(ValueError):
        ms.update_status(demo_db, cid, "hacked")


def test_nothing_is_ever_marked_posted_automatically(demo_db):
    ms.generate_content(demo_db, "LinkedIn post", "topic here", use_ai=False)
    ms.save_edited(demo_db, "LinkedIn post", "topic", "edited text")
    assert demo_db.scalar("SELECT COUNT(*) FROM generated_content WHERE status != 'draft'") == 0
