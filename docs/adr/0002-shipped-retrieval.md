# ADR 0002: Shipped retrieval = requirement decomposition + hybrid BM25/dense (no reranker, no outcome prior)

**Status:** accepted · **Evidence:** `evals/results/shipped_choice.json`, `evals/results/rag_variants.json`

## Context
The PDF requires the deployed RAG design to be chosen by evaluation (R6). We measured V0, V1 (decomposition), V2
(hybrid), V3 (cross-encoder rerank), V4 (outcome-aware), V8 (corrective loop) and combinations on every dev job with
gold evidence (all three personas), with Recall@5, MRR and nDCG@10.

## Decision
Ship `decomposed + hybrid` on structure-aware chunks: highest Recall@5 and nDCG@10 overall and on jobs with four or more
requirements, which is what the Writer needs (coverage of every requirement).

## Consequences
- The MiniLM cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`) reduced Recall@5 on this corpus and multiplied latency;
  it was trained on web search passages, not on matching job requirements to portfolio code and case studies.
- The outcome prior helped MRR with dense retrieval but lowered Recall@5/nDCG; outcome information is still used where
  it helps the reply rate: as phrasing exemplars and as promoted outcome memory for the Writer.
- Dense-only decomposition has the best MRR (first hit), so it remains the fallback if BM25 is unavailable.
- Revisit when the corpus grows (user uploads) or with a reranker fine-tuned on requirement/evidence pairs.
