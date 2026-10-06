# Security & privacy

- **Secrets:** `GEMINI_API_KEY` and `DATABASE_URL` come from environment variables / Space Secrets only. `.env` is git-ignored; `.env.example` has no values. Keys are never shown in the UI, health check, logs or error messages (Gemini errors are scrubbed).
- **SQL:** every query is parameterised; table names are validated.
- **Uploads:** extension allow-list (.csv/.xlsx), size and row limits, files read as text/data only (no macros executed), validation before any write.
- **Exports:** cells starting with `= + - @` are prefixed to prevent spreadsheet formula injection.
- **Errors:** user-facing messages only; no stack traces, connection strings or keys.
- **Privacy:** Gemini receives only the selected lead's relevant facts (no phone/e-mail, no unrelated customers) or the compact evidence of the current question.
- **Human approval:** the AI cannot send e-mail, publish posts, or modify/delete CRM records; destructive chat requests are refused.
- **Prompt-injection note:** CRM text is passed as data in JSON; the system prompt forbids inventing facts and the app does not execute model output.
- **Social posting:** the app never calls LinkedIn/X APIs; "Open in LinkedIn / X" only opens a pre-filled composer in your browser.
- **Not included in MVP:** user authentication, role-based access, audit log, encryption at rest. Add these before storing real customer data in a public deployment.
