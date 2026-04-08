# F23: JSON-in-JSON Decision Fields

## Problem
The synthesis stage occasionally produces decision fields where the value is a raw JSON string instead of plain text — JSON nested inside JSON. This is a serialization defect in the model's output, not a content problem.

Note: Other "decision structure" issues are covered by existing patterns:
- Duplicate decision IDs → F1
- Missing/phantom critical path entries → F4

## Examples from run 73
- Plan 73b: `d10` has its `decision` field containing `"{\"decision\": \"...\", \"rationale\": \"...\"}"` instead of a plain text decision string

## Occurrence
1/5 plans (20%) in run 73. Low frequency but causes consistency penalty when it hits.

## Root Cause
The model sometimes generates a JSON object where the schema expects a string, then the extraction layer serializes the whole thing as a string — producing JSON-in-JSON. This happens when the model confuses the extraction schema with the parent structure.

## Potential Fix
Deterministic: during extraction, check if a string field parses as JSON. If it does, unwrap to extract the intended value. Cost: 0 LLM calls.

## Status: ❌ Not yet fixed
