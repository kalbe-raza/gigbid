"""Resource templates so an MCP host can load full portfolio items by URI."""

from __future__ import annotations

from mcp_servers.portfolio.corpus import documents


def case(corpus: str, case_id: str) -> str:
    d = documents(corpus).get(f"case:{case_id}")
    if not d:
        raise ValueError(f"unknown case study {case_id}")
    return f"# {d.title}\n\n- project: {d.project}\n- industry: {d.client_industry}\n- date: {d.date}\n" \
           f"- skills: {', '.join(d.skills)}\n\n{d.text}"


def proposal(corpus: str, proposal_id: str) -> str:
    d = documents(corpus).get(f"proposal:{proposal_id}")
    if not d:
        raise ValueError(f"unknown proposal {proposal_id}")
    return f"# Proposal for: {d.title}\n\n- outcome: **{d.outcome}**\n- date: {d.date}\n" \
           f"- cited project: {d.project}\n\n{d.text}"


def repo(corpus: str, name: str) -> str:
    docs = documents(corpus)
    readme = docs.get(f"repo:{name}")
    files = sorted(k.split("#", 1)[1] for k in docs if k.startswith(f"repo:{name}#"))
    if not readme and not files:
        raise ValueError(f"unknown repository {name}")
    return (readme.text if readme else f"# {name}\n") + "\n\n## Files indexed\n" + "\n".join(f"- {f}" for f in files)


def cv(corpus: str) -> str:
    d = next((x for x in documents(corpus).values() if x.type == "cv"), None)
    return d.text if d else "No CV uploaded."
