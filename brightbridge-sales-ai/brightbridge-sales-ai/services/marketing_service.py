"""Marketing content generation (drafts only; nothing is ever published automatically)."""
from __future__ import annotations

import re
import urllib.parse

import config
from agent import gemini_client
from agent.guardrails import safe_error_message, sanitize_text
from agent.prompts import SOCIAL_PROMPT, SYSTEM_PROMPT
from database.db import Database
from services.analytics_service import campaign_performance
from services.email_service import save_draft

KINDS = {"LinkedIn post": ("linkedin_post", "one LinkedIn post (120-200 words, strong first line, short paragraphs, 3 relevant hashtags)"),
         "Short social post": ("short_post", "one short social post under 280 characters"),
         "Campaign ideas": ("campaign_ideas", "five distinct B2B campaign ideas, each with a name, one-line concept, channel and a measurable goal"),
         "Content calendar": ("content_calendar", "a 4-week content calendar (2 posts per week) as a simple list: week, day, format, topic idea")}
PLATFORMS = ["LinkedIn", "Twitter/X", "Instagram", "Facebook", "Email newsletter"]
TONES = ["Professional", "Friendly", "Thought-leader", "Conversational", "Bold"]
OBJECTIVES = ["Generate leads", "Build awareness", "Promote a webinar", "Share expertise", "Drive demo bookings"]


STATUSES = ["draft", "approved", "posted"]
_OPENERS = {"Professional": "Repetitive work quietly drains growing teams.", "Friendly": "Quick question: how much of your week goes to repeat tasks?",
            "Thought-leader": "Most businesses do not have a people problem — they have a workflow problem.",
            "Conversational": "Let's be honest: nobody enjoys copy-pasting between spreadsheets.", "Bold": "Stop doing by hand what a workflow can do for you."}
_CTAS = {"Generate leads": "Want a quick look at where automation could help your team? Send us a message.",
         "Build awareness": "Follow BrightBridge Solutions for practical ideas on smarter workflows.",
         "Promote a webinar": "Join our upcoming session — comment 'IN' and we will send you the details.",
         "Share expertise": "What is the one task you would automate first? Tell us in the comments.",
         "Drive demo bookings": "Book a short discovery call and we will map one workflow with you."}


def template_content(kind: str, topic: str, audience: str, platform: str, tone: str, objective: str) -> str:
    """Plain, deterministic draft (no AI). Uses only the inputs; no statistics, clients or testimonials."""
    topic, audience = sanitize_text(topic, 300), sanitize_text(audience, 200) or "business owners"
    opener, cta = _OPENERS.get(tone, _OPENERS["Professional"]), _CTAS.get(objective, _CTAS["Generate leads"])
    words = [w for w in re.findall(r"[A-Za-z]{4,}", topic)][:3]
    tags = " ".join(["#" + w.capitalize() for w in words] + ["#BusinessAutomation", "#Workflows"])[:120]
    if kind == "LinkedIn post":
        return (f"{opener}\n\nTopic: {topic}.\n\nFor {audience}, the first step is simple: list the tasks your team repeats every week, "
                f"pick the one that wastes the most time, and design a smarter workflow for just that one.\n\n"
                f"Start small, measure the time saved, then expand.\n\n{cta}\n\n{tags}")
    if kind == "Short social post":
        short = f"{opener} Today: {topic}. {cta}"
        return (short if len(short) <= 270 else short[:267] + "...")
    if kind == "Campaign ideas":
        return (f"Campaign ideas for: {topic} (audience: {audience})\n\n"
                f"1. Workflow audit offer — Channel: LinkedIn + email — Measure: audit requests\n"
                f"2. Live webinar: {topic} — Channel: Webinar — Measure: registrations and attendance\n"
                f"3. Simple checklist download — Channel: Content / website — Measure: downloads and follow-up replies\n"
                f"4. 5-part email mini-series — Channel: Email — Measure: opens, replies, meetings booked\n"
                f"5. Weekly tip posts — Channel: {platform} — Measure: profile visits and inbound messages")
    formats = ["Educational post", "Poll", "Checklist / carousel", "Short tip", "Question to the audience", "Behind-the-scenes", "Process explainer", "Call-to-action post"]
    lines = [f"4-week content calendar for: {topic} (audience: {audience}, platform: {platform})", ""]
    for week in range(4):
        for j, day in enumerate(("Tuesday", "Thursday")):
            lines.append(f"Week {week + 1} · {day} · {formats[(week * 2 + j) % len(formats)]} — idea: {topic} ({['problem', 'first step', 'common mistake', 'next step'][week]})")
    return "\n".join(lines)


def share_links(text: str) -> dict:
    """Pre-filled composer links. The user reviews the text and presses Post themselves; nothing is posted by this app."""
    text = (text or "").strip()
    out = {"linkedin": "https://www.linkedin.com/feed/?shareActive=true&text=" + urllib.parse.quote(text), "x": None}
    if text and len(text) <= 280:
        out["x"] = "https://x.com/intent/post?text=" + urllib.parse.quote(text)
    return out


def update_status(db: Database, content_id: int, status: str) -> bool:
    if status not in STATUSES:
        raise ValueError("Invalid status")
    if not db.scalar("SELECT id FROM generated_content WHERE id = ?", (int(content_id),)):
        return False
    db.execute("UPDATE generated_content SET status = ? WHERE id = ?", (status, int(content_id)))
    return True


def generate_content(db: Database, kind: str, topic: str, audience: str = "Small and mid-sized business owners",
                     platform: str = "LinkedIn", tone: str = "Professional", objective: str = "Generate leads", use_ai: bool = True) -> dict:
    if kind not in KINDS:
        return {"ok": False, "message": "Please choose a valid content type."}
    topic = sanitize_text(topic, 300)
    if len(topic) < 3:
        return {"ok": False, "message": "Please enter a topic (at least 3 characters)."}
    if not use_ai:
        text = template_content(kind, topic, audience, platform, tone, objective)
        cid = save_draft(db, KINDS[kind][0], f"{kind} (template): {topic}"[:200], text)
        return {"ok": True, "ai": False, "text": text, "content_id": cid,
                "message": "Template draft ready (no AI used). Edit it before posting; nothing has been published."}
    if not gemini_client.is_configured():
        return {"ok": False, "message": config.GEMINI_MISSING_MSG + " Use “Create from template (no AI)” instead."}
    content_type, description = KINDS[kind]
    prompt = SOCIAL_PROMPT.format(kind=description, topic=topic, audience=sanitize_text(audience, 200) or "B2B decision makers",
                                  platform=sanitize_text(platform, 40), tone=sanitize_text(tone, 40), objective=sanitize_text(objective, 80))
    if content_type in ("campaign_ideas", "content_calendar"):
        perf = campaign_performance(db)
        if not perf.empty:
            top = perf.groupby("channel")["leads"].sum().sort_values(ascending=False).head(3)
            prompt += "\n\nReal CRM fact (channels ranked by leads generated): " + ", ".join(f"{c}: {n}" for c, n in top.items())
    try:
        text = gemini_client.generate_text(prompt, SYSTEM_PROMPT, temperature=0.8)
    except gemini_client.GeminiError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "message": safe_error_message(exc, "content generation")}
    if not text:
        return {"ok": False, "message": "Gemini returned an empty response. Please try again."}
    cid = save_draft(db, content_type, f"{kind}: {topic}"[:200], text)
    return {"ok": True, "ai": True, "text": text, "content_id": cid, "message": "Draft ready. Edit it before posting; nothing has been published."}


def save_edited(db: Database, kind: str, topic: str, text: str) -> int:
    content_type = KINDS.get(kind, ("note", ""))[0]
    return save_draft(db, content_type, f"{kind} (edited): {sanitize_text(topic, 150)}", text)


def list_generated(db: Database):
    return db.query_df("SELECT id, content_type, title, status, created_at, body FROM generated_content ORDER BY id DESC")
