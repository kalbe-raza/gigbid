"""Loaders: turn a portfolio folder into `Document`s with metadata on every item.

Supported inputs (the persona corpora and anything a user uploads in the UI):
  cv.pdf / *.pdf          -> type "cv"          (parsed with pypdf)
  case_studies/*.md       -> type "case_study"  (YAML front matter carries project/skills/date/industry)
  repos/<name>/README.md  -> type "readme"      (repo files can also arrive through the filesystem MCP server)
  repos/<name>/<code>     -> type "code"
  reviews.jsonl           -> type "review"
  proposals.jsonl         -> type "proposal"    (keeps the outcome: won / replied / ignored)

Skills on each document are DERIVED here (front matter, README tech lists, code imports, vocabulary
mentions). Ground-truth skill lists under _gold/ are never read by ingestion.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from rag.skills import canon, mentions

CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".php", ".r", ".R", ".sql", ".yml", ".yaml", ".json", ".prisma",
            ".graphql", ".tf", ".conf", ".xml", ".bas", ".dax", ".pq", ".md", ""}

# import / config signatures -> skill they prove (implied skills live here)
IMPORT_SKILLS = {
    r"\bfrom celery\b|\bimport celery\b": "Celery", r"\bimport redis\b|createClient\(|redis://": "Redis",
    r"\bpinecone\b": "Pinecone", r"\bimport mlflow\b": "MLflow", r"\bimport shap\b": "SHAP",
    r"pytesseract": "Tesseract", r"\bimport cv2\b": "OpenCV", r"^FROM \w+": "Docker",
    r"\bimport lightgbm\b": "LightGBM", r"from airflow": "Airflow", r"\bimport wandb\b": "Weights & Biases",
    r"\bimport faiss\b": "FAISS", r"from fastapi": "FastAPI", r"torch\.onnx": "ONNX", r"\bimport pytest\b": "pytest",
    r"from pydantic": "Pydantic", r"IsolationForest": "Isolation Forest", r"\bimport psycopg": "PostgreSQL",
    r"twilio": "Twilio", r"runs-on: ubuntu": "GitHub Actions", r"sagemaker": "SageMaker",
    r"GradientTape": "Grad-CAM", r"\bimport optuna\b": "Optuna", r"<View>": "Label Studio",
    r"googleapiclient": "Google Calendar API", r"^model \w+ \{": "Prisma", r"stripe": "Stripe",
    r"\bboto3\b": "AWS S3", r"django\.contrib\.gis": "PostGIS", r"mapbox": "Mapbox",
    r"\.subscribe\(": "Redis Pub/Sub", r"^type \w+ \{|type Query": "GraphQL", r"@shopify/polaris": "Polaris",
    r"\"framework\": \"nextjs\"": "Vercel", r"\bimport pika\b": "RabbitMQ", r"apiVersion: apps/v1": "Kubernetes",
    r"@tanstack/react-query": "React Query", r"stripe\.transfers": "Stripe Connect", r"jsonwebtoken": "JWT",
    r"google\.maps": "Google Maps API", r"@testing-library": "Testing Library", r"\btest\(": "Jest",
    r"openid-client": "OpenID Connect", r"proxy_pass": "Nginx", r"^resource \"aws_": "Terraform",
    r"stream: true": "Streaming Responses", r"Sql\.Database": "Power Query", r"`analytics\.|ga4_export": "BigQuery",
    r"\bfrom scipy\b": "SciPy", r"statsmodels": "statsmodels", r"destination: bigquery": "Airbyte",
    r"\bfrom nltk\b": "NLTK", r"SSIS|\.dtsx": "SSIS", r"ga4_export": "GA4 BigQuery Export",
    r"\bimport requests\b": "Requests", r"library\(ggplot2\)": "ggplot2", r"coxph|Surv\(": "Survival Analysis",
    r"rapidfuzz": "RapidFuzz", r"Sub \w+\(": "VBA", r":= DIVIDE": "Power Pivot", r"Looker|Calculated field": "Looker Studio",
    r"\bimport torch\b|torch\.": "PyTorch", r"tensorflow|tf\.keras": "TensorFlow", r"from sklearn": "scikit-learn",
    r"\bimport pandas\b|pd\.": "Pandas", r"from django": "Django", r"from 'react'|from \"react\"|useQuery|jsx": "React",
    r"\.tsx?$": "TypeScript",
}


@dataclass
class Document:
    doc_id: str
    corpus: str
    type: str
    text: str
    title: str = ""
    project: str | None = None
    skills: list[str] = field(default_factory=list)
    date: str | None = None
    client_industry: str | None = None
    outcome: str | None = None
    source: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _vocab() -> set[str]:
    from rag.vocab import VOCAB

    return VOCAB


def derive_skills(text: str, path: str = "") -> list[str]:
    found = {canon(s) for s in mentions(text, _vocab())}
    for pat, skill in IMPORT_SKILLS.items():
        if re.search(pat, text, re.M) or (pat.startswith(r"\.tsx") and re.search(pat, path)):
            found.add(canon(skill))
    return sorted(found)


def _front_matter(md: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", md, re.S)
    if not m:
        return {}, md
    raw = re.sub(r"skills: \[(.*?)\]", lambda x: "skills: [" + ", ".join(
        json.dumps(s.strip()) for s in x.group(1).split(",")) + "]", m.group(1))
    try:
        return yaml.safe_load(raw) or {}, m.group(2)
    except yaml.YAMLError:
        return {}, m.group(2)


def load_pdf(path: Path, corpus: str) -> Document:
    from pypdf import PdfReader

    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    return Document(doc_id=f"cv:{corpus}" if path.name == "cv.pdf" else f"pdf:{path.stem}", corpus=corpus,
                    type="cv", title=path.stem, text=text, skills=derive_skills(text), source=str(path))


def load_case_study(path: Path, corpus: str) -> Document:
    fm, body = _front_matter(path.read_text())
    pid = fm.get("project") or path.stem
    skills = sorted({canon(s) for s in fm.get("skills", [])} | set(derive_skills(body)))
    return Document(doc_id=f"case:{pid}", corpus=corpus, type="case_study", text=body.strip(),
                    title=fm.get("title", pid), project=pid, skills=skills, date=str(fm.get("date") or ""),
                    client_industry=fm.get("client_industry"), source=str(path))


def repo_documents(repo: str, files: dict[str, str], corpus: str, source: str) -> list[Document]:
    """Turn {relative_path: content} for one repository into a README doc plus one doc per code file.

    README and code share doc_id `repo:<name>` at evaluation time (see rag/variants/base.py)."""
    docs = []
    readme = files.get("README.md", "")
    date = None
    m = re.search(r"Built (\d{4})", readme)
    if m:
        date = m.group(1)
    if readme:
        docs.append(Document(doc_id=f"repo:{repo}", corpus=corpus, type="readme", text=readme, title=f"{repo} README",
                             project=repo, skills=derive_skills(readme), date=date, source=f"{source}/README.md"))
    for rel, content in sorted(files.items()):
        if rel == "README.md":
            continue
        docs.append(Document(doc_id=f"repo:{repo}#{rel}", corpus=corpus, type="code", text=f"# file: {rel}\n{content}",
                             title=f"{repo}/{rel}", project=repo, skills=derive_skills(content, rel), date=date,
                             source=f"{source}/{rel}"))
    return docs


def load_repo_dir(path: Path, corpus: str) -> list[Document]:
    files = {}
    for f in sorted(path.rglob("*")):
        if f.is_file() and (f.suffix in CODE_EXT or f.name == "Dockerfile") and f.stat().st_size < 60_000:
            files[str(f.relative_to(path))] = f.read_text(errors="ignore")
    return repo_documents(path.name, files, corpus, str(path))


def load_jsonl(path: Path, corpus: str, kind: str) -> list[Document]:
    docs = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if kind == "review":
            docs.append(Document(doc_id=f"review:{r['review_id']}", corpus=corpus, type="review", text=r["text"],
                                 title=f"Review by {r['reviewer_role']}, {r['client']}", project=r.get("project"),
                                 skills=derive_skills(r["text"]), date=r.get("date"),
                                 client_industry=r.get("client_industry"), source=str(path),
                                 extra={"rating": r.get("rating")}))
        else:
            docs.append(Document(doc_id=f"proposal:{r['proposal_id']}", corpus=corpus, type="proposal", text=r["text"],
                                 title=r["job_title"], project=r.get("cited_project"),
                                 skills=sorted({canon(s) for s in r.get("job_skills", [])}), date=r.get("date"),
                                 outcome=r["outcome"], source=str(path),
                                 extra={"job_category": r.get("job_category"), "price": r.get("price")}))
    return docs


def load_portfolio(root: Path, corpus: str, repo_loader=None) -> list[Document]:
    """Load everything under a persona/user portfolio folder. `repo_loader(path)` may replace local reads
    with the third-party MCP path (rag/ingest/repo_mcp.py)."""
    docs: list[Document] = []
    for pdf in sorted(root.glob("*.pdf")):
        docs.append(load_pdf(pdf, corpus))
    for md in sorted((root / "case_studies").glob("*.md")):
        docs.append(load_case_study(md, corpus))
    for repo in sorted(p for p in (root / "repos").glob("*") if p.is_dir()):
        docs.extend(repo_loader(repo, corpus) if repo_loader else load_repo_dir(repo, corpus))
    if (root / "reviews.jsonl").exists():
        docs.extend(load_jsonl(root / "reviews.jsonl", corpus, "review"))
    if (root / "proposals.jsonl").exists():
        docs.extend(load_jsonl(root / "proposals.jsonl", corpus, "proposal"))
    return docs
