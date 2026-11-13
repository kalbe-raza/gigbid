# ADR 0003: Model routing (open profile)
Open-weight models only, via Groq's hosted free tier (no GPU available), with an OpenRouter free open model as fallback
and Ollama (`qwen3:4b`) for fully local runs. Cheap, frequent nodes use gpt-oss-20b; the Writer uses gpt-oss-120b;
the Critic uses a different family (Qwen 3.8 27B) so it does not share the Writer's blind spots; the Compliance Guard
uses gpt-oss-safeguard-20b (built for policy classification); injection screening uses Llama Prompt Guard 2 (86M).
Two configurations are compared on the same fixed GigSim job set: `open` (Writer gpt-oss-120b, Critic Qwen) and
`open_b` (Writer Qwen, Critic gpt-oss-120b) - results in `evals/results/gigsim_outcomes_best_open*.json`.
Free-tier limits (8k tokens/min, 200k tokens/day per model) bound the size of every LLM experiment; all of them are
cached (`LLM_CACHE=1`) so re-running a report costs nothing.
