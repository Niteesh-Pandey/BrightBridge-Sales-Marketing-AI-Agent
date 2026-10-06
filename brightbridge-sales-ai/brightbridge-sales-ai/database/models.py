"""Domain constants shared by services, import and demo generator."""
from __future__ import annotations

TABLES = ["companies", "contacts", "campaigns", "leads", "opportunities", "activities", "generated_content"]

OPEN_STAGES = ["Prospecting", "Qualification", "Proposal", "Negotiation"]
CLOSED_STAGES = ["Closed Won", "Closed Lost"]
STAGES = OPEN_STAGES + CLOSED_STAGES

LEAD_STATUSES = ["New", "Contacted", "Qualified", "Unqualified"]
LEAD_SOURCES = ["Website", "LinkedIn", "Referral", "Webinar", "Cold Outreach", "Trade Show", "Email Campaign"]
ACTIVITY_TYPES = ["Email Sent", "Email Opened", "Email Replied", "Call", "Meeting", "Demo", "Note"]
CONTENT_TYPES = ["email", "linkedin_post", "short_post", "campaign_ideas", "content_calendar"]

INDUSTRIES = [
    "Logistics", "Manufacturing", "Retail", "Healthcare", "Education",
    "Financial Services", "Real Estate", "Hospitality", "IT Services", "Agriculture",
]
