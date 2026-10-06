# Deploying to Hugging Face Spaces

> Status: configuration prepared; **not verified on a live Space by the author**. Complete the checklist below and note the result.

1. Create a free account at huggingface.co.
2. **New Space** → SDK **Gradio** → hardware **CPU basic (free)** → visibility as you prefer.
3. Push the project files (the README front matter already declares `sdk: gradio`, `app_file: app.py`):
```bash
git clone https://huggingface.co/spaces/<you>/<space-name>
# copy project files in (do NOT copy .env or *.db), then:
git add . && git commit -m "BrightBridge v0.1.0" && git push
```
4. Space **Settings → Variables and secrets**: add **Secret** `GEMINI_API_KEY`. Optional variables: `GEMINI_MODEL` (default `gemini-flash-latest`), `DATA_MODE=demo`.
5. The Space builds and starts automatically. Wait for status *Running*.
6. **Verification checklist**
   - [ ] Welcome screen appears; click *Demo Mode*
   - [ ] Banner shows "DEMO MODE — Synthetic Business Data"
   - [ ] Dashboard KPIs and 6 charts render
   - [ ] CRM filters and lead detail work
   - [ ] Lead Priority shows scores; export downloads a file
   - [ ] Settings → Health: Database ✓, Demo Data ✓, Gemini ✓ (or "Not configured")
   - [ ] AI Assistant: "Which leads should I call today?" returns ANSWER / SUPPORTING DATA / SUGGESTED ACTION
   - [ ] Settings → *Test Gemini connection* says "Connected" (header badge turns green ✓)
   - [ ] AI Email generates a draft (requires the secret)
   - [ ] Import Data: upload several CSVs together (Real mode) and import
7. **Docker Space alternative:** change README front matter to `sdk: docker` and `app_port: 7860`; the included `Dockerfile` runs as UID 1000.

## Limitations of free hardware
- Free CPU Spaces **sleep after inactivity** and restart cold (demo data is regenerated).
- The disk is **ephemeral**: anything imported in Real mode can disappear on restart. Do not treat it as a production database.
- For real data set `DATABASE_URL` (Space Secret) to a persistent PostgreSQL database and install `psycopg[binary]` (uncomment in `requirements.txt`).
- No GPU is needed; no local model is downloaded. Gemini is called over the network.
- Never put the API key in code, `requirements.txt`, the repo or public files.
