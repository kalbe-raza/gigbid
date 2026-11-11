"""Turn the raw Kaggle downloads into the compact market files GigSim and the evals use.

Real data only. Nothing in this script invents a job, a price or a client: it parses, filters,
anonymises and aggregates. Synthetic data (personas, scams, injections, hidden client preferences)
is produced separately by scripts/generate_personas.py and scripts/build_dev_set.py and is labelled
`"synthetic": true` everywhere it appears.

Usage:  python scripts/prepare_market_data.py --raw ../../data_raw --out data/market
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

csv.field_size_limit(10**9)

# Persona domain -> Upwork categories that belong to it (as they appear in the raw data)
DOMAINS: dict[str, list[str]] = {
    "ai_ml": [
        "Machine Learning", "Deep Learning", "AI Chatbot Development", "Generative AI Modeling",
        "AI Integration", "Data Mining",
    ],
    "fullstack": [
        "Full Stack Development", "Back-End Development", "Front-End Development",
        "Ecommerce Website Development", "Database Development", "CMS Development",
    ],
    "data_analyst": [
        "Data Analytics", "Data Visualization", "Financial Analysis & Modeling", "Data Extraction",
        "Data Processing", "Data Engineering", "Business Analysis & Strategy",
    ],
}
OFF_DOMAIN = [
    "Graphic Design", "Video Editing", "Social Media Marketing", "SEO", "Mobile App Development",
    "Data Entry", "Lead Generation", "Creative Writing", "Logo Design", "Accounting",
    "General Virtual Assistance", "Scripting & Automation", "Web Design", "DevOps Engineering",
    "Systems Administration", "UX/UI Design",
]
CAT_TO_DOMAIN = {c: d for d, cats in DOMAINS.items() for c in cats}

SOURCES = {
    "upwork50k": "kaggle:asaniczka/upwork-job-postings-dataset-2024-50k-records (ODC-By)",
    "upwork60k": "kaggle:ahmedmyalo/upwork-freelance-jobs-60k (Apache-2.0)",
    "contracts": "kaggle:asaniczka/freelance-contracts-dataset-1-3-million-entries (ODC-By)",
    "gigsuccess": "kaggle:sarveshchhetri/freelancer-success-earnings-and-churn-prediction (Apache-2.0)",
}

META_RE = re.compile(
    r"(?P<body>.*?)(?:(?:Budget:\s*\$(?P<budget>[\d,\.]+))|(?:Hourly Range:\s*\$(?P<hlo>[\d\.]+)-\$(?P<hhi>[\d\.]+))|(?:Hourly))?"
    r"\s*Posted On:\s*(?P<posted>[^C]+?UTC)\s*Category:\s*(?P<cat>.+?)Skills:(?P<skills>.*)$",
    re.S,
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace(" ", " ")
    # Raw posts glue bullet lines together ("tasks:- Setting up"); restore readable breaks.
    text = re.sub(r"(?<=[a-z.:!?)])-\s(?=[A-Z])", "\n- ", text)
    text = re.sub(r"[ \t]+", " ", text)
    # Personal contact details are never kept, even in "real" data.
    text = EMAIL_RE.sub("[email removed]", text)
    text = PHONE_RE.sub("[phone removed]", text)
    return text.strip()


def _num(x: str | None) -> float | None:
    if x is None:
        return None
    x = str(x).replace(",", "").replace("$", "").strip()
    try:
        v = float(x)
    except ValueError:
        return None
    return v if v == v else None  # NaN guard


def parse_upwork50k(path: Path) -> list[dict]:
    jobs, seen = [], set()
    for r in csv.DictReader(path.open(encoding="utf-8")):
        m = META_RE.match(r.get("description") or "")
        if not m:
            continue
        cat = m.group("cat").strip()
        if len(cat) > 60:  # a few rows have the body glued into the category field
            continue
        # The RSS capture repeats "Skills:" and glues "Country: X" / "click to apply" onto the list.
        skills_raw = re.split(r"Skills:|Country:|click to apply", m.group("skills"))[0]
        skills = [s.strip() for s in re.split(r",\s*", skills_raw) if s.strip()]
        skills = list(dict.fromkeys(s for s in skills if len(s) < 50))[:15]
        body = _clean(m.group("body"))
        if len(body) < 120:
            continue
        key = (r["title"].strip().lower(), body[:200].lower())
        if key in seen:  # exact duplicates exist in the RSS capture
            continue
        seen.add(key)
        is_hourly = str(r.get("is_hourly")).lower() == "true"
        jobs.append({
            "source_id": _id(r["link"]),
            "title": _clean(r["title"]),
            "description": body,
            "category": cat,
            "domain": CAT_TO_DOMAIN.get(cat, "off_domain" if cat in OFF_DOMAIN else "other"),
            "skills": skills,
            "is_hourly": is_hourly,
            "budget": _num(m.group("budget")) if not is_hourly else None,
            "hourly_low": _num(m.group("hlo")) if is_hourly else None,
            "hourly_high": _num(m.group("hhi")) if is_hourly else None,
            "posted": m.group("posted").strip(),
            "country": r.get("country") or None,
            "source": SOURCES["upwork50k"],
            "synthetic": False,
        })
    return jobs


def parse_upwork60k(path: Path) -> list[dict]:
    out = []
    reader = csv.DictReader(path.open(encoding="utf-8-sig"))
    for r in reader:
        cats = [r.get(f"Category_{i}", "").strip() for i in range(1, 10)]
        cats = [c for c in cats if c]
        payment_type = (r.get("Payment_type") or "").strip()
        out.append({
            "source_id": _id(r.get("Job_URL", ""), r.get("Job Title", "")),
            "title": _clean(r.get("Job Title", "")),
            "search_keyword": r.get("Search_Keyword", ""),
            "skills": cats,
            "experience_level": r.get("EX_level_demand") or None,
            "payment_type": payment_type,
            "job_cost": _num(r.get("Job_Cost")),
            "hourly_low": _num(r.get("Start_rate")),
            "hourly_high": _num(r.get("End_rate")),
            "connects_required": _num(r.get("Connects_Num")),
            "applicants": (r.get("Applicants_Num") or "").strip() or None,
            "client": {
                "payment_verified": (r.get("Payment_Situation") or "").strip() == "Payment verified",
                "total_spent": _num(r.get("Spent($)")) or 0.0,
                "rating": _num(r.get("Rating")),
                "feedback_count": int(_num(r.get("Feedback_Num")) or 0),
                "country": (r.get("Client_Country") or "").strip() or None,
                "enterprise": bool((r.get("Enterprise_Client") or "").strip()),
                "hires": int(_num(r.get("Freelancers_Num")) or 0),
            },
            "source": SOURCES["upwork60k"],
            "synthetic": False,
        })
    return out


KEYWORD_DOMAIN = {
    "ai_ml": ["machine learning", "deep learning", "ai ", "llm", "nlp", "computer vision", "chatbot", "pytorch",
              "tensorflow", "gpt", "langchain", "rag", "data scien"],
    "fullstack": ["full stack", "fullstack", "react", "django", "node", "next.js", "laravel", "backend",
                  "back-end", "frontend", "front-end", "web app", "api", "vue", "fastapi"],
    "data_analyst": ["data analy", "power bi", "tableau", "excel", "dashboard", "sql", "looker", "analytics",
                     "data visual", "etl", "dbt", "data engineer"],
}


def _kw_domain(title: str, extra: str = "") -> str | None:
    t = f" {title.lower()} {extra.lower()} "
    for d, kws in KEYWORD_DOMAIN.items():
        if any(k in t for k in kws):
            return d
    return None


def historical_jobs(rows60k: list[dict], n: int, rng: random.Random) -> list[dict]:
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for r in rows60k:
        d = _kw_domain(r["title"], " ".join(r["skills"]) + " " + r["search_keyword"])
        if d and (r["job_cost"] or r["hourly_low"]):
            r = dict(r, domain=d)
            by_domain[d].append(r)
    per = n // len(by_domain)
    out = []
    for _d, rows in sorted(by_domain.items()):
        rng.shuffle(rows)
        out.extend(rows[:per])
    return out


def contract_rates(path: Path) -> dict:
    """Hourly-rate percentiles from 1.3M real contracts, by domain keyword."""
    vals: dict[str, list[float]] = defaultdict(list)
    for r in csv.DictReader(path.open(encoding="utf-8")):
        rate = _num(r.get("hourly_rate"))
        if not rate or rate < 3 or rate > 300:
            continue
        d = _kw_domain(r.get("job_title", ""))
        if d:
            vals[d].append(rate)
    res = {}
    for d, v in vals.items():
        a = np.array(v)
        res[d] = {"n": int(a.size), **{f"p{p}": round(float(np.percentile(a, p)), 2) for p in (10, 25, 50, 75, 90)}}
    return res


def freelancer_benchmarks(path: Path) -> dict:
    groups = {"ai_ml": "AI & Machine Learning", "fullstack": "Web Development", "data_analyst": "Data Science & Analytics"}
    res = {}
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    for d, skill in groups.items():
        rs = [r for r in rows if r["primary_skill"] == skill]
        rate = np.array([_num(r["hourly_rate_usd"]) for r in rs if _num(r["hourly_rate_usd"])])
        jss = np.array([_num(r["job_success_rate"]) for r in rs if _num(r["job_success_rate"])])
        res[d] = {
            "n": len(rs),
            "hourly_rate_p50": round(float(np.median(rate)), 2),
            "job_success_p50": round(float(np.median(jss)), 1),
            "note": "dataset is self-described as 'real-world style'; used only for persona calibration",
        }
    return res


def price_stats(hist: list[dict]) -> dict:
    """Fixed-price and hourly percentiles per domain from the historical jobs (used by Pricing)."""
    res = {}
    for d in sorted({h["domain"] for h in hist}):
        fixed = np.array([h["job_cost"] for h in hist if h["domain"] == d and h["job_cost"] and h["job_cost"] >= 5])
        hourly = np.array([
            (h["hourly_low"] + (h["hourly_high"] or h["hourly_low"])) / 2
            for h in hist if h["domain"] == d and h["hourly_low"]
        ])
        conn = np.array([h["connects_required"] for h in hist if h["domain"] == d and h["connects_required"]])
        res[d] = {
            "fixed": {"n": int(fixed.size), **{f"p{p}": round(float(np.percentile(fixed, p)), 2) for p in (10, 25, 50, 75, 90)}},
            "hourly": {"n": int(hourly.size), **{f"p{p}": round(float(np.percentile(hourly, p)), 2) for p in (10, 25, 50, 75, 90)}},
            "connects": {"n": int(conn.size), **{f"p{p}": round(float(np.percentile(conn, p)), 1) for p in (25, 50, 75)}},
        }
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="../../data_raw")
    ap.add_argument("--out", default="data/market")
    ap.add_argument("--seed", type=int, default=794)
    ap.add_argument("--historical", type=int, default=1500)
    a = ap.parse_args()
    raw, out = Path(a.raw), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)

    posts = parse_upwork50k(raw / "upwork50k/upwork-jobs.csv")
    in_domain = [p for p in posts if p["domain"] in DOMAINS]
    off = [p for p in posts if p["domain"] == "off_domain"]
    rng.shuffle(off)
    keep = in_domain + off[:4000]  # off-domain posts are only needed as "skip" negatives
    with (out / "job_posts.jsonl").open("w") as f:
        for p in keep:
            f.write(json.dumps(p) + "\n")

    rows60 = parse_upwork60k(raw / "upwork-freelance-jobs-60k/Final_Upwork_Dataset.csv")
    hist = historical_jobs(rows60, a.historical, rng)
    with (out / "historical_jobs.jsonl").open("w") as f:
        for h in hist:
            f.write(json.dumps(h) + "\n")
    # Real client signal distributions, sampled into GigSim client profiles.
    clients = [r["client"] for r in rows60 if r["client"]["country"]]
    rng.shuffle(clients)
    (out / "client_pool.json").write_text(json.dumps(clients[:3000]))

    stats = {
        "price": price_stats(hist),
        "contract_hourly_rates": contract_rates(raw / "freelance-contracts-dataset-1-3-million-entries/contracts.csv"),
        "freelancer_benchmarks": freelancer_benchmarks(
            raw / "freelancer-success-earnings-and-churn-prediction/freelancer_gig_success_dataset.csv"),
        "sources": SOURCES,
    }
    (out / "market_stats.json").write_text(json.dumps(stats, indent=2))
    counts = defaultdict(int)
    for p in keep:
        counts[p["domain"]] += 1
    print(json.dumps({"job_posts": dict(counts), "historical": len(hist), "clients": min(3000, len(clients))}, indent=1))


if __name__ == "__main__":
    main()
