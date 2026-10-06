# Importing your real data (CSV / XLSX)

## Which files do I need?
Upload **as many of these as you have, all at once** (Import Data → *Upload all files at once*). Files are linked by **company name** — spell it the same in every file. The **Download ALL templates + guide (ZIP)** button gives you ready-made templates.

| File | Required columns | What it gives you |
|---|---|---|
| `leads.csv` ⭐ | `company_name` (+ `contact_name` or `contact_email`) | The lead list. **Minimum needed.** |
| `opportunities.csv` | `company_name`, `stage`, `value_inr` | Pipeline value, won revenue, deal value in the score |
| `activities.csv` | `company_name`, `activity_type`, `activity_date` | Engagement, recency, follow-ups, AI summaries |
| `companies.csv` | `company_name` | Industry / city filters (optional — companies are auto-created) |
| `contacts.csv` | `company_name`, `contact_name` | Contact details (optional — auto-created from leads) |
| `campaigns.csv` | `campaign_name` | Campaign charts; link leads with `campaign_name` in leads.csv |

* **Minimum:** `leads.csv`. **Best results:** leads + opportunities + activities. **Full:** all six.
* Column names are matched by synonyms (e.g. "Company", "Account", "Amount", "Deal Value", "Interaction Type"). Extra columns are ignored.
* If a company has several leads, add `contact_email` to `opportunities.csv` / `activities.csv` so each row finds the right lead.

## How files are recognised
* **By content, not by file name.** Headers are matched fuzzily ("Org Name", "Deal Size", "Stage Name", "Person Name", "Action", typos, camelCase). A file that contains *stage* and *value* is treated as deals even if it is called `companies.csv` or `export.csv`.
* If headers are cryptic, columns are inferred from their values (stage words such as *won / negotiating*, activity words such as *zoom call*, e-mail addresses).
* The *Detected as* table shows what each file was recognised as and **why**.

## When records already exist: choose a mode
| Mode | What happens |
|---|---|
| **SKIP** (default) | Existing records are kept; matching rows are ignored and listed |
| **UPSERT** | Existing records are updated with your values (empty cells never erase data); new rows are added; duplicate rows inside your upload are merged |
| **OVERWRITE** | Existing data of the uploaded entity types is deleted first (parents take their children: companies → contacts, leads, deals, activities; leads → deals, activities), then your data is inserted. A backup ZIP is saved, you must type `OVERWRITE`, and the whole step is atomic |

Seeing *"0 new … Nothing to import"*? Everything already exists. Choose **UPSERT** or **OVERWRITE**, or press **Reset validation & start fresh**.

## Missing links are created automatically
Deals and activities find their company by `company_id` (when a companies file maps ids to names) or by name. Unknown companies, contacts and leads are **created automatically** and listed under *Notices*.

## Allowed values
* `stage`: Prospecting, Qualification, Proposal, Negotiation, Closed Won, Closed Lost (aliases such as *won*, *lost* accepted)
* `activity_type`: Email Sent, Email Opened, Email Replied, Call, Meeting, Demo, Note
* `lead_status`: New, Contacted, Qualified, Unqualified
* dates: `YYYY-MM-DD`, `DD/MM/YYYY` and most common formats; amounts: `320000`, `3,20,000`, `₹3.2L`, `250k`, `$1,500`; win probability: `60`, `60%` or `0.6`
* Loose spelling is understood: stage *won / negotiating / proposal sent*, activity *phone call / zoom / sent email*

## Workflow
1. Switch to **Real mode** (welcome screen, Settings, or the button in the Import tab).
2. Upload all files → **Check & validate**. You see what each file was detected as, a per-file result, errors with **file + row number**, duplicates, warnings, and a downloadable report.
3. Fix problems in your files (optional) and re-check, or accept that bad rows are skipped.
4. Tick the confirmation box → **Import valid records**.

## Rules
* In SKIP mode existing records are never changed; UPSERT/OVERWRITE change data only when you choose them.
* Rows with real problems (no company, unknown stage/activity type, invalid date) are skipped and listed with file and row. Invalid e-mails and non-numeric amounts do **not** block a row (warning; amount stored as 0).
* Companies/contacts used in `leads.csv` but missing from `companies.csv`/`contacts.csv` are created automatically (you get a warning).
* Imports are checked against `.csv` / `.xlsx` only, 5 MB per file, up to 12 files and 20,000 rows each.
* Nothing is written until you confirm.

## One flat file
If you only have a single sheet (one row per lead with company, contact, deal and last contact date) use the *One flat file (quick)* tab.
