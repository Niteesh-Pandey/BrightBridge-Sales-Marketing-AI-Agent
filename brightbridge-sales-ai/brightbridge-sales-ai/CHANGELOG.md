# Changelog

## v0.1.3 — chart, scoring & pipeline fixes
- **Dashboard charts:** month axis is now real timestamps with `%b %Y` ticks (no more `23:59:59.999 Sep 30` labels), ₹ axes use plain grouped numbers (no `741.499k`-style ticks), all value axes anchored at zero (campaign chart can never show a `-1…1` range)
- **Weighted pipeline fixed:** demo deals now carry stage-based `win_probability` (seeded + auto-migrated); missing probabilities in imported data fall back to stage estimates and are labelled "stage-based estimate" instead of a broken "needs win-probability data" ₹0
- **Stale demo data auto-regenerates:** if a saved demo database is older than 45 days, fresh data is generated on start — recency scores, CALL NOW leads and due follow-ups no longer collapse to 0 after time passes; demo generator now also produces a healthy share of genuinely hot leads
- **Pipeline tab:** funnel is sorted by value so percentages are always ≤100% (the 131% bug); the headline separates **open pipeline** from **closed-won** instead of adding them together; empty exports show a message instead of downloading blank files
- **Follow-ups tab:** date comparison is None-safe (no more empty table from a comparison crash); empty state explains what to do; upcoming-slider row no longer overlaps the export row
- **Lead Priority tab:** slider/export controls split into separate rows (no overlap); empty level shows the counts of the other levels; export button refuses empty tables with a message
- **CRM tab:** min/max deal value filters treat 0 as "no limit" on both sides and say so in the label; row-click handler guards against empty/invalid selections
- **AI Email tab:** lead id is validated before any Gemini call (no blank API requests); mailto link is always URL-encoded and refuses empty drafts with a message; empty lead list shows guidance
- **Requirements pinned:** plotly>=6.0 (with gradio 6.x) — older plotly/gradio combinations were the cause of charts rendering as lines/skewed numbers and of broken row-click events

## v0.1.2 — ingestion & dashboard hardening
- **Dashboard TypeError fixed (2 root causes):** division by zero impressions/leads produced `pd.NA` (`float() … NAType`), and deal values stored as text broke comparisons. New `services/sanitize.py` (currency/comma/lakh/crore parsing, NaN→0, `safe_call`), type-safe loaders, per-KPI and per-chart fallbacks (a failing chart shows a placeholder, never crashes the tab)
- **Fuzzy header mapping** (RapidFuzz, with difflib fallback): handles synonyms, word order, camelCase, typos ("Compnay Name", "Deal Valeu"), noise words (INR, of, the)
- **Content-based file classification** (columns + values), file name is only a tie-breaker: a `companies.csv` containing stage + value is treated as deals; lead *Status* is not mistaken for a deal stage; cryptic headers are inferred from values
- **Foreign-key fallbacks:** deals/activities resolve company by id → normalised name; unknown companies/contacts/leads are created automatically with an informational notice (no more "No lead found" errors)
- **Duplicate modes SKIP / UPSERT / OVERWRITE** (UPSERT never blanks data with empty cells; OVERWRITE needs typed confirmation, shows what will be deleted, saves a backup ZIP, runs atomically)
- **Reset:** backend `reset_import_session()` + "Reset validation & start fresh" button in both import tabs; clear explanation when everything is a duplicate
- Lenient parsing: invalid e-mails and non-numeric amounts no longer block rows (warning + safe value); more date formats; loose stage/activity vocabulary ("won", "negotiating", "zoom call")
- Optional `win_probability` (+ automatic DB migration), new KPIs (average deal size, win rate, weighted pipeline); flat-file import now uses the same engine
- 148 tests

## v0.1.1
- **Gemini status is now honest:** "connected ✓" only after a real test request; key-present-but-unverified, error and not-configured states; Settings → *Test Gemini connection*; background check at start-up
- **Gemini reliability:** default model `gemini-flash-latest` with automatic fallback/discovery (older models such as `gemini-2.0-flash` are retired); clear error reasons (invalid key, quota, model, network)
- **Fixed two function-calling bugs** that broke free-form AI questions (tool deep-copy and string type hints)
- **Multi-file import:** upload all CSV/XLSX files at once; auto-detection; cross-file link validation; template ZIP + guide; file/row error report
- **AI assistant:** new `query_leads` tool and filter questions (industry, city, stage, source, priority, value, inactivity, counts)
- **Marketing:** no-AI template mode, LinkedIn/X pre-filled posting links (user presses Post), draft → approved → posted status
- Empty-workspace guidance, chained refresh after mode switch/import, 114 tests (incl. real SDK vs fake Gemini server)

## v0.1.0 — MVP
- SQLite database layer (PostgreSQL-ready via `DATABASE_URL`), schema with foreign keys and indexes
- Synthetic demo data generator (Indian companies, cities, INR deals) with CLI parameters
- Deterministic lead scoring (0–100), priority bands and next-action engine (every action has WHY + SOURCE DATA)
- Evidence-first AI agent with 12 CRM tools; Gemini function calling and deterministic fallback
- AI email drafts, LinkedIn/short posts, campaign ideas, content calendar (drafts only)
- CSV/XLSX import with column detection, mapping, validation, error report and confirmation
- Gradio UI: Dashboard, CRM, Lead Priority, Pipeline, Follow-ups, AI Email, Marketing, AI Assistant, Import Data, Settings
- CSV/XLSX export, health check, Docker, Hugging Face Spaces configuration, documentation, pytest suite
