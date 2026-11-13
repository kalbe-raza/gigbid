# My agent's reply rate was 0%. The bug was in the simulator I wrote to grade it.

*Syed Kalbe Raza · notes from building GigBid, a multi-agent copilot for freelancers (AI-4016 R&D project). Every
number here comes from a file in the project's `evals/results/` folder.*

For the last two weeks I have been building GigBid, a set of LangGraph agents that read freelance job posts, decide
whether a freelancer should bid, and draft a proposal in which every factual sentence cites the freelancer's own
portfolio. Nothing is ever sent without a human click. To measure whether any of it works, I also built GigSim, a
small marketplace simulator whose clients have hidden preferences (must-have skills, a budget ceiling, a dislike of
generic openings) and an LLM that decides whether to ignore a proposal, reply, or ask for an interview.

I expected the interesting results to be about retrieval or about how many agents you need. The most surprising
result was more basic than that: the first time I ran the full system against calibrated clients, it got
**{{superseded_gigsim121_best_openb:summary/proposals}} proposals out and
{{superseded_gigsim121_best_openb:summary/reply_rate|.0%}} replies**, and the problem was not in the agent at all.

## The number that looked like a finding

Before that run, GigSim's LLM clients replied to every proposal they received, which is obviously unrealistic.
So I calibrated them: the LLM's "reply" was only accepted with a probability taken from a simple rule model of the
client's preferences. After calibration the reply rate dropped to zero. It was tempting to read this as a finding
("the proposals are not good enough") and start tuning the writer prompt.

What stopped me was that the judge, a separate LLM grading the proposals on a rubric, scored the same letters
{{superseded_gigsim121_best_openb:summary/judge_overall_mean|.1f}} out of 10 on average. Good letters and zero replies is a combination worth checking. So instead of changing the
agent, I recomputed the simulator's own features for each ignored proposal: how many of the client's must-have
skills it addressed, the price relative to the budget, whether the opening was generic, how much evidence it cited.

The pattern was immediate. For {{gigsim_feature_audit:zero_must_have_exact_121}} of the {{gigsim_feature_audit:proposals}} proposals, "must-have skills addressed" was **0%**. One client's
must-haves were `microsoft excel` and `financial analysis`; the letter talked about building financial models in
Excel. My feature checked whether the exact phrase "microsoft excel" appeared in the text. It did not, so the
simulator told its LLM client, in so many words, "this proposal addresses 0% of your must-have skills", and the
client ignored it. The agent was being graded on string matching I had written in five minutes.

## The fix, and what it did

GigSim 1.2.2 credits a skill if the phrase appears, and otherwise by the share of its distinctive words that do
(six-letter stems, vendor prefixes such as "Microsoft" ignored), so "Excel" counts for "microsoft excel" and
"financial models" half-counts for "financial analysis". Re-scoring the same seven letters, the number with zero
must-have credit falls from {{gigsim_feature_audit:zero_must_have_exact_121}} to {{gigsim_feature_audit:zero_must_have_stem_122}}, and the two that remain (MySQL; Python and
statistics) are genuine gaps in the letters. I bumped the simulator's version, re-froze its checksum
manifest (CI fails if anything under `gigsim/` changes without that), and wrote the bug into the project's AI usage
disclosure, because the exact-match line was one an assistant wrote and I accepted.

While reading the ignored letters I also saw a real agent weakness: the writer described skills in its own words
("relational databases") instead of the client's ("MySQL"). Writer prompt version 5 asks it to name each listed skill
its evidence supports, in the client's words, and never to name one it cannot prove. With both changes, the same
fixed job set gives **{{gigsim_outcomes_best_openb:summary/reply_rate|.0%}}** replies
({{gigsim_outcomes_best_openb:summary/proposals}} proposals, 95% interval
{{gigsim_outcomes_best_openb:summary/reply_ci95/0|.0%}}–{{gigsim_outcomes_best_openb:summary/reply_ci95/1|.0%}}).

I cannot cleanly say how much of the jump came from the simulator fix and how much from the prompt, because I
changed both before re-running. That is the honest cost of fixing two things at once, and the main reason I now
re-run an experiment after every single change, even when it feels slow.

## The second surprise: better retrieval did not move replies

The research question I actually cared about was retrieval. A naive approach embeds the whole job post and takes
the top five portfolio items. My shipped approach splits the post into requirements, searches for each with both
BM25 and dense vectors, and fuses the results. On {{rag_variants:variants/best/overall/n}} jobs with labelled evidence,
it finds more of the right evidence: Recall@5 goes from {{rag_variants:variants/v0_naive/overall/recall@5|.3f}} to
{{rag_variants:variants/best/overall/recall@5|.3f}}.

End to end, on the same jobs and clients, the naive retrieval got
{{gigsim_outcomes_v0_openb:summary/reply_rate|.0%}} replies and the shipped one
{{gigsim_outcomes_best_openb:summary/reply_rate|.0%}}. With about seven proposals per arm those intervals overlap almost
completely, so the honest reading is "no measurable difference", not "worse". Two other things I tried hurt
retrieval outright: a cross-encoder reranker and an "outcome prior" that boosts evidence from past winning
proposals. Both are switched off in the shipped configuration.

Why does better recall not show up in replies? Because, once the simulator was fixed, the clients respond to what
the writer controls: naming their skills, a price under their ceiling, an opening about *their* problem. Retrieval
decides *which* past project gets cited, which matters for honesty (every claim has to be provable, and the claim
verifier removed the unsupported ones in every run: {{gigsim_outcomes_best_openb:summary/unsupported_claims_per_proposal_before|.2f}}
per proposal before verification, {{gigsim_outcomes_best_openb:summary/unsupported_claims_per_proposal_after|.0f}} after).
It just does not decide whether a client answers. I kept the better retriever anyway, because the human reviewing
the draft relies on the evidence being right, but I stopped expecting it to raise the reply rate.

## Small samples are a design constraint, not a footnote

Everything above ran on free tiers: Groq's hosted open-weight models with 8,000 tokens per minute and 200,000 tokens
per model per day, a CPU-only laptop with 8 GB of RAM. One full outcome run costs about
{{gigsim_outcomes_best_openb:summary/tokens_per_proposal|.0f}} tokens per proposal, so a day of quota buys a few dozen
proposals across every experiment. My A/B test of two writing strategies ended at
{{ab_test:summary/reply_rate_A|.0%}} versus {{ab_test:summary/reply_rate_B|.0%}} on {{ab_test:summary/pairs}} paired jobs,
McNemar p = {{ab_test:summary/mcnemar_exact_p|.2f}}. That is not evidence either strategy is better. It is evidence
that a paired design, a significance test and a pre-registered hypothesis matter more at this scale than at any other.

The quota also found a bug for me. Halfway through the evaluation, the provider tightened the per-request output
limit on one model, and the client's "wait and retry" logic could never succeed. It now reads the limit from the
error and shrinks the request. Separately, two runs of a retrieval experiment that should have been identical
differed in the third decimal place. The cause was a line that took "the first four" skills from a Python `set`,
whose order changes with every process. It had been silently changing which requirements the agent searched for.

## What I would tell myself two weeks ago

1. **Test the test harness first.** A simulator, a judge or a grading script is code with bugs. When a metric moves
   to an extreme, check the grader before you "fix" the system.
2. **Explain a failure from its features.** Recomputing the simulator's own features for each ignored proposal found
   the bug in minutes. Reading the letters again would not have.
3. **Change one thing per run.** I lost the ability to attribute the reply-rate jump because I changed two.
4. **Measure the end metric.** Retrieval metrics improved; replies did not. Without the end-to-end run I would have
   shipped a reranker that made things worse and told a story about it.
5. **Write down the hypothesis before running.** My tool-design hypothesis turned out half wrong (coarse tools did
   not lose argument accuracy); because it was recorded first, I could not quietly rewrite it afterwards.

The project, including the simulator, the evaluation runners and every result file, is at
`github.com/kalbe-raza/gigbid`.
