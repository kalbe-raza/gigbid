<!-- generated from EXTENSION.tmpl.md by scripts/build_report.py; edit the template -->
# Signature extension X2: proposal experiments

**Question.** Does the way a proposal is written change whether a client replies, once the evidence, price and model
are held fixed? GigBid runs two writer strategies as an A/B test and reports a significance test, not an anecdote.

## The two strategies
| Arm | Prompt | What it does |
|---|---|---|
| **A, evidence-first** | [`agent/prompts/writer_strategy_A.md`](../agent/prompts/writer_strategy_A.md) | the client's problem in one sentence, two or three cited proofs, the approach, one question |
| **B, short and consultative** | [`agent/prompts/writer_strategy_B.md`](../agent/prompts/writer_strategy_B.md) | at most 110 words: the problem, ONE strongest proof, two clarifying questions, a small paid first milestone |

Both arms go through the same claim verifier, critic, compliance guard and human approval; only the writing strategy
differs.

## How it runs in the product
`experiments.proposal_ab` in [`agent/config.yaml`](../agent/config.yaml) turns it on. Each job gets an arm by a stable
hash of its id (`agent/nodes/writer.py: experiment_arm`), so a re-analysis keeps its arm. The arm is stored with the
draft and the pipeline card, and the outcome (viewed / replied / interview / hired) arrives through the pipeline
sync, so the Analytics screen keeps accumulating real A/B data while the user works. Turning the experiment off, or
promoting the winner to the only strategy, is a config change.

## How it was measured
`MODEL_PROFILE=open_b python -m evals.runners.ab_test --per-persona 4`
([`evals/runners/ab_test.py`](../evals/runners/ab_test.py)). **Paired design:** every job in the fixed set is written
twice, once per arm, and both letters are submitted to GigSim, whose clients decide with an LLM gated by their hidden
preferences. Pairing removes the job-to-job variance (budget, client, difficulty), which dominates at this sample size.
The primary test is **McNemar's exact test** on the discordant pairs (jobs where only one arm got a reply); a
two-proportion z-test is shown for comparison.

## Result
| | Arm A | Arm B |
|---|---|---|
| reply rate | 0.71 (95% CI 0.36–0.92) | 0.71 (95% CI 0.36–0.92) |
| only this arm got a reply | 1 jobs | 1 jobs |
| words per letter | 140 | 118 |
| tokens per letter | 11481 | 12065 |

7 paired jobs · McNemar exact p = **1.000** · two-proportion
z = 0.00 (p = 1.000) · writer model
`groq:qwen/qwen3.8-27b` · source [`evals/results/ab_test.json`](../evals/results/ab_test.json).

**Reading.** No detectable difference: the arms disagreed on 1 +
1 jobs, and McNemar's test needs at least six discordant jobs all favouring one arm before
it can reach p < 0.05 at this size. Strategy B was also longer than designed (118 words against
a limit of 110), so the comparison was closer to "two similar letters" than "short versus long"; the next version
enforces B's length in code. The value of X2 is the machinery: a paired design, a pre-stated test and an Analytics
panel that keeps collecting real outcomes per arm.

## Limits
GigSim's clients are simulated, so this measures what an LLM client with hidden preferences responds to, not a real
market. At this size only a large effect would reach significance; the value of X2 is that the same code keeps
collecting paired evidence in production.
