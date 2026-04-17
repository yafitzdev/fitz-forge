# B1 — Risks field group extraction fails, leaving "Risk Analysis" section empty

**Status:** open
**Impact:** 6/10
**Opened:** 2026-04-17
**Observed run:** `plan_dfb348b2b01d_20260417_183600.md` (fitz-sage, gemma-4-26b-a4b-it@q6_k, live `fitz-forge plan` invocation)

## Symptom

The synthesis stage logs a hard failure in one field group:

```
Stage 'synthesis': field group 'risks' extraction failed:
Could not extract valid JSON from output (2585 chars).
Preview: {\n  "risks": [\n    {\n      "category": "technical",\n
"description": "Mid-Stream Failure & Partial Downloads: Database connection drops
or errors after HTTP 200 OK result in truncated/corrupt CSV files without explicit
error messages.",\n      "impact": "high",\n      "likelihood": "medium",\n
"mitigation": "Implement localized try-except blocks within the generator to yield
a specific 'ERROR: [Message]' row at the end of the stream.",\n      "contingency":
"Include a metadata header in the ...
```

Pydantic default kicks in → `Risk Analysis` section in the rendered plan is empty
(just the `## Risk Analysis` heading, no content).

## Evidence

- The model **did** produce substantive risk content (the preview shows a well-formed `{"risks": [...]}` start with real, relevant risks).
- The 2585-char output parses as JSON in isolation (confirmed manually).
- The extractor (`fitz_forge/planning/pipeline/stages/base.py`, `extract_json`) rejected it.
- This is not a model capability issue — output quality is fine. It's a parser/extractor robustness issue.

## Generalisation

Invariant: **when a field-group LLM call emits a recognisable JSON object containing
the expected top-level key, extraction must succeed even in the presence of**
- trailing prose after the closing `}`
- extra whitespace / backslashed newlines in string values
- unicode escapes
- content straddling a ```json code fence

Current `extract_json` likely trips on one of these. Per-group Pydantic defaults
are a safety net for genuine extraction failures, but here the content was real and
should have been kept.

## Scope of the class

The same failure mode could silently hit any field group in Context / Architecture+Design
/ Roadmap+Risk. The user only noticed "risks" because that section rendered empty
at the top level. Check whether other groups have silently defaulted on other runs —
particularly `assumptions`, `stakeholders`, `tradeoffs`, `components`, `phases`.

## Fix direction (not yet applied)

1. Read `fitz_forge/planning/pipeline/stages/base.py::extract_json` and reproduce the
   failure with the saved trace output from this run (see traces/ for the
   synthesis field-group call that emitted the 2585-char payload).
2. Identify which specific token shape broke parsing.
3. Generalise the fix per CLAUDE.md rule #10 — enumerate every variant, don't patch
   one site. E.g. if the fix is "strip ```json fences", handle also bare ```,
   trailing prose, BOM, etc.
4. Add regression test with the actual failing payload.
5. Re-run this challenge (5 plans) to confirm `Risk Analysis` no longer empties out.

## Acceptance

- 5/5 plans on the `csv_export` challenge render a non-empty Risk Analysis section.
- No field-group extraction failures logged in any of the 5 runs.
- Regression test with the exact failing 2585-char payload passes.
