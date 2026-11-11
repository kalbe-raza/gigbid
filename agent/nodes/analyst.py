"""Job Analyst: job post -> JobSpec (deliverables, requirements, budget, timeline, screening questions, hidden needs).
Structured marketplace fields (budget, skills, category) are copied, not re-guessed; the LLM adds the rest."""

from __future__ import annotations

import re

from agent.handoffs import AnalystOut, JobSpec, Requirement
from agent.llm import LLMError, chat, load_prompt
from agent.nodes.common import NodeContext, gigbid_node
from agent.safety import wrap_untrusted
from rag.skills import GENERIC_SKILLS, canon_set
from rag.variants.base import heuristic_requirements

QUESTION = re.compile(r"(?:^|\n|\. )([A-Z][^?\n]{10,200}\?)")


def base_spec(job: dict) -> JobSpec:
    budget_type = "hourly" if job.get("is_hourly") else ("fixed" if job.get("budget") else "unknown")
    return JobSpec(
        job_id=job["job_id"], title=job.get("title", ""), skills=job.get("skills", []), category=job.get("category"),
        budget_type=budget_type,
        budget_min=job.get("hourly_low") if job.get("is_hourly") else job.get("budget"),
        budget_max=job.get("hourly_high") if job.get("is_hourly") else job.get("budget"),
    )


def heuristic_spec(job: dict) -> JobSpec:
    """Deterministic fallback when the LLM is unavailable (marked in the UI as 'degraded analysis')."""
    spec = base_spec(job)
    reqs = heuristic_requirements(job)
    specific = {s for s in canon_set(job.get("skills", [])) if s not in GENERIC_SKILLS}
    spec.requirements = [Requirement(text=r, skill=r.removeprefix("experience with ") if r.startswith("experience with ")
                                     else None, priority="must" if r.removeprefix("experience with ") in specific else "nice")
                         for r in reqs]
    spec.summary = job.get("title", "")
    spec.screening_questions = [q.strip() for q in QUESTION.findall(job.get("description", ""))][:3]
    return spec


async def analyse(job: dict) -> tuple[JobSpec, bool]:
    prompt = load_prompt("analyst")
    msg = (f"Title: {job.get('title')}\nCategory: {job.get('category')}\nListed skills: {', '.join(job.get('skills', []))}\n"
           f"Budget: {'hourly ' + str(job.get('hourly_low')) + '-' + str(job.get('hourly_high')) if job.get('is_hourly') else job.get('budget')}\n\n"
           + wrap_untrusted("job_post", job.get("description", ""), 3500))
    try:
        res = await chat("analyst", [{"role": "system", "content": prompt}, {"role": "user", "content": msg}],
                         schema=AnalystOut, max_tokens=900)
    except LLMError:
        return heuristic_spec(job), False
    out: AnalystOut = res.parsed
    spec = base_spec(job)
    spec.summary = out.summary
    spec.deliverables = out.deliverables
    spec.requirements = out.requirements or heuristic_spec(job).requirements
    spec.timeline = out.timeline
    spec.screening_questions = out.screening_questions
    spec.hidden_needs = out.hidden_needs
    return spec, True


@gigbid_node("analyst")
async def job_analyst(state: dict, ctx: NodeContext) -> dict:
    spec, used_llm = await analyse(state["job"])
    out = {"job_spec": spec.model_dump(), "_summary": f"{len(spec.requirements)} requirements, "
           f"{len(spec.screening_questions)} screening questions"}
    if not used_llm:
        out["errors"] = ["analyst: LLM unavailable, heuristic JobSpec used (degraded)"]
    return out
