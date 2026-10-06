---
title: BrightBridge Sales Intelligence
emoji: 🌉
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 6.29.0
python_version: "3.11"
app_file: app.py
pinned: false
license: mit
---

# BrightBridge Sales Intelligence

**An evidence-first Sales & Marketing AI agent** for a fictional B2B consulting company, *BrightBridge Solutions*.
Built by **Niteesh Pandey**. Version **v0.1.0**.

> **DEMO MODE — Synthetic Business Data.** All companies, people, e-mails, phone numbers, deals and campaigns in demo mode are fictional.

## What it does
Turns CRM data into clear sales priorities, evidence-backed answers and editable drafts.

**Business problem:** sales teams lose deals to missed follow-ups, unclear priorities and generic chatbots that guess. This app computes the facts first (scores, pipeline, follow-ups) and only then lets Gemini explain or draft — always for human review.

```
CRM Data → Deterministic Analytics → Evidence → Gemini reasoning/generation → Human review → Action
```

## Features
- **Dashboard:** 6 KPIs + 6 charts (pipeline by stage, leads by source, score distribution, industry, monthly pipeline, campaigns)
- **CRM:** search; industry/stage/source/priority/value/date filters; lead details, activity history, opportunities; export
- **Lead Priority:** deterministic 0–100 score → CALL NOW / FOLLOW UP SOON / NURTURE / LOW PRIORITY, with next action + WHY + SOURCE DATA
- **Pipeline & Follow-ups:** funnel, overdue / due-today list, exports
- **AI Assistant:** tools-based agent; every answer shows ANSWER · SUPPORTING DATA · SUGGESTED ACTION; replies *"Insufficient data to determine this."* when evidence is missing
- **AI Email:** personalised drafts from one lead's facts; editable; "open in my email app" link — never auto-sent
- **Marketing:** LinkedIn post, short post, campaign ideas, content calendar — with Gemini or from a no-AI template; editable; **"Open in LinkedIn / X"** opens the composer with your text pre-filled and *you* press Post; draft → approved → posted tracking. The app never posts by itself (LinkedIn API posting is a future integration)
- **Import Data:** upload **all your CSV/XLSX files at once** (leads, opportunities, activities, companies, contacts, campaigns) → content-based auto-detect with fuzzy headers → cross-file validation with automatic foreign-key fallbacks → error report with file/row → **SKIP / UPSERT / OVERWRITE** duplicate modes → confirmation → atomic import, plus a one-click validation reset. Includes a template ZIP and a "which files do I need" guide; a single flat-file mode is also available
- **Settings:** health check (Database, Demo Data, Gemini, Environment, Application), data mode, persistence notes

## Demo mode vs Real mode
| | Demo (`DATA_MODE=demo`) | Real (`DATA_MODE=real`) |
|---|---|---|
| Data | Synthetic, auto-generated on first launch | Starts empty; you import CSV/XLSX |
| Database file | `data/brightbridge_demo.db` | `data/brightbridge_real.db` |
| Import | Disabled (protects demo) | Enabled |

Switch any time in **Settings** or from the welcome screen. The rest of the app is identical.

## Gemini setup
1. Create a key in Google AI Studio.
2. Locally: put it in `.env` as `GEMINI_API_KEY=...`. On Hugging Face: add it as a **Space Secret** (never in code/repo).
3. Optional `GEMINI_MODEL` (default `gemini-flash-latest`; if a model is unavailable the app tries fallbacks automatically).
4. **Verify it:** Settings → *Test Gemini connection*. The header badge shows **connected ✓** only after a real test request succeeded; a key that is merely present shows "key found, not verified".

Without a key the app still runs: *"Gemini AI is not configured. CRM analytics remain available."* Scores, dashboards, pipeline, follow-ups, import/export and a plain-template email all keep working.

## Local setup (Windows + Anaconda)
```bat
conda create -n brightbridge_ai python=3.11 -y
conda activate brightbridge_ai
pip install -r requirements.txt
copy .env.example .env
:: edit .env and set GEMINI_API_KEY
python scripts/initialize_database.py
python scripts/generate_demo_data.py
python app.py
```
Then open **http://127.0.0.1:7860** in your browser. Details: [docs/local_setup.md](docs/local_setup.md).

## Hugging Face Spaces
See [docs/huggingface_deployment.md](docs/huggingface_deployment.md). Short version: create a Gradio Space, push these files, add `GEMINI_API_KEY` under *Settings → Variables and secrets*. **Not yet verified on a live Space** — follow the verification checklist in the guide and record your result.

## Testing
```bash
pip install -r requirements-dev.txt
pytest
```
148 tests cover database/foreign keys, demo data, scoring, next actions, agent tools and filters, guardrails, single- and multi-file CSV/XLSX import, marketing/posting links, export, health and app startup. The real `google-genai` SDK (including function calling, model fallback and invalid-key handling) is tested against a local fake Gemini server, so no internet or real key is needed.

## Documentation
[Local setup](docs/local_setup.md) · [Hugging Face](docs/huggingface_deployment.md) · [Architecture](docs/architecture.md) · [Data import](docs/data_import.md) · [Security](docs/security.md)

## Limitations
- Single-tenant, no user login (add authentication before exposing real customer data publicly).
- Free Hugging Face Spaces use an **ephemeral** disk: demo data regenerates on restart; imported real data may be lost. Use PostgreSQL (`DATABASE_URL`) for real data.
- PostgreSQL support is implemented in the abstraction but only SQLite is covered by the automated tests.
- Gemini quality/availability and free-tier quotas depend on Google.
- Intent routing for the assistant is rule-based; unusual questions fall back to Gemini tool-calling (when configured) or "Insufficient data".
- Gemini was tested against a local fake server, not Google's live API — use Settings → Test Gemini connection with your key.
- Docker files are provided but were not built in the authoring environment.

## Roadmap
Authentication · PostgreSQL CI tests · Gmail/Outlook drafts · Calendar · HubSpot/Salesforce/Zoho connectors · WhatsApp/LinkedIn · multi-agent orchestrator (Sales, Marketing, Analytics → Follow-up, Campaign, Reporting agents).

## License
MIT © Niteesh Pandey
