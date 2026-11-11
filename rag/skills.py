"""Skill normalisation shared by ingestion, the Fit Matcher, gold-label construction and evals.

Upwork skill labels are noisy ("Microsoft Power BI", "Power BI", "ReactJS", "React"). Everything that
compares skills goes through `canon()` so "the same skill" means the same thing everywhere.
"""

from __future__ import annotations

import re
from functools import lru_cache

ALIASES: dict[str, str] = {
    "microsoft power bi": "power bi", "powerbi": "power bi", "power bi desktop": "power bi",
    "amazon web services": "aws", "aws lambda": "aws", "amazon s3": "aws s3",
    "restful api": "rest api", "api development": "rest api", "api": "rest api", "rest": "rest api",
    "api integration": "api integration", "django rest framework": "django rest framework",
    "reactjs": "react", "react.js": "react", "react js": "react", "nodejs": "node.js", "node": "node.js",
    "nextjs": "next.js", "next": "next.js", "vuejs": "vue.js", "vue": "vue.js",
    "postgres": "postgresql", "postgre sql": "postgresql", "mongo": "mongodb",
    "nlp": "natural language processing", "natural language processing": "natural language processing",
    "ml": "machine learning", "machine learning model": "machine learning", "ai model development": "machine learning",
    "ai model training": "machine learning", "artificial neural network": "neural network",
    "ai": "artificial intelligence", "ai development": "artificial intelligence",
    "ai app development": "artificial intelligence", "ai model integration": "ai integration",
    "chatgpt api": "openai api", "gpt-4": "openai api", "gpt api": "openai api", "openai": "openai api",
    "chatgpt": "openai api", "ai chatbot": "chatbot development", "chatbot": "chatbot development",
    "ai bot": "chatbot development", "ai agent development": "ai agents", "llm": "large language models",
    "large language model": "large language models", "rag": "retrieval-augmented generation",
    "excel": "microsoft excel", "ms excel": "microsoft excel", "excel formula": "microsoft excel",
    "google sheets": "google sheets", "google data studio": "looker studio", "looker": "looker studio",
    "data visualisation": "data visualization", "dashboard": "data visualization",
    "time series analysis": "time series forecasting", "forecasting": "time series forecasting",
    "statistical analysis": "statistics", "a/b test": "a/b testing", "ab testing": "a/b testing",
    "etl pipeline": "etl", "data cleansing": "data cleaning", "data scraping": "web scraping",
    "web crawling": "web scraping", "scrapy": "web scraping", "sklearn": "scikit-learn",
    "tensorflow": "tensorflow", "keras": "keras", "pytorch": "pytorch", "hugging face": "hugging face",
    "transformers": "transformers", "full-stack development": "web development", "full stack development": "web development",
    "web application": "web development", "html5": "html", "css 3": "css", "css3": "css",
    "typescript": "typescript", "javascript": "javascript", "js": "javascript",
    "github actions": "ci/cd", "ci/cd": "ci/cd", "mern": "mern stack", "shopify development": "shopify",
    "shopify app development": "shopify", "woocommerce": "woocommerce", "e-commerce development": "ecommerce website development",
    "ecommerce website": "ecommerce website development", "ecommerce development": "ecommerce website development",
    "computer vision": "computer vision", "opencv": "opencv", "object detection": "computer vision",
    "image processing": "computer vision", "image classification": "computer vision", "yolo": "computer vision",
    "ocr": "ocr", "optical character recognition": "ocr", "speech recognition": "speech recognition",
    "whisper": "speech recognition", "sql server": "sql", "microsoft sql server": "sql", "mysql": "mysql",
    "r": "r", "r programming": "r", "rstudio": "r", "spss": "statistics", "ibm spss": "statistics",
    "data analytics": "data analysis", "business intelligence": "business intelligence",
    "financial projection": "financial modeling", "financial model": "financial modeling",
    "angularjs": "angular", "angular js": "angular", ".net framework": ".net", "asp.net": ".net", "asp.net core": ".net",
    "dot net": ".net", "vue.js 3": "vue.js", "wordpress development": "wordpress", "wordpress plugin": "wordpress",
    "ios development": "ios", "android app development": "android", "flutter development": "flutter",
}

# Umbrella skills: they appear on most posts in a domain and say little about fit on their own.
GENERIC_SKILLS = {
    "web development", "rest api", "api integration", "html", "css", "artificial intelligence", "data science",
    "data analysis", "javascript", "python", "sql", "machine learning", "neural network", "business intelligence",
    "data visualization", "data mining",
}

# Skills too generic to prove anything on their own; they never count toward coverage.
STOP_SKILLS = {
    "english", "communications", "website", "writing", "email communication", "data entry",
    "administrative support", "virtual assistance", "list building", "lead generation", "research",
    "problem solving", "project management", "web design", "landing page", "graphic design",
    "microsoft office", "google docs", "customer service", "content writing", "automation",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower().strip(".,;:"))


@lru_cache(maxsize=8192)
def canon(skill: str) -> str:
    s = _norm(skill)
    return ALIASES.get(s, s)


def canon_set(skills) -> set[str]:
    out = set()
    for s in skills or []:
        c = canon(s)
        if c and c not in STOP_SKILLS:
            out.add(c)
    return out


def mentions(text: str, vocab: set[str]) -> set[str]:
    """Skills from `vocab` mentioned in free text (word-boundary match, alias aware)."""
    t = " " + _norm(text) + " "
    found = set()
    for v in vocab:
        if len(v) <= 2:  # "r", "ai": too ambiguous in prose
            continue
        if re.search(r"(?<![\w.])" + re.escape(v) + r"(?![\w])", t):
            found.add(v)
    for alias, target in ALIASES.items():
        if target in vocab and len(alias) > 3 and re.search(r"(?<![\w.])" + re.escape(alias) + r"(?![\w])", t):
            found.add(target)
    return found
