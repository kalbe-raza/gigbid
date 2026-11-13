# ADR 0004: Critic approval threshold (E5 optimisation)
Before: the Critic's own `approve` flag decided, and "partly supported" claims counted as hard grounding failures. In the
baseline trial every draft used all 3 critic rounds: 18.2k tokens and 234 s p95 per proposal
(`evals/results/gigsim_outcomes_baseline_trial.json`), far over the declared budget.
After: approval is computed in code - score ≥ `thresholds.critic_min_score` (6.5) and no hard issue (generic opening,
remaining unsupported claim, platform length). Partly supported claims are soft and shown to the human next to the
claim. Effect measured in `evals/results/gigsim_outcomes_best_openb.json` (critic rounds, tokens, p95) and compared in
`evals/results/budgets.json`.
