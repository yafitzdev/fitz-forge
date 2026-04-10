# Next Up — Implementation Queue

## 1. Taxonomy Scoring Integration (Tier 2)

**Status:** Sonnet classification works manually (10 plans scored via subagents). Needs to be wired into the scoring pipeline so it runs automatically.

**What exists:**
- `benchmarks/eval_v2_taxonomy.py` — prompt builder + response parser + score formula
- `benchmarks/streaming_taxonomy.json` — architecture (A1-A5) + per-file (E1-E6, R1-R5, S1-S3) taxonomy
- `score_v2_prompt_NN.md` files generated per plan
- Manual results from 10 plans: 3/10 A1 (correct), 4/10 A2 (partial), 3/10 A4 (blocking)

**What needs to happen:**
- Wire Sonnet subagent scoring into `plan_factory` batch flow (or separate command)
- Parse JSON responses via `parse_taxonomy_response()`
- Compute combined score: `deterministic * 0.X + taxonomy * 0.Y` (weights TBD)
- Write combined results to `scores_v2.json`
- Update SCORE_V2_SUMMARY.md with taxonomy columns

**Key finding:** Plan_01 scored 98.9 deterministic but A4 taxonomy (fake streaming). Plan_06 scored 89.7 deterministic but A1 taxonomy (correct architecture). The taxonomy inverts the ranking for 3/10 plans — it's load-bearing.

**Effort:** Small — plumbing, not new logic.

---

## 2. Artifact Generation Black Box — DONE

**Status:** Implemented and tested. `fitz_forge/planning/artifact/`

**Results:**
- 100% success rate on isolated artifact tests (30/30)
- Raw code output eliminates JSON quote mangling entirely
- Surgical rewrite now fires for any file with a reference method
- Validation catches fabrication, parse errors, missing yield, wrong return type
- Retry with specific error feedback (up to 3 attempts)

**Run 91 (first benchmark with black box):**
- Parse failures: 16 → 1
- Fabrications: 8 → 3 (+ post-run fix for dedent recovery in fabrication check)
- 1 perfect 100/100 plan

---

## Remaining Issues (not yet addressed)

1. **V2-F7: Missing required file (1/6 plans)** — synthesis reasoning sometimes doesn't include engine.py in needed_artifacts. Upstream issue in synthesis extraction, not artifact generation.

2. **V2-F6d: Method name mismatch (1/6 plans)** — model picks different names across artifacts (`_synthesizer.stream()` vs `generate_stream()`). Prior signature injection partially helps. Could be improved by having decision resolution commit to exact method names.

3. **Taxonomy automation** — see item 1 above. Manual Sonnet classification works but needs to be automated for benchmark flow.
