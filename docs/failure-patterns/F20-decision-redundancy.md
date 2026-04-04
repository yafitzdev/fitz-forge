# F20: Decision Redundancy in Synthesis Prompt

## Problem
The decomposition + resolution stages produce 12-15 decisions that are highly redundant. Multiple decisions resolve to the same conclusion, repeat the same constraints, and cite the same evidence. This bloats the synthesis prompt with duplicate information.

Example from a real run (13 decisions, 17K chars):
- **d1, d6, d10, d12** all conclude: `chat_stream() -> Iterator[str]`, `chat()` unchanged
- **d2, d3, d4, d5** all repeat: `answer()` signature frozen, `Answer` is a static dataclass
- The same method signature (`chat_stream(messages: list[dict[str, Any]], **kwargs: Any) -> Iterator[str]`) appears in evidence for 4+ decisions
- The same constraint ("existing method must not be modified") appears in 6+ decisions

8 of 13 decisions could collapse into 2-3 without losing any information.

## Impact
- Synthesis prompt decisions section: ~17K chars (33% of total 50K prompt)
- After merge: estimated ~5K chars (70% reduction, 12K chars saved)
- Total prompt reduction: ~24% (50K → 38K)
- Shorter prompt → more attention budget for codebase context → potentially less F10 fabrication
- Shorter reasoning output (fewer decisions to narrate) → less attention drift during generation
- Currently the #1 contributor to prompt bloat

## Occurrence Rate
100% of plans — every decomposition produces redundant decisions because questions are scoped to individual files, but many files share the same architectural decision.

## Root Cause
The decomposition prompt asks for "atomic decisions that can each be resolved independently with focused context." This is correct for resolution (each decision reads 1-3 files). But after resolution, many atomic decisions converge to the same conclusion because they examined different facets of the same architectural truth.

The pipeline currently has string-similarity dedup (F1, threshold 0.85) on the decomposition questions, but no semantic dedup on the resolved decisions.

## Fix
New post-resolution stage: **Decision Merger**

1. After all decisions are resolved, cluster by semantic similarity:
   - Decisions with >50% relevant_files overlap
   - Decisions with similar constraint text
   - Decisions that reference the same method signatures in evidence
2. For each cluster, produce one merged decision:
   - Union of all evidence (deduplicated)
   - Union of all constraints (deduplicated)
   - Combined decision text (the shared conclusion)
3. Rewrite depends_on references to point to merged decision IDs

**Approach options:**
- **Deterministic**: Cluster by relevant_files overlap + constraint SequenceMatcher similarity. Zero LLM cost.
- **LLM-assisted**: Give the model all resolved decisions and ask it to merge redundant ones. One extra LLM call.
- **Hybrid**: Deterministic clustering, then LLM summarizes each cluster into one decision.

## Measurement
- Before: count decisions entering synthesis, measure decisions section char count
- After: count merged decisions, measure reduced char count
- F10 regression test: does the merge reduce fabrication rate?

## Test Data
- Baseline: 13.2 avg decisions, 18.8K avg chars, 31% constraint redundancy, 40% evidence redundancy
- After fix (file-overlap merger): 13→8 decisions, 18.7K→15.2K chars (19% reduction)
- Correctly clusters: provider implementations (d1+d6+d8), engine methods (d2+d12), SDK methods (d3+d13), route+schema (d4+d9)
- Uses union-find on evidence file overlap — zero thresholds, zero LLM cost

## Status: FIXED (deterministic file-overlap merger)
