# Change request: GigSim platform policy update 2026-11-02

## What changed
The course did not ship a GigSim build or the text of the 2 November "platform policy update", so this repository's
GigSim carries a **self-defined** update (`gigsim/policy.yaml`, version `2026-11-02`, served at `GET /v1/policy`):

1. **New cost:** proposals to jobs with a fixed budget of at least $1,000, or an hourly ceiling of at least $50, cost
   1.5x the listed Connects (rounded up).
2. **New rule:** cover letters longer than 1,500 characters are rejected at submission (HTTP 422).

## What we had to edit
Exactly one commit, `Change request 2026-11-02: ...` (see `git log --grep "Change request"`):

| File | Lines | Why |
|---|---|---|
| `agent/config.yaml` | +6 / -4 | surcharge rule under `pricing.connects_surcharge_rules`; `proposal.max_cover_letter_chars: 1500` |
| `data/policy_pack/marketplace_policy.md` | +5 | section 8, so the Compliance Guard's policy model knows the rule |
| `tests/unit/test_core.py` | +12 / -1 | new test for the change; one existing pricing expectation now 9 Connects instead of 6 |

**Python source changed: 0 lines.** Elapsed time from starting the edit to all tests passing: about 2 minutes.

## Why it was cheap (and the honest caveat)
- Rules and limits were already read from config: the Pricing Strategist applies `connects_surcharge_rules`, the writer
  prompt is filled with `max_cover_letter_chars`, the Compliance Guard trims to it, and the Marketplace MCP server's
  dry run reports a policy warning when a letter is over the limit.
- The Connects figure the marketplace reports already includes platform surcharges, and Pricing never double-applies
  config rules on top of it, so the dry-run preview the human approves always shows the true cost.
- **Caveat:** the same team wrote GigSim's policy file and GigBid, so this is not a blind test of adaptability. A
  genuinely unexpected rule (for example "no more than 3 proposals per client per week") would need one new rule type
  in the Pricing/decision code, not just config. We estimate ~30 lines for that kind of change.

## Verification
- `pytest tests/unit -k change_request` passes; the full suite passes.
- `GET /v1/policy` on GigSim returns version `2026-11-02`; the approval panel shows the surcharge in the dry run.
