# Local setup (Windows + Anaconda)

1. Install Anaconda and open **Anaconda Prompt**.
2. `cd` into the project folder `brightbridge-sales-ai`.
3. Run:
```bat
conda create -n brightbridge_ai python=3.11 -y
conda activate brightbridge_ai
pip install -r requirements.txt
copy .env.example .env
```
4. Open `.env` in a text editor and set `GEMINI_API_KEY=your_key` (optional — the app works without it).
5. Initialise and seed:
```bat
python scripts/initialize_database.py
python scripts/generate_demo_data.py
```
Custom size: `python scripts/generate_demo_data.py --companies 200 --leads 750 --opportunities 400 --activities 2000 --campaigns 20`
6. Start: `python app.py` → open **http://127.0.0.1:7860**.
7. Tests: `pip install -r requirements-dev.txt` then `pytest`.

The app also creates and seeds the demo database automatically on first launch, so steps 5 is optional.

**Docker (optional):** copy `.env.example` to `.env`, then `docker compose up --build` and open http://localhost:7860.

**Troubleshooting**
- *Port in use:* set `PORT=7862` (`set PORT=7862` in cmd) and rerun.
- *Gemini errors:* open Settings → **Test Gemini connection**; it shows the exact reason (invalid key, quota, model, network). The key itself is never shown.
- *Reset demo data:* Settings → Regenerate demo data, or delete `data/brightbridge_demo.db`.
