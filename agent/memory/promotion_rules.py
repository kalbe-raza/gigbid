"""Explicit promotion rules (R7). Nothing reaches long-term memory except through these functions.

STYLE memory (learned from the user's edits to drafts)
  observe:  an approved edit is diffed into abstract PATTERNS (never raw text): cut_first_sentence, no_greeting,
            shorter, end_with_question, avoid_word:<w> (words the user deleted, from a closed list of fluff words
            or any word deleted in 3+ edits)
  promote:  a pattern becomes a rule only after it appears in >= memory.style.min_occurrences edits on
            >= memory.style.min_distinct_jobs different jobs (config). One-off edits never become rules.

OUTCOME memory (what won or got replies, per job category)
  observe:  marketplace events (replied / interview / hired / ignored) for proposals GigBid sent, with the
            proposal's features (opening style, evidence citations, price position, length)
  promote:  an insight "in <category>, <feature> got replies X% vs Y% base" only when the feature bucket has
            >= memory.outcome.min_samples proposals AND beats the category base rate by >= min_rate_delta.
"""

from __future__ import annotations

import re
from collections import Counter

from agent.settings import settings

FLUFF = {"passionate", "expert", "perfectly", "guarantee", "guaranteed", "world-class", "dedicated", "hardworking",
         "best", "amazing", "excellent", "highly", "skilled", "seasoned", "rockstar", "ninja", "synergy", "leverage",
         "delighted", "thrilled", "hope", "dear", "sir", "madam", "kindly", "really", "very"}
GREETING = re.compile(r"^\s*(hi|hello|hey|dear|greetings|good (morning|afternoon|evening))\b", re.I)
STOP = set("the a an and or of to in on for with at by from my our your this that it is was be as i you we they "
           "he she will would can could have has had do did not no".split())

STYLE_TEXT = {
    "cut_first_sentence": "Cut the first sentence: start directly with the client's problem.",
    "no_greeting": "Do not open with a greeting (no 'Hi', 'Hello', 'Dear').",
    "shorter": "Keep the cover letter short - aim for about {avg_words} words.",
    "end_with_question": "End with one specific question for the client.",
}


def _first_sentence(t: str) -> str:
    return re.split(r"(?<=[.!?])\s+", t.strip(), maxsplit=1)[0].strip()


def _words(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z'-]+", re.sub(r"\[[^\]]+\]", "", t.lower()))]


def edit_patterns(original: str, edited: str) -> dict[str, dict]:
    """Abstract patterns from one approved edit. Returns {pattern_id: {"detail": ...}}."""
    pats: dict[str, dict] = {}
    if not original.strip() or not edited.strip() or original.strip() == edited.strip():
        return pats
    o1 = _first_sentence(original)
    if o1 and o1 not in edited and _first_sentence(edited) != o1:
        pats["cut_first_sentence"] = {}
    if GREETING.search(original) and not GREETING.search(edited):
        pats["no_greeting"] = {}
    ow, ew = _words(original), _words(edited)
    if ow and len(ew) <= 0.8 * len(ow):
        pats["shorter"] = {"words": len(ew)}
    if edited.rstrip().endswith("?") and not original.rstrip().endswith("?"):
        pats["end_with_question"] = {}
    removed = Counter(ow) - Counter(ew)
    for w in removed:
        if w in STOP or len(w) < 4:
            continue
        if w in FLUFF or w not in ew:
            pats[f"avoid_word:{w}"] = {}
    return pats


def style_rule_text(pattern: str, stats: dict) -> str:
    if pattern.startswith("avoid_word:"):
        return f"Never use the word '{pattern.split(':', 1)[1]}'."
    tmpl = STYLE_TEXT.get(pattern, pattern)
    words = stats.get("words") or []
    return tmpl.replace("{avg_words}", str(int(sum(words) / len(words)) if words else 150))


def should_promote_style(candidate: dict) -> bool:
    cfg = settings()["memory"]["style"]
    pat = candidate["pattern"]
    jobs = set(candidate.get("jobs", []))
    needed = cfg["min_occurrences"]
    if pat.startswith("avoid_word:") and pat.split(":", 1)[1] not in FLUFF:
        needed = max(needed, 3)  # ordinary words need at least 3 independent deletions
    return candidate.get("count", 0) >= needed and len(jobs) >= cfg["min_distinct_jobs"]


def proposal_features(text: str, price_ratio: float | None = None) -> dict:
    words = len(_words(text))
    opening = "generic" if re.search(r"^(dear|hello,? i am|hi,? i am|i am (very )?passionate|i am an expert)", text.strip(),
                                     re.I) else "specific"
    cites = len(set(re.findall(r"\[(?:case|repo|review|cv):[^\]]+\]", text)))
    return {
        "opening": opening,
        "evidence": "2+ citations" if cites >= 2 else ("1 citation" if cites == 1 else "no citations"),
        "length": "short (<150 words)" if words < 150 else ("medium" if words <= 220 else "long (>220 words)"),
        "price": "unknown" if price_ratio is None else ("at/below client budget" if price_ratio <= 1.0 else "above budget"),
    }


def outcome_insights(stats: dict, category: str) -> list[dict]:
    """stats = {"n": int, "replied": int, "buckets": {"evidence=2+ citations": {"n":..,"replied":..}, ...}}"""
    cfg = settings()["memory"]["outcome"]
    n, rep = stats.get("n", 0), stats.get("replied", 0)
    if n < cfg["min_samples"]:
        return []
    base = rep / n
    out = []
    for bucket, b in stats.get("buckets", {}).items():
        if b["n"] >= cfg["min_samples"]:
            rate = b["replied"] / b["n"]
            if rate - base >= cfg["min_rate_delta"]:
                feat, val = bucket.split("=", 1)
                out.append({"key": f"{category}:{bucket}", "category": category, "feature": feat, "value": val,
                            "rate": round(rate, 3), "base_rate": round(base, 3), "n": b["n"],
                            "insight": f"In {category}, proposals with {feat} = {val} got replies {rate:.0%} of the time "
                                       f"vs {base:.0%} overall (n={b['n']})."})
    return out
