# F21: Structural Overview Stub Confusion

## Problem
The model reads `...` (Ellipsis) in the structural overview — which is an abbreviation for "method body omitted" — and concludes the method is literally an unimplemented stub. This causes it to bypass existing, fully-implemented layers when designing the architecture.

## Examples from run 73
- Plans 73c, 73d claim `CodeSynthesizer.generate()` is "a stub with body `...`" — the real method is ~50 lines of retrieval + context assembly + LLM generation
- Plan 73d claims `fitz.query()` is "stubbed" — it has a full implementation body
- Plan 73c then bypasses the synthesizer entirely and calls `chat_stream()` directly from the engine, skipping retrieval/guardrails/context assembly
- Plan 73e proposes calling `chat_stream` directly from `answer_stream`, bypassing the entire synthesis pipeline

## Occurrence
3/5 plans (60%) in run 73. Affects alignment (-2 pts) and implementability (-1 pt).

## Root Cause
The structural overview format shows method signatures but abbreviates bodies:
```
classes: CodeSynthesizer [generate -> str, ...]
```
The model interprets `...` as `pass` or literal Ellipsis (Python stub convention), not as "body omitted for brevity." Once it believes a method is unimplemented, it makes wrong architectural decisions — skipping layers, proposing unnecessary rewrites.

## Impact
- Alignment: model designs around a phantom "stub" instead of the real implementation
- Consistency: ADRs/decisions reference stubs that don't exist, contradicting artifacts that correctly use the method
- Implementability: developers following the plan would skip the synthesis layer, breaking retrieval + guardrails

## Potential Fixes
1. **Change structural overview format**: Replace `...` with something unambiguous like `[implemented]` or show a 1-line summary of what the method does. Cost: 0 LLM calls, pure format change.
2. **Inject method body summaries**: For key methods (engine.answer, synthesizer.generate), include a 2-3 line summary of what they do in the context. Cost: 0 LLM calls, increases prompt size.
3. **Add explicit note in prompt**: "The structural overview abbreviates method bodies with `...` — this does NOT mean the method is unimplemented." Cost: 0, prompt change only.

## Status: ❌ Not yet fixed
