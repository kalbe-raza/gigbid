# GigBid Portfolio MCP server

A read-only MCP server over a freelancer's portfolio (CV PDF, Markdown case studies, repositories, client reviews, past
proposals with outcomes) with hybrid retrieval. Plug it into Claude Desktop or Cursor and ask "what proves I can do X?".

**Tools:** `search_evidence(requirement, k, filters)` (evidence items with type, source, date, outcome, citable id),
`get_case_study(id)`, `list_skills()`.
**Resources:** `portfolio://case/{id}`, `portfolio://proposal/{id}`, `portfolio://repo/{name}`, `portfolio://cv`.
**Prompts:** answer a screening question from my portfolio · find my strongest evidence for a job post · what is my
portfolio missing.

## Install / run (no clone needed)
```bash
# your portfolio folder: cv.pdf, case_studies/*.md, repos/<name>/..., reviews.jsonl, proposals.jsonl (all optional)
PORTFOLIO_DIR=~/my-portfolio uvx --from "git+https://github.com/kalbe-raza/gigbid" gigbid-portfolio-mcp
```
Claude Desktop (`claude_desktop_config.json`):
```json
{"mcpServers": {"portfolio": {"command": "uvx",
  "args": ["--from", "git+https://github.com/kalbe-raza/gigbid", "gigbid-portfolio-mcp"],
  "env": {"PORTFOLIO_DIR": "/Users/me/my-portfolio"}}}}
```
Try it on a demo persona: `PORTFOLIO_DIR=data/personas/fullstack python -m mcp_servers.portfolio.server`.
Docker: `docker build -f infra/docker/mcp-portfolio.Dockerfile -t gigbid-portfolio . && docker run -p 8101:8101 -e MCP_SERVICE_TOKENS=secret gigbid-portfolio`
(Streamable HTTP at `/mcp`, `Authorization: Bearer secret`).

Case-study front matter: `project`, `title`, `client_industry`, `date`, `skills: [..]`. Embeddings run locally on CPU
(`BAAI/bge-small-en-v1.5`, ~70 MB, downloaded on first use).
