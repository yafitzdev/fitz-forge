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

## 2. Artifact Generation Black Box

**Status:** Designed, not implemented.

**Problem:** Current artifact generation is 500+ lines across 4 functions with 13 helpers, each addressing a specific failure pattern (F7, F9, F10, F21, F25). Fixes are whack-a-mole — each one adds complexity. Output validation happens in the scorer (after the fact), not in the pipeline (before returning).

**Design:**

```
fitz_forge/planning/artifact/
  __init__.py              # exports generate_artifact()
  context.py               # ArtifactContext dataclass + assemble_context()
  validate.py              # validate() -> list[ArtifactError]
  strategy.py              # Protocol + SurgicalRewriteStrategy + NewCodeStrategy
  generator.py             # generate_artifact() — the black box
```

**Interface:**
```python
async def generate_artifact(
    client, filename, purpose, source_dir,
    structural_index, decisions, reasoning,
    prior_sigs=[], max_attempts=3,
) -> ArtifactResult
```

**Architecture:**
1. `assemble_context()` — gather all inputs deterministically (source, reference method, interfaces, schema fields)
2. Pick strategy: `SurgicalRewriteStrategy` if reference method exists, `NewCodeStrategy` if genuinely new code
3. `strategy.generate()` — LLM call (pluggable)
4. `validate()` — AST parse, fabrication check, yield check, return type check
5. If validation fails: `strategy.retry()` with specific error feedback injected
6. Max 3 attempts, then return failure

**Strategy protocol:**
```python
class ArtifactStrategy(Protocol):
    name: str
    async def generate(self, client, context: ArtifactContext) -> str
    def build_retry_prompt(self, context, previous, errors) -> str
```

**Validation checks (all deterministic):**
- Parseable (AST with recovery)
- No fabricated methods (self._xxx calls vs structural index)
- No fabricated classes (ClassName() vs index)
- Has yield (streaming files only)
- Correct return type (Iterator/Generator, not Answer)
- Non-empty (has at least one def)
- No NotImplementedError (soft fail)

**Key changes from current code:**
- Surgical rewrite is the DEFAULT for any file with a reference method (not gated by 3+ pipeline steps)
- Retry stays on the same strategy with error feedback (no fallback to worse path)
- Output validation runs the SAME checks as the scorer — if it passes validation, the scorer will agree
- `_repair_fabricated_refs()` goes away — if output is wrong, retry with feedback instead of silent patching
- Strategies are hot-swappable — can add DiffStrategy, TestDrivenStrategy etc. later

**Effort:** Medium — refactor, not net-new logic. Most code exists but needs restructuring.

**Depends on:** Nothing. Can be implemented independently.

---

## Implementation Order

1. **Artifact black box first** — this is the bigger win. Fixes the root cause of bad artifacts instead of detecting them after the fact. Will likely improve both deterministic AND taxonomy scores.
2. **Taxonomy integration second** — once artifacts are better, wire in the automated Sonnet scoring to track the improvement.
