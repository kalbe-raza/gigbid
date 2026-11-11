"""Render the three SYNTHETIC persona corpora from scripts/persona_specs.py.

Output per persona under data/personas/<persona>/:
  profile.json            rates, floor, headline (synthetic)
  cv.pdf                  CV as a real PDF (the loader must parse it)
  case_studies/*.md       one per project, three different writing styles
  repos/<project>/        README.md + code files for projects that have a repo
  reviews.jsonl           client reviews tied to projects
  proposals.jsonl         ~150 past proposals with outcome won / replied / ignored
  _gold/projects.json     ground-truth skills per project (explicit + implied). Used ONLY by the
                          dev-set builder and evals; never by ingestion or agents.

The corpus deliberately contains what defeats naive RAG: the same project described three ways
(case study, README, CV line, proposals), skills only implied by code imports, and proposals whose
outcome matters more than their wording.

Usage: python scripts/generate_personas.py --seed 794
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from persona_specs import PERSONAS  # noqa: E402

CLIENT_NAMES = [
    "Northwind", "Bluegate", "Crescent", "Indus", "Harbor", "Silverline", "Orchard", "Kestrel", "Meridian",
    "Pinecrest", "Saltmarsh", "Tidewater", "Granite", "Lumen", "Sable", "Juniper", "Corvid", "Maple",
]
REVIEWER_ROLES = ["CTO", "founder", "operations manager", "product manager", "head of data", "marketing lead",
                  "engineering manager", "CEO", "finance director"]

# Small, realistic code bodies keyed by library/skill. Implied skills show up ONLY here.
SNIPPETS: dict[str, tuple[str, str]] = {
    "Celery": ("tasks.py", "from celery import Celery\n\napp = Celery(\"worker\", broker=\"redis://redis:6379/0\")\n\n\n@app.task(bind=True, max_retries=5, default_retry_delay=30)\ndef process(self, item_id: str) -> None:\n    try:\n        handle(item_id)\n    except TransientError as exc:\n        raise self.retry(exc=exc)\n"),
    "Redis": ("cache.py", "import json\nimport redis\n\nr = redis.Redis.from_url(\"redis://localhost:6379/1\")\n\n\ndef cached(key: str, ttl: int, fn):\n    hit = r.get(key)\n    if hit:\n        return json.loads(hit)\n    value = fn()\n    r.setex(key, ttl, json.dumps(value))\n    return value\n"),
    "Pinecone": ("index_store.py", "from pinecone import Pinecone\n\npc = Pinecone(api_key=settings.PINECONE_KEY)\nindex = pc.Index(\"kb-articles\")\n\n\ndef upsert(chunks):\n    index.upsert(vectors=[(c.id, c.vector, {\"article\": c.article}) for c in chunks])\n"),
    "MLflow": ("track.py", "import mlflow\n\nmlflow.set_experiment(\"churn\")\nwith mlflow.start_run():\n    mlflow.log_params(params)\n    mlflow.log_metric(\"recall_at_alerts\", recall)\n    mlflow.sklearn.log_model(model, \"model\")\n"),
    "SHAP": ("explain.py", "import shap\n\nexplainer = shap.TreeExplainer(model)\nshap_values = explainer.shap_values(X_sample)\ntop_reasons = shap_values.argsort(axis=1)[:, -3:]\n"),
    "Tesseract": ("ocr.py", "import pytesseract\nfrom PIL import Image\n\n\ndef read_page(path: str) -> str:\n    return pytesseract.image_to_string(Image.open(path), config=\"--psm 6\")\n"),
    "OpenCV": ("preprocess.py", "import cv2\n\n\ndef deskew(img):\n    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)\n    coords = cv2.findNonZero(cv2.bitwise_not(gray))\n    angle = cv2.minAreaRect(coords)[-1]\n    m = cv2.getRotationMatrix2D((img.shape[1] / 2, img.shape[0] / 2), angle, 1.0)\n    return cv2.warpAffine(img, m, (img.shape[1], img.shape[0]))\n"),
    "Docker": ("Dockerfile", "FROM python:3.11-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nCMD [\"uvicorn\", \"app:api\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]\n"),
    "LightGBM": ("train_gbm.py", "import lightgbm as lgb\n\nmodel = lgb.LGBMRegressor(n_estimators=800, learning_rate=0.03, num_leaves=63)\nmodel.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(50)])\n"),
    "Airflow": ("dags/nightly.py", "from airflow import DAG\nfrom airflow.operators.python import PythonOperator\nfrom datetime import datetime\n\nwith DAG(\"nightly_retrain\", start_date=datetime(2023, 1, 1), schedule=\"0 2 * * *\", catchup=False) as dag:\n    PythonOperator(task_id=\"retrain\", python_callable=retrain)\n"),
    "Weights & Biases": ("train_logging.py", "import wandb\n\nwandb.init(project=\"review-sentiment\", config=cfg)\nfor epoch in range(cfg.epochs):\n    loss = train_epoch()\n    wandb.log({\"loss\": loss, \"macro_f1\": evaluate()})\n"),
    "FAISS": ("ann.py", "import faiss\nimport numpy as np\n\nindex = faiss.IndexFlatIP(384)\nindex.add(np.asarray(cv_vectors, dtype=\"float32\"))\nscores, ids = index.search(query_vec, 20)\n"),
    "FastAPI": ("api.py", "from fastapi import FastAPI\nfrom pydantic import BaseModel\n\napi = FastAPI()\n\n\nclass Query(BaseModel):\n    text: str\n    k: int = 5\n\n\n@api.post(\"/match\")\ndef match(q: Query):\n    return ranker.top_k(q.text, q.k)\n"),
    "ONNX": ("export.py", "import torch\n\ntorch.onnx.export(model, dummy, \"detector.onnx\", opset_version=17, input_names=[\"images\"])\n"),
    "pytest": ("tests/test_regressions.py", "import pytest\n\n\n@pytest.mark.parametrize(\"case\", load_cases(\"cases.jsonl\"))\ndef test_clause_extraction(case):\n    result = extract(case.contract)\n    assert result.clause_type == case.expected\n"),
    "Pydantic": ("schemas.py", "from pydantic import BaseModel, Field\n\n\nclass Clause(BaseModel):\n    clause_type: str\n    text: str\n    confidence: float = Field(ge=0, le=1)\n"),
    "Isolation Forest": ("score.py", "from sklearn.ensemble import IsolationForest\n\niso = IsolationForest(n_estimators=300, contamination=0.005, random_state=7)\nscores = -iso.fit(X).score_samples(X)\n"),
    "PostgreSQL": ("db.py", "import psycopg\n\nwith psycopg.connect(DSN) as conn:\n    rows = conn.execute(\"SELECT id, amount, created_at FROM events WHERE created_at > now() - interval '1 day'\").fetchall()\n"),
    "Twilio": ("notify.py", "from twilio.rest import Client\n\nclient = Client(SID, TOKEN)\n\n\ndef remind(phone: str, body: str):\n    client.messages.create(to=phone, from_=SENDER, body=body)\n"),
    "MLflow Registry": ("registry.py", "import mlflow\n\nmlflow.register_model(\"runs:/{}/model\".format(run_id), \"triage\")\n"),
    "GitHub Actions": (".github/workflows/deploy.yml", "name: deploy\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - run: docker build -t app .\n      - run: ./scripts/blue_green.sh\n"),
    "SageMaker": ("deploy_sm.py", "from sagemaker.model import Model\n\nModel(image_uri=IMAGE, model_data=ARTIFACT, role=ROLE).deploy(initial_instance_count=1, instance_type=\"ml.m5.large\")\n"),
    "Grad-CAM": ("gradcam.py", "import tensorflow as tf\n\n\ndef grad_cam(model, img, layer=\"conv5_block3_out\"):\n    grad_model = tf.keras.models.Model(model.inputs, [model.get_layer(layer).output, model.output])\n    with tf.GradientTape() as tape:\n        conv, preds = grad_model(img)\n        loss = preds[:, tf.argmax(preds[0])]\n    return tape.gradient(loss, conv)\n"),
    "Optuna": ("tune.py", "import optuna\n\nstudy = optuna.create_study(direction=\"minimize\")\nstudy.optimize(objective, n_trials=120)\n"),
    "Label Studio": ("labeling/config.xml", "<View>\n  <Text name=\"clause\" value=\"$text\"/>\n  <Choices name=\"type\" toName=\"clause\">\n    <Choice value=\"indemnity\"/><Choice value=\"termination\"/><Choice value=\"other\"/>\n  </Choices>\n</View>\n"),
    "Google Calendar API": ("calendar_tools.py", "from googleapiclient.discovery import build\n\nsvc = build(\"calendar\", \"v3\", credentials=creds)\nbusy = svc.freebusy().query(body={\"timeMin\": start, \"timeMax\": end, \"items\": [{\"id\": cal}]}).execute()\n"),
    "Prisma": ("prisma/schema.prisma", "model Subscription {\n  id        String   @id @default(cuid())\n  customer  String\n  plan      String\n  status    String\n  periodEnd DateTime\n}\n"),
    "Webhooks": ("app/api/stripe/route.ts", "export async function POST(req: Request) {\n  const event = stripe.webhooks.constructEvent(await req.text(), req.headers.get('stripe-signature')!, secret);\n  if (event.type === 'invoice.payment_failed') await startDunning(event.data.object);\n  return new Response('ok');\n}\n"),
    "AWS S3": ("storage.py", "import boto3\n\ns3 = boto3.client(\"s3\")\ns3.upload_file(local_path, \"lesson-videos\", key, ExtraArgs={\"ContentType\": \"video/mp4\"})\n"),
    "PostGIS": ("listings/queries.py", "from django.contrib.gis.db.models.functions import Distance\n\nListing.objects.filter(location__within=polygon).annotate(d=Distance(\"location\", center)).order_by(\"d\")\n"),
    "Mapbox": ("src/MapSearch.tsx", "import mapboxgl from 'mapbox-gl';\n\nconst map = new mapboxgl.Map({ container: 'map', style: 'mapbox://styles/mapbox/streets-v12' });\nmap.on('draw.create', (e) => searchWithin(e.features[0].geometry));\n"),
    "Redis Pub/Sub": ("server/pubsub.js", "const { createClient } = require('redis');\nconst sub = createClient();\nawait sub.subscribe('chat:*', (msg) => io.to(room(msg)).emit('message', JSON.parse(msg)));\n"),
    "GraphQL": ("schema.graphql", "type Workout {\n  id: ID!\n  name: String!\n  exercises: [Exercise!]!\n}\n\ntype Query {\n  workouts(userId: ID!): [Workout!]!\n}\n"),
    "Polaris": ("web/frontend/BundleForm.jsx", "import { Card, FormLayout, TextField } from '@shopify/polaris';\n\nexport function BundleForm() {\n  return <Card><FormLayout><TextField label=\"Bundle name\" /></FormLayout></Card>;\n}\n"),
    "Vercel": ("vercel.json", "{\n  \"framework\": \"nextjs\",\n  \"images\": { \"formats\": [\"image/avif\", \"image/webp\"] }\n}\n"),
    "RabbitMQ": ("broker.py", "import pika\n\nconn = pika.BlockingConnection(pika.ConnectionParameters(\"rabbitmq\"))\nch = conn.channel()\nch.queue_declare(queue=\"tracking-events\", durable=True)\n"),
    "Kubernetes": ("k8s/deployment.yaml", "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: tracking\nspec:\n  replicas: 3\n  template:\n    spec:\n      containers:\n        - name: api\n          image: registry/tracking:latest\n"),
    "React Query": ("src/hooks/useMetrics.ts", "import { useQuery } from '@tanstack/react-query';\n\nexport const useMetrics = (range: string) =>\n  useQuery({ queryKey: ['metrics', range], queryFn: () => api.metrics(range), staleTime: 60_000 });\n"),
    "Stripe Connect": ("server/payouts.js", "const transfer = await stripe.transfers.create({ amount, currency: 'usd', destination: freelancer.stripeAccountId });\n"),
    "JWT": ("server/auth.js", "const jwt = require('jsonwebtoken');\nconst token = jwt.sign({ sub: user.id, role: user.role }, process.env.JWT_SECRET, { expiresIn: '1h' });\n"),
    "Google Maps API": ("src/tracking.js", "const directions = new google.maps.DirectionsService();\ndirections.route({ origin: store, destination: home, travelMode: 'DRIVING' }, render);\n"),
    "Jest": ("src/__tests__/QuoteForm.test.jsx", "import { render, screen } from '@testing-library/react';\n\ntest('shows premium after submit', async () => {\n  render(<QuoteForm />);\n  expect(await screen.findByText(/premium/i)).toBeInTheDocument();\n});\n"),
    "Testing Library": ("src/setupTests.js", "import '@testing-library/jest-dom';\n"),
    "OpenID Connect": ("lib/oidc.ts", "import { Issuer } from 'openid-client';\n\nconst issuer = await Issuer.discover(tenant.issuerUrl);\nconst client = new issuer.Client({ client_id: tenant.clientId, client_secret: tenant.secret });\n"),
    "Stripe": ("payments.php", "<?php\n\\Stripe\\Stripe::setApiKey(getenv('STRIPE_KEY'));\n$intent = \\Stripe\\PaymentIntent::create(['amount' => $total, 'currency' => 'usd']);\n"),
    "Nginx": ("nginx.conf", "server {\n  listen 80;\n  location / { proxy_pass http://app:3000; }\n}\n"),
    "Terraform": ("infra/main.tf", "resource \"aws_ecs_service\" \"site\" {\n  name            = var.site\n  desired_count   = 2\n  launch_type     = \"FARGATE\"\n}\n"),
    "Streaming Responses": ("app/api/suggest/route.ts", "export async function POST(req: Request) {\n  const stream = await openai.chat.completions.create({ model, messages, stream: true });\n  return new Response(toReadable(stream));\n}\n"),
    "Power Query": ("queries/Sales.pq", "let\n    Source = Sql.Database(\"dw\", \"sales\"),\n    Filtered = Table.SelectRows(Source, each [Date] >= #date(2023, 1, 1))\nin\n    Filtered\n"),
    "BigQuery": ("sql/cohorts.sql", "SELECT DATE_TRUNC(first_paid, MONTH) AS cohort,\n       DATE_DIFF(activity_month, first_paid, MONTH) AS month_n,\n       COUNT(DISTINCT customer_id) AS active\nFROM `analytics.subscriptions`\nGROUP BY 1, 2\n"),
    "Looker Studio": ("docs/looker_fields.md", "Calculated field `Retention %`: SUM(active) / MAX(cohort_size)\n"),
    "Tableau Prep": ("flows/attrition.tfl.md", "Flow: HRIS export -> clean tenure -> join exit survey -> output attrition.hyper\n"),
    "VBA": ("macros/Scenario.bas", "Sub SwitchScenario(name As String)\n    Sheets(\"Inputs\").Range(\"Scenario\").Value = name\n    Application.Calculate\nEnd Sub\n"),
    "Power Pivot": ("model/measures.dax", "Runway Months := DIVIDE([Cash Balance], [Monthly Burn])\n"),
    "SciPy": ("analysis/test.py", "from scipy import stats\n\nt, p = stats.ttest_ind(control_rev, variant_rev, equal_var=False)\n"),
    "statsmodels": ("analysis/model.py", "import statsmodels.formula.api as smf\n\nfit = smf.ols(\"log_bookings ~ log_price + C(season) + C(room_type)\", data=df).fit()\nprint(fit.summary())\n"),
    "Airbyte": ("ingestion/connections.yaml", "sources:\n  - facebook_marketing\n  - google_ads\n  - tiktok_marketing\ndestination: bigquery\n"),
    "NLTK": ("themes.py", "from nltk.tokenize import word_tokenize\nfrom nltk.corpus import stopwords\n\ntokens = [w for w in word_tokenize(text.lower()) if w not in stopwords.words(\"english\")]\n"),
    "SSIS": ("etl/README_ssis.md", "SSIS package LoadPlantKPIs.dtsx runs nightly and loads OTIF facts into dw.fact_delivery.\n"),
    "GA4 BigQuery Export": ("sql/ga4_paths.sql", "SELECT user_pseudo_id, ARRAY_AGG(traffic_source.source ORDER BY event_timestamp) AS path\nFROM `ga4_export.events_*`\nWHERE event_name = 'purchase'\nGROUP BY 1\n"),
    "Requests": ("fetch_prices.py", "import requests\n\nresp = requests.get(PARTNER_API, params={\"sku\": sku}, headers={\"Authorization\": f\"Bearer {TOKEN}\"}, timeout=20)\nresp.raise_for_status()\n"),
    "ggplot2": ("report/plots.R", "library(ggplot2)\nggplot(readm, aes(x = ward, y = rate)) + geom_col() + coord_flip()\n"),
    "Survival Analysis": ("report/survival.R", "library(survival)\nfit <- coxph(Surv(days, readmitted) ~ age + comorbidity + ward, data = patients)\n"),
    "RapidFuzz": ("dedupe.py", "from rapidfuzz import fuzz, process\n\nmatches = process.extract(name, candidates, scorer=fuzz.token_sort_ratio, score_cutoff=92)\n"),
}

MAIN_FILES = {
    "Python": ("main.py", "def main() -> None:\n    config = load_config()\n    run(config)\n\n\nif __name__ == \"__main__\":\n    main()\n"),
    "JavaScript": ("src/index.js", "import { start } from './app';\n\nstart({ port: process.env.PORT || 3000 });\n"),
    "TypeScript": ("src/index.ts", "import { createApp } from './app';\n\ncreateApp().listen(Number(process.env.PORT ?? 3000));\n"),
    "PHP": ("index.php", "<?php\nrequire __DIR__ . '/vendor/autoload.php';\n$app = require_once __DIR__ . '/bootstrap/app.php';\n"),
    "R": ("report/run.R", "source('report/load.R')\nsource('report/survival.R')\n"),
    "SQL": ("sql/model.sql", "CREATE VIEW fact_sales AS SELECT order_id, territory_id, amount FROM staging.orders;\n"),
}


def opening(style: str, job_title: str, project_title: str | None, rng: random.Random) -> str:
    if style == "generic":
        return rng.choice([
            "Dear Sir/Madam, I am very passionate about this field and I have read your job description carefully.",
            "Hello, I am an expert with many years of experience and I can do this job perfectly.",
            "Hi there! I am the best fit for your project and I am available to start immediately.",
        ])
    if style == "question":
        return f"Before anything else: for \"{job_title}\", is the main goal speed or accuracy? It changes how I would start."
    if project_title:
        return f"Your post about \"{job_title}\" reads a lot like a problem I solved in my {project_title.lower()} project."
    return f"I read your post about \"{job_title}\" and I can see what you need."


def proposal_body(persona: dict, proj: tuple | None, job_title: str, price: float, length: str, close_q: bool,
                  rng: random.Random) -> str:
    parts = []
    if proj:
        _, title, industry, year, skills, _, problem, approach, outcome, _ = proj
        parts.append(f"In {year}, for a {industry} client, {problem}. I {approach}; {outcome}.")
        parts.append(f"I would use the same toolkit here ({', '.join(skills[:3])}).")
    else:
        parts.append(f"I have worked with {', '.join(rng.sample(persona['all_skills'], 3))} on several projects.")
    if length == "long":
        parts.append("My process is discovery, a written plan, weekly demos, documentation and a handover call. "
                     "I am available across time zones, communicate daily, and I care about quality, deadlines and "
                     "long-term relationships with every client I work with.")
    parts.append(f"My proposed price is ${price:,.0f}.")
    if close_q:
        parts.append("Could you share a sample of the data or the current codebase so I can confirm the plan?")
    return " ".join(parts)


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def write_cv_pdf(path: Path, key: str, p: dict, projects: list[tuple]) -> None:
    from reportlab import rl_config
    from reportlab.lib.pagesizes import A4

    rl_config.invariant = 1  # byte-identical PDFs across runs
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4, title=f"CV - {p['name']} (synthetic)")
    flow = [Paragraph(f"{p['name']} <font size=8>(synthetic persona)</font>", st["Title"]),
            Paragraph(f"{p['headline']} &middot; {p['city']} &middot; {p['years']} years", st["Normal"]),
            Spacer(1, 8), Paragraph("Summary", st["Heading2"]),
            Paragraph(f"{p['headline']}. I take projects from scoping to handover and write up what I measured. "
                      f"Typical engagements are {projects[0][2]}, {projects[1][2]} and {projects[2][2]} clients.", st["Normal"]),
            Paragraph("Core skills", st["Heading2"]),
            Paragraph(", ".join(p["cv_skills"]), st["Normal"]),
            Paragraph("Selected experience", st["Heading2"])]
    for proj in sorted(projects, key=lambda x: -x[3])[:12]:
        flow.append(Paragraph(f"<b>{proj[1]}</b> ({proj[2]}, {proj[3]}): {proj[8]}.", st["Normal"]))
    flow += [Paragraph("Education", st["Heading2"]), Paragraph(p["education"], st["Normal"])]
    doc.build(flow)


def case_study(proj: tuple, style: int, client: str) -> str:
    pid, title, industry, year, skills, _implied, problem, approach, outcome, has_repo = proj
    front = (f"---\nproject: {pid}\ntitle: {title}\nclient_industry: {industry}\ndate: {year}-0{(len(pid) % 9) + 1}-15\n"
             f"skills: [{', '.join(skills)}]\nsynthetic: true\n---\n\n")
    repo_line = f"\nCode: see the `{pid}` repository.\n" if has_repo else ""
    if style == 0:  # narrative
        return front + (f"# {title}\n\n**Client:** {client} ({industry}, synthetic)\n\n"
                        f"## Situation\n{problem[0].upper() + problem[1:]}.\n\n## What I did\nI {approach}.\n\n"
                        f"## Result\n{outcome[0].upper() + outcome[1:]}.\n\n## Stack\n{', '.join(skills)}.\n{repo_line}")
    if style == 1:  # challenge / solution / result bullets, skills not repeated in prose
        return front + (f"# {title} ({year})\n\n- **Challenge:** {problem}.\n- **Solution:** {approach}.\n"
                        f"- **Result:** {outcome}.\n- **Client sector:** {industry}.\n{repo_line}")
    # short blurb, deliberately vague about tools (skills only in front matter)
    return front + (f"# {title}\n\nA {industry} client came to me because {problem}. "
                    f"Over a few weeks we changed that: {outcome}.\n{repo_line}")


def readme(proj: tuple, files: list[str]) -> str:
    pid, title, industry, year, skills, implied, problem, approach, outcome, _ = proj
    techs = skills + implied
    return (f"# {pid}\n\n{title}. Built {year}. (Synthetic portfolio repository.)\n\n"
            f"## Architecture\n{approach[0].upper() + approach[1:]}.\n\n"
            f"## Tech\n" + "\n".join(f"- {t}" for t in techs if t in SNIPPETS or t in MAIN_FILES or t in skills) +
            "\n\n## Layout\n" + "\n".join(f"- `{f}`" for f in files) +
            "\n\n## Running\n```bash\ncp .env.example .env\nmake run\n```\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=794)
    ap.add_argument("--out", default="data/personas")
    ap.add_argument("--posts", default="data/market/job_posts.jsonl")
    ap.add_argument("--proposals", type=int, default=150)
    a = ap.parse_args()
    out = Path(a.out)

    posts = [json.loads(line) for line in open(a.posts)]
    for key, p in PERSONAS.items():
        prng = random.Random(f"{a.seed}-{key}")
        root = out / key
        (root / "case_studies").mkdir(parents=True, exist_ok=True)
        (root / "repos").mkdir(exist_ok=True)
        (root / "_gold").mkdir(exist_ok=True)
        projects = p["projects"]
        explicit = sorted({s for pr in projects for s in pr[4]})
        generic = {"ai_ml": ["Artificial Intelligence", "Data Science", "Neural Network", "AI Development", "API"],
                   "fullstack": ["Web Development", "HTML", "CSS", "Web Application", "API", "API Integration", "MySQL"],
                   "data_analyst": ["Dashboard", "Business Intelligence", "Data Mining", "Data Modeling"]}[key]
        p["cv_skills"] = list(dict.fromkeys(explicit[:28] + generic))
        p["all_skills"] = list(dict.fromkeys(explicit + generic))

        profile = {k: p[k] for k in ("name", "headline", "city", "hourly_rate", "hourly_floor", "min_project",
                                     "years", "education", "cv_skills")}
        profile.update(persona=key, synthetic=True, weekly_connects_budget=80)
        (root / "profile.json").write_text(json.dumps(profile, indent=2))
        write_cv_pdf(root / "cv.pdf", key, p, projects)

        clients = {}
        for i, proj in enumerate(projects):
            client = f"{prng.choice(CLIENT_NAMES)} {proj[2].split()[0].title()} Co."
            clients[proj[0]] = client
            (root / "case_studies" / f"{proj[0]}.md").write_text(case_study(proj, i % 3, client))
            if proj[9]:
                rdir = root / "repos" / proj[0]
                files = []
                lang = "TypeScript" if any(s in proj[4] for s in ("Next.js", "TypeScript")) else (
                    "JavaScript" if any(s in proj[4] for s in ("React", "Node.js", "Vue.js")) else (
                        "PHP" if "PHP" in proj[4] else ("R" if "R" in proj[4] else (
                            "SQL" if "SQL" in proj[4] and "Python" not in proj[4] else "Python"))))
                fname, body = MAIN_FILES[lang]
                files.append(fname)
                for lib in proj[5] + [s for s in proj[4] if s in SNIPPETS]:
                    if lib in SNIPPETS:
                        fn, code = SNIPPETS[lib]
                        files.append(fn)
                        (rdir / fn).parent.mkdir(parents=True, exist_ok=True)
                        (rdir / fn).write_text(code)
                (rdir / fname).parent.mkdir(parents=True, exist_ok=True)
                (rdir / fname).write_text(body)
                (rdir / "README.md").write_text(readme(proj, files))

        # Reviews: 12 per persona, mostly positive, tied to a project, sometimes vague.
        with (root / "reviews.jsonl").open("w") as f:
            for i, proj in enumerate(prng.sample(projects, 12)):
                rating = prng.choice([5, 5, 5, 4.8, 4.5, 4])
                vague = i % 4 == 3
                text = ("Great to work with, communicative and on time. Would hire again." if vague else
                        f"{p['name'].split()[0]} took on our {proj[1].lower()} and {proj[8]}. "
                        f"Clear updates every week and solid documentation.")
                f.write(json.dumps({
                    "review_id": f"rv-{key[:2]}-{i:02d}", "project": proj[0], "client": clients[proj[0]],
                    "client_industry": proj[2], "reviewer_role": prng.choice(REVIEWER_ROLES), "rating": rating,
                    "date": f"{proj[3]}-{prng.randint(1, 12):02d}-{prng.randint(1, 28):02d}", "text": text,
                    "synthetic": True}) + "\n")

        # Past proposals with outcomes, written for REAL historical job titles of this domain.
        pool = [x for x in posts if x["domain"] == key and int(x["source_id"], 16) % 10 >= 7]
        prng.shuffle(pool)
        mkt = {"ai_ml": 700, "fullstack": 600, "data_analyst": 300}[key]
        stats = {"won": 0, "replied": 0, "ignored": 0}
        with (root / "proposals.jsonl").open("w") as f:
            for i, job in enumerate(pool[: a.proposals]):
                jskills = set(job["skills"])
                ranked = sorted(projects, key=lambda pr: -len(jskills & set(pr[4] + pr[5])))
                relevant = ranked[0] if jskills & set(ranked[0][4] + ranked[0][5]) else None
                style = prng.choices(["specific", "generic", "question"], [0.45, 0.35, 0.20])[0]
                cite = prng.random() < 0.6
                proj = (relevant if prng.random() < 0.75 else prng.choice(projects)) if cite else None
                band = job["budget"] or mkt
                pos = prng.choices(["below", "in", "above"], [0.2, 0.55, 0.25])[0]
                price = band * {"below": 0.6, "in": 1.0, "above": 1.7}[pos]
                length = prng.choice(["short", "medium", "long"])
                close_q = prng.random() < 0.4
                rel = proj is not None and proj is relevant
                # Hidden outcome model of the synthetic market (same family GigSim clients use).
                logit = (-1.3 + 1.3 * (style == "specific") - 1.0 * (style == "generic") + 0.6 * (style == "question")
                         + 1.2 * rel + 0.5 * close_q + 0.4 * (pos == "in") - 0.7 * (pos == "above") - 0.5 * (length == "long"))
                replied = prng.random() < sigmoid(logit)
                won = replied and prng.random() < (0.30 + 0.25 * rel)
                outcome = "won" if won else ("replied" if replied else "ignored")
                stats[outcome] += 1
                text = opening(style, job["title"], proj[1] if proj else None, prng) + "\n\n" + \
                    proposal_body(p, proj, job["title"], price, length, close_q, prng)
                f.write(json.dumps({
                    "proposal_id": f"pp-{key[:2]}-{i:03d}", "job_title": job["title"], "job_category": job["category"],
                    "job_skills": job["skills"], "cited_project": proj[0] if proj else None, "price": round(price, 2),
                    "date": f"202{prng.randint(3, 5)}-{prng.randint(1, 12):02d}-{prng.randint(1, 28):02d}",
                    "text": text, "outcome": outcome, "synthetic": True,
                    "_features": {"opening": style, "relevant_evidence": rel, "price_position": pos,
                                  "length": length, "closing_question": close_q},
                }) + "\n")
        gold = {pr[0]: {"title": pr[1], "industry": pr[2], "year": pr[3], "skills": pr[4], "implied": pr[5],
                        "has_repo": pr[9], "client": clients[pr[0]]} for pr in projects}
        (root / "_gold" / "projects.json").write_text(json.dumps(gold, indent=2))
        print(key, len(projects), "projects", stats)


if __name__ == "__main__":
    main()
