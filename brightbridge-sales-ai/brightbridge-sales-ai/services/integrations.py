"""Interfaces for FUTURE integrations (not implemented in the MVP).

Each integration would implement one of these small protocols and be registered in the
service layer. Nothing here talks to an external system.
"""
from __future__ import annotations

from typing import Protocol


class EmailProvider(Protocol):          # Gmail, Outlook
    def create_draft(self, to: str, subject: str, body: str) -> str: ...


class CalendarProvider(Protocol):       # Google Calendar, Microsoft Calendar
    def create_event(self, title: str, start_iso: str, end_iso: str, attendees: list[str]) -> str: ...


class CRMConnector(Protocol):           # HubSpot, Salesforce, Zoho CRM
    def pull_leads(self, since_iso: str | None = None) -> list[dict]: ...
    def push_note(self, external_id: str, note: str) -> None: ...


class MessagingProvider(Protocol):      # WhatsApp Business, LinkedIn
    def create_message_draft(self, recipient: str, text: str) -> str: ...


class MarketingPlatform(Protocol):      # ad / email marketing platforms
    def campaign_metrics(self, campaign_id: str) -> dict: ...


# Future multi-agent layout (see docs/architecture.md):
#   Orchestrator -> Sales agent | Marketing agent | Analytics agent
#   later: Follow-up agent, Campaign agent, Reporting agent
