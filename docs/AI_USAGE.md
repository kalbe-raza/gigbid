# AI usage disclosure

**Tool:** Claude Code (Anthropic, model Claude Opus 5.5) was used as a coding assistant throughout this repository:
data pipeline scripts, GigSim, MCP servers, LangGraph nodes and graphs, API, worker, web UI, tests, eval runners and
the first drafts of these docs. Commits it co-wrote carry a `Co-Authored-By: Claude` trailer.

**What I (the student) remain responsible for:** every design decision, every number (all measured by the runners in
`evals/runners/` and written to `evals/results/`), the judge-calibration labels (`evals/datasets/judge_labels.csv`,
labelled by me), the demo video, the write-up and the viva.

**Incorrect suggestions identified and corrected (examples from the commit history):**
1. The first claim verifier checked claims only against 400-character evidence snippets, so true claims (e.g. JavaScript,
   listed in the CV) were "unsupported" and removed. Fixed by fetching evidence per claim through the Portfolio MCP server.
2. The Compliance Guard pasted the policy model's *instruction* ("delete or provide evidence…") into the cover letter
   as if it were a fix. Fixed: only "delete" or a proper replacement sentence is applied; anything else is shown to the
   human.
3. The verifier's rewrite replaced "Shopify" with "WooCommerce" (grounded but wrong for the job); the Critic caught it,
   and rewrites must now keep a citation.
4. Data generation used Python `set` iteration order, so "deterministic" seeds produced different files per run
   (hash randomisation). Fixed with sorted iteration; verified byte-identical across `PYTHONHASHSEED`.
5. The cross-encoder reranker was assumed to help; measurement showed it lowered Recall@5, so it is off in the shipped
   config (ADR 0002).
