# F13: Upstream Reasoning Failures

## Problem
Floor plans (scores 29-37) are NOT caused by artifact generation issues. They're caused by failures in UPSTREAM stages — decision decomposition and synthesis reasoning — that produce fundamentally wrong analysis:

### Pattern A: Decision duplication (Plan 9)
15 decisions where 7 are near-identical duplicates asking the same question. The F1 dedup filter catches exact duplicates (0.85+ similarity) but these are semantically identical with different wording (similarity 0.70-0.80).

### Pattern B: Codebase misread (Plan 4)
d3 claims "StreamingChatProvider doesn't exist in the codebase" when it clearly does (visible in base.py structural index). d7 in another plan claims it's "dead code." The model misreads its own context.

### Pattern C: Empty/hollow sections (Plan 3)
Architecture section has `approaches: []` and `recommended: ""`. The reasoning produced content but the extraction returned empty. Might be an F6-adjacent issue (empty extraction) that the retry didn't catch because `approaches` wasn't in the retry list.

Wait — approaches IS in the retry list. So either:
1. The retry also returned empty (both attempts failed)
2. The model genuinely produced no approaches in the reasoning

## Impact
- Plan 3: consistency=4, alignment=4, implementability=4 → total 29
- Plan 4: contract=5, consistency=4 → total 32
- Plan 9: consistency=4 → total 37
- These 3 plans drag the 10-plan average from ~43 to 40.3

## Root Cause
These are model quality limits on a 3B parameter model:
- Semantic dedup requires understanding meaning, not just string similarity
- Codebase misreads happen when the model ignores parts of its context
- Empty reasoning sections happen when the model fails to generate useful content

## Fix Options (increasingly expensive)
1. **Semantic dedup**: Use embedding similarity instead of string similarity for decision dedup (requires embedding model)
2. **Fact-checking pass**: After decision resolution, verify key claims against the structural index ("does StreamingChatProvider exist?")
3. **Best-of-3 reasoning**: Generate 3 reasoning candidates instead of 2 to reduce chance of hollow sections
4. **Post-extraction validation**: Check that architecture.approaches is non-empty AND substantive (not just empty strings)

## Fix (IMPLEMENTED)
**Best-of-3 with scope consensus**: Generate 3 reasoning candidates, extract scope size (files + types), compute median, penalize candidates >50% from median. This filters out over-engineered outliers.

Results: Run 67 avg 45.3/60 (was 42.5 with best-of-2). Floor rose from 33→37. The consensus band naturally selects for "what most candidates agree on" as reasonable scope.

**Key learning**: The proactive fix for model quality limits is MORE CALLS + PICK THE BEST, not post-processing. The model WILL produce good output some % of the time. The scorer's job is to select it.

Also: F13C fallback derives approaches from key_tradeoffs when extraction returns empty.

## Status: PARTIALLY FIXED (best-of-3 + F13C fallback)
