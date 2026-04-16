# V2-F8: Fabricated Types

**Occurrence:** 2/6 plans (run 91), was 1/10 (run 89), 5/7 (run 84)
**Impact:** -15 pts on affected plans

## What Happens

The model invents classes that don't exist in the codebase (e.g. `AnswerChunk`, `StreamingChunk`, `StreamingChatResponse`).

## Sub-Patterns

### F8a: Fabricated classes in engine.py

The model creates typed chunk objects (`AnswerChunk`, `StreamingChunk`) instead of yielding raw strings. Training data influence — OpenAI/Anthropic SDKs use typed chunks.

**Run 91:** 2/6 plans had fabricated classes in engine.py. The artifact black box validation should have caught these but didn't because `check_artifact` couldn't parse indented surgical output. **Fixed post-run** with dedent recovery in `_check_fabrication`.

### F8b/F8c: Provider subclasses / Request DTOs

Not seen since run 88. Fixed by decomp scorer's ref_complete criterion.

## Status

The artifact black box now validates against the structural index with dedent recovery. Fabricated classes trigger a retry with "Class 'AnswerChunk' not found — remove or replace it." Next benchmark should show improvement.
