# Architecture

```
User → Gradio UI (ui/) → Services (services/) → Database (database/) 
                              ↓
                  Deterministic analytics (scoring, next action, KPIs)
                              ↓
                    AI agent (agent/) → Gemini API
```
Principle: **CRM data → deterministic calculations → evidence → Gemini reasoning/generation → human review → action.**

| Layer | Files | Role |
|---|---|---|
| UI | `ui/*.py`, `app.py` | One module per tab; `ui/context.py` holds active mode + DB |
| Services | `services/*.py` | CRM read-model, scoring, next action, analytics, email, marketing, import, export, health |
| Data | `database/db.py`, `schema.sql`, `seed.py` | SQLite default; `?` params translated for PostgreSQL |
| Agent | `agent/agent.py`, `tools.py`, `prompts.py`, `guardrails.py`, `gemini_client.py` | Intent → tools → evidence → (optional) Gemini phrasing |

**Agent flow:** classify intent → call CRM tools (recorded) → build ANSWER/SUPPORTING DATA/SUGGESTED ACTION from tool output → if Gemini is configured, rephrase using *only* that evidence (JSON) → on any Gemini error keep the deterministic answer. Free-form questions use Gemini function calling; answers produced without any tool call are discarded.

**Lead score (sum 100):** engagement 20, recency 20, deal value 15, stage 15, activity count 10, email engagement 8, meeting activity 7, follow-up status 5. Unqualified leads are capped at 39; closed opportunities are `CLOSED`.

**Persistence:** application files (code), temporary files (`data/exports`), persistent database (`DATABASE_URL`). Free Space disks are ephemeral.

**Portability:** no cloud-specific code — runs on Hugging Face, Render, Railway, VPS, AWS, Azure, GCP (Docker or `python app.py`).

**Future:** integration protocols in `services/integrations.py`; multi-agent: Orchestrator → Sales / Marketing / Analytics agents, later Follow-up / Campaign / Reporting agents (the tool layer is already agent-agnostic).
