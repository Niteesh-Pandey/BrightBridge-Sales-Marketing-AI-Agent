"""Generate realistic, fully synthetic demo data for BrightBridge Solutions.

Everything produced here is fictional: company names, people, e-mails
(example.com), phone numbers (000 prefix style) and deals.

Usage:
    python scripts/generate_demo_data.py
    python scripts/generate_demo_data.py --companies 200 --leads 750 --opportunities 400 --activities 2000 --campaigns 20
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from database.db import Database  # noqa: E402
from database.models import (  # noqa: E402
    ACTIVITY_TYPES, INDUSTRIES, LEAD_SOURCES, OPEN_STAGES,
)

CITIES = [("Mumbai", "Maharashtra"), ("Pune", "Maharashtra"), ("Nagpur", "Maharashtra"), ("Delhi", "Delhi"),
          ("Gurugram", "Haryana"), ("Noida", "Uttar Pradesh"), ("Lucknow", "Uttar Pradesh"), ("Bengaluru", "Karnataka"),
          ("Mysuru", "Karnataka"), ("Chennai", "Tamil Nadu"), ("Coimbatore", "Tamil Nadu"), ("Hyderabad", "Telangana"),
          ("Ahmedabad", "Gujarat"), ("Surat", "Gujarat"), ("Jaipur", "Rajasthan"), ("Kolkata", "West Bengal"),
          ("Indore", "Madhya Pradesh"), ("Bhopal", "Madhya Pradesh"), ("Kochi", "Kerala"), ("Chandigarh", "Chandigarh")]
PREFIX = ["Blue", "Apex", "Nova", "Prime", "Urban", "Zen", "Terra", "Vista", "Orbit", "Swift", "Pioneer", "Crest", "Lotus",
          "Metro", "Evergreen", "Sunrise", "Horizon", "Vertex", "Anchor", "Kite", "Maple", "Indigo", "Saffron", "Delta",
          "Summit", "Harbor", "Amber", "Cobalt", "Falcon", "Granite"]
SUFFIX = {
    "Logistics": ["Logistics", "Freight Lines", "Cargo Systems"], "Manufacturing": ["Industries", "Fabricators", "Components"],
    "Retail": ["Retail", "Mart", "Stores"], "Healthcare": ["Healthcare", "Diagnostics", "Clinics"],
    "Education": ["Academy", "Learning", "Edu Services"], "Financial Services": ["Capital", "Finserv", "Advisors"],
    "Real Estate": ["Realty", "Estates", "Developers"], "Hospitality": ["Hospitality", "Hotels", "Stays"],
    "IT Services": ["Digital Systems", "Tech Solutions", "Softworks"], "Agriculture": ["Agro", "Farms", "Agritech"],
}
FIRST = ["Aarav", "Vivaan", "Aditya", "Kabir", "Rohan", "Ishaan", "Arjun", "Rahul", "Karan", "Siddharth", "Ananya", "Diya",
         "Meera", "Priya", "Neha", "Kavya", "Ritu", "Sneha", "Pooja", "Isha", "Manish", "Suresh", "Deepak", "Anil", "Nisha"]
LAST = ["Sharma", "Verma", "Iyer", "Nair", "Reddy", "Patel", "Mehta", "Gupta", "Singh", "Kapoor", "Joshi", "Bose", "Menon",
        "Chopra", "Desai", "Kulkarni", "Agarwal", "Malhotra", "Rao", "Banerjee"]
TITLES = ["Founder & CEO", "Managing Director", "Head of Operations", "COO", "VP Sales", "Marketing Head",
          "Operations Manager", "IT Head", "Finance Controller", "General Manager"]
SIZES = ["11-50", "51-200", "201-500", "501-1000"]
DEAL_TEMPLATES = ["Workflow Automation", "CRM Setup & Automation", "Process Consulting", "Reporting Dashboard Build",
                  "Invoice Automation", "Sales Ops Automation", "Customer Onboarding Workflow"]
CHANNELS = ["LinkedIn", "Email", "Webinar", "Content / SEO", "Events"]
CAMPAIGN_NAMES = ["Automation Audit Drive", "Workflow Webinar Series", "SME Efficiency Playbook", "Ops Cost Saver Outreach",
                  "Festive Season Productivity Push", "LinkedIn Thought Leadership", "Case-Style Guide Launch",
                  "CRM Cleanup Challenge", "Quarter-End Planning Mailer", "Manufacturing Automation Roadshow"]
OUTCOMES = {"Call": ["Connected - positive", "Connected - needs time", "No answer", "Left voicemail"],
            "Meeting": ["Discovery completed", "Demo completed", "Stakeholders aligned", "Rescheduled"],
            "Demo": ["Demo completed", "Asked for pricing"], "Email Sent": ["Sent"], "Email Opened": ["Opened"],
            "Email Replied": ["Replied - interested", "Replied - asked for details"], "Note": ["Internal note"]}


def _iso(d: date) -> str:
    return d.isoformat()


def generate(n_companies=200, n_leads=750, n_opps=400, n_acts=2000, n_campaigns=20, seed=42, today: date | None = None) -> dict:
    """Return dict of lists-of-dict, one per table, with consistent integer ids."""
    rnd = random.Random(seed)
    today = today or date.today()
    data = {k: [] for k in ["companies", "contacts", "campaigns", "leads", "opportunities", "activities"]}

    for i in range(1, n_campaigns + 1):
        start = today - timedelta(days=rnd.randint(20, 330))
        end = start + timedelta(days=rnd.randint(14, 75))
        impressions = rnd.randint(8_000, 160_000)
        clicks = int(impressions * rnd.uniform(0.01, 0.06))
        data["campaigns"].append({
            "id": i, "name": f"{CAMPAIGN_NAMES[(i - 1) % len(CAMPAIGN_NAMES)]} {2025 + (i - 1) // len(CAMPAIGN_NAMES)}".strip(),
            "channel": rnd.choice(CHANNELS), "start_date": _iso(start), "end_date": _iso(end),
            "budget_inr": rnd.choice([25000, 40000, 60000, 85000, 120000, 200000]), "impressions": impressions,
            "clicks": clicks, "conversions": 0})

    used_names, contact_id = set(), 0
    for cid in range(1, n_companies + 1):
        ind = rnd.choice(INDUSTRIES)
        for _ in range(30):
            name = f"{rnd.choice(PREFIX)} {rnd.choice(SUFFIX[ind])}"
            if name not in used_names:
                break
            name = f"{name} {rnd.choice(['Pvt Ltd', 'India', 'Group', 'Co'])}"
            if name not in used_names:
                break
        used_names.add(name)
        city, state = rnd.choice(CITIES)
        slug = "".join(ch for ch in name.lower() if ch.isalnum())[:18]
        data["companies"].append({"id": cid, "name": name, "industry": ind, "city": city, "state": state,
                                  "size": rnd.choice(SIZES), "website": f"https://www.{slug}{cid}.example.com",
                                  "created_at": _iso(today - timedelta(days=rnd.randint(30, 400)))})
        for _ in range(rnd.choice([1, 1, 2])):
            contact_id += 1
            fn, ln = rnd.choice(FIRST), rnd.choice(LAST)
            data["contacts"].append({
                "id": contact_id, "company_id": cid, "full_name": f"{fn} {ln}", "job_title": rnd.choice(TITLES),
                "email": f"{fn.lower()}.{ln.lower()}{contact_id}@{slug}{cid}.example.com",
                "phone": f"+91 90000 {rnd.randint(10000, 99999)}",
                "created_at": _iso(today - timedelta(days=rnd.randint(20, 380)))})
    contacts_by_company = {}
    for c in data["contacts"]:
        contacts_by_company.setdefault(c["company_id"], []).append(c)

    for lid in range(1, n_leads + 1):
        comp = rnd.choice(data["companies"])
        contact = rnd.choice(contacts_by_company[comp["id"]])
        source = rnd.choice(LEAD_SOURCES)
        camp = rnd.choice(data["campaigns"])["id"] if source in ("Email Campaign", "Webinar", "LinkedIn") or rnd.random() < 0.2 else None
        created = today - timedelta(days=rnd.randint(5, 300))
        data["leads"].append({"id": lid, "company_id": comp["id"], "contact_id": contact["id"], "campaign_id": camp,
                              "source": source, "status": rnd.choices(["New", "Contacted", "Qualified", "Unqualified"], [20, 35, 35, 10])[0],
                              "owner": rnd.choice(["Niteesh Pandey", "Riya Menon", "Aman Khanna"]),
                              "notes": None, "created_at": _iso(created)})
        if camp:
            data["campaigns"][camp - 1]["conversions"] += 1

    # opportunities: spread across distinct leads where possible
    lead_ids = [l["id"] for l in data["leads"]]
    rnd.shuffle(lead_ids)
    lead_by_id = {l["id"]: l for l in data["leads"]}
    comp_by_id = {c["id"]: c for c in data["companies"]}
    # stage-based win probability (with small jitter) so weighted pipeline is always computable
    PROB_BASE = {"Prospecting": 15, "Qualification": 30, "Proposal": 55, "Negotiation": 75,
                 "Closed Won": 100, "Closed Lost": 0}
    for oid in range(1, n_opps + 1):
        lead_id = lead_ids[(oid - 1) % len(lead_ids)]
        lead = lead_by_id[lead_id]
        stage = rnd.choices(["Prospecting", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"],
                            [14, 20, 22, 14, 18, 12])[0]
        created = max(date.fromisoformat(lead["created_at"]), today - timedelta(days=rnd.randint(5, 260)))
        value = round(rnd.choice([45000, 75000, 120000, 180000, 250000, 320000, 420000, 600000, 850000]) * rnd.uniform(0.85, 1.2), -3)
        closed_at = None
        if stage in ("Closed Won", "Closed Lost"):
            closed_at = _iso(min(today, created + timedelta(days=rnd.randint(14, 90))))
            expected = closed_at
            prob = float(PROB_BASE[stage])
        else:
            expected = _iso(today + timedelta(days=rnd.randint(-10, 90)))
            prob = float(min(95, max(5, PROB_BASE[stage] + rnd.randint(-8, 8))))
        data["opportunities"].append({
            "id": oid, "lead_id": lead_id, "name": f"{rnd.choice(DEAL_TEMPLATES)} - {comp_by_id[lead['company_id']]['name']}",
            "stage": stage, "value_inr": float(value), "expected_close_date": expected,
            "created_at": _iso(created), "closed_at": closed_at, "win_probability": prob})
    opp_by_lead = {}
    for o in data["opportunities"]:
        opp_by_lead.setdefault(o["lead_id"], []).append(o)

    # activities: "hot" leads get recent, richer activity; "very hot" leads get meetings/replies
    # within the last few days, so the CALL NOW band is always populated with real examples
    hot = set(rnd.sample(lead_ids, k=max(1, int(len(lead_ids) * 0.35))))
    very_hot = set(rnd.sample(sorted(hot), k=max(1, int(len(hot) * 0.25))))
    weights = [8 if l["id"] in very_hot else 5 if l["id"] in hot else 1 for l in data["leads"]]
    for aid in range(1, n_acts + 1):
        lead = rnd.choices(data["leads"], weights)[0]
        opp = rnd.choice(opp_by_lead[lead["id"]]) if lead["id"] in opp_by_lead else None
        created = date.fromisoformat(lead["created_at"])
        max_age = max(1, (today - created).days)
        if lead["id"] in very_hot:
            age = rnd.randint(0, min(max_age, 7))
            atype = rnd.choices(ACTIVITY_TYPES, [10, 14, 16, 20, 22, 16, 2])[0]   # replies/meetings/demos heavy
        else:
            age = rnd.randint(0, min(max_age, 14 if lead["id"] in hot else max_age))
            atype = rnd.choices(ACTIVITY_TYPES, [22, 22, 8, 18, 12, 6, 12])[0]
        when = today - timedelta(days=age)
        follow = None
        if atype in ("Call", "Meeting", "Demo", "Email Replied") and age <= 45 and rnd.random() < 0.6:
            follow = _iso(when + timedelta(days=rnd.randint(2, 14)))
        data["activities"].append({
            "id": aid, "lead_id": lead["id"], "opportunity_id": opp["id"] if opp else None, "type": atype,
            "activity_date": _iso(when), "outcome": rnd.choice(OUTCOMES[atype]),
            "notes": f"Synthetic {atype.lower()} record.", "follow_up_date": follow})
    return data


def write_csvs(data: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in data.items():
        pd.DataFrame(rows).to_csv(out_dir / f"{name}.csv", index=False)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--companies", type=int, default=200)
    p.add_argument("--leads", type=int, default=750)
    p.add_argument("--opportunities", type=int, default=400)
    p.add_argument("--activities", type=int, default=2000)
    p.add_argument("--campaigns", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--database-url", default=None, help="Defaults to the demo database")
    p.add_argument("--no-db", action="store_true", help="Only write CSV files")
    p.add_argument("--csv-dir", default=None, help="Where to write CSV copies (default: data/demo)")
    a = p.parse_args(argv)
    for name in ("companies", "leads", "opportunities", "activities", "campaigns"):
        if getattr(a, name) < 1:
            p.error(f"--{name} must be >= 1")

    data = generate(a.companies, a.leads, a.opportunities, a.activities, a.campaigns, a.seed)
    csv_dir = Path(a.csv_dir) if a.csv_dir else config.DEMO_DIR
    write_csvs(data, csv_dir)
    print(f"CSV files written to {csv_dir}")
    if not a.no_db:
        from database.seed import seed_database
        db = Database(a.database_url or config.default_database_url("demo"))
        db.initialize()
        seed_database(db, data)
        print(f"Database seeded: {db.url}")
        for t in ("companies", "contacts", "leads", "opportunities", "activities", "campaigns"):
            print(f"  {t:<14}{db.count(t)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
