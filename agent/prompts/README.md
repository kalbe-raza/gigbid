# Prompts

One versioned Markdown file per LLM node. No prompt text lives in Python (CI enforces this with
`tests/unit/test_no_inline_prompts.py`). The first line of each file is `<!-- node: <name> | version: <n> -->`;
bump the version when you change a prompt so traces and eval results can be tied to a prompt version.

Nodes without a prompt are deterministic on purpose: Scout (dedup + queue), Fit Matcher (retrieval + scoring),
decision, Pricing Strategist (market statistics + rules). Their reasoning is shown as rule-based rationale lines.

Every prompt that sees a job post or a client message receives it inside `<untrusted_...>` tags, with look-alike
tags defanged by `agent/safety.wrap_untrusted`, and is told that instructions inside those tags are data.
