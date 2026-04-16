# Per-Field Extraction

## Problem

Small quantized language models (3B parameters at Q3) cannot reliably produce
large, deeply-nested JSON objects in a single generation pass. When asked to
output the entire architecture+design schema at once (~6 top-level keys, dozens
of nested fields), the model frequently drops fields, breaks nesting, or emits
invalid JSON. The larger the schema, the higher the failure rate -- at 3B Q3,
anything over ~2000 characters of JSON schema becomes unreliable.

## Solution

Per-field extraction decomposes the synthesis stage into three phases:

1. **Best-of-3 reasoning + self-critique** -- the model writes three
   unrestricted natural language attempts, scope consensus picks the best,
   and a critique pass checks for scope inflation / hallucinated files.
2. **13 small JSON extractions** -- one extraction call per field group,
   each targeting a mini-schema under 2000 characters. The model only
   produces a few fields at a time from the reasoning it already wrote.
3. **Pydantic validation** -- extracted JSON is validated against typed
   schemas. Failed groups get Pydantic defaults instead of crashing the
   stage (partial plan > no plan).

This is the core technique that makes local-first planning viable on consumer
hardware. Without it, the pipeline would require a 70B+ model for structured
output reliability.

## How It Works

### Field Group Definitions

The synthesis stage defines a `_FIELD_GROUPS` list. Each entry is a dict
with three keys:

- `label` -- human-readable name used in logging and progress reporting
  (e.g., `"approaches"`, `"phases"`, `"risks"`)
- `fields` -- list of JSON keys to extract (e.g., `["approaches", "recommended",
  "reasoning", "scope_statement"]`)
- `schema` -- a `json.dumps()` string showing the exact shape the model must
  produce, kept under 2000 characters

The 13 groups, grouped by final plan section:

| Section      | Groups | Labels                                                        |
|--------------|--------|---------------------------------------------------------------|
| Context      | 4      | description, stakeholders, files, assumptions                 |
| Architecture | 2      | approaches, tradeoffs                                         |
| Design       | 4      | adrs, components, integrations, artifacts                     |
| Roadmap      | 1      | phases                                                        |
| Risk         | 2      | scheduling, risks                                             |

### Extraction Flow

`PipelineStage._extract_field_group()` in `base.py` handles every extraction.
For each group:

1. Build a minimal prompt: `"Extract the following fields: {field_names}. Return
   ONLY valid JSON matching this exact schema: {mini_schema}. --- ANALYSIS TO
   EXTRACT FROM --- {reasoning_text}"`
2. Optionally prepend codebase context (the `extra_context` parameter) with a
   grounding rule: "Every file path, module name, or API field you write MUST
   appear in the codebase context above."
3. Call `client.generate(messages, max_tokens=4096)`.
4. Parse the response through `extract_json()`.
5. On any failure (generation error, invalid JSON), log a warning and return `{}`
   so the caller merges Pydantic defaults instead of crashing.

### Selective Codebase Context

Not every field group needs codebase evidence. Injecting the full gathered
context into every extraction wastes tokens and can confuse the model.
Synthesis defines a `_CONTEXT_GROUPS` set listing which groups receive it:

- Context: `{"description", "files"}` — both reference real file paths
- Architecture: `{"approaches"}` — evaluates approaches against real code
- Design: `{"adrs", "components", "integrations", "artifacts"}` — design
  decisions and components must point at real interfaces
- Roadmap/Risk: receive slim design output (component + artifact names, no
  code bodies) plus decision constraints — enough to ground phases and
  risks without re-sending the full codebase.

Groups like `tradeoffs`, `stakeholders`, `assumptions`, and `scheduling`
receive no codebase context — they derive purely from reasoning text.

### JSON Extraction Robustness

`extract_json()` in `base.py` handles the messy reality of LLM-generated JSON
through a cascade of strategies:

1. **Direct parse** -- raw output is valid JSON
2. **Code fence extraction** -- strips `` ```json ... ``` `` wrappers
3. **Bare block extraction** -- finds the outermost `{...}` or `[...]`
4. **Truncated JSON repair** -- when the model hits its token limit mid-output,
   `_repair_truncated_json()` closes unclosed strings, trims back to the last
   structurally valid point, and appends missing `]`/`}` delimiters
5. **Unquoted identifier fix** -- converts `[d1, d2]` to `["d1", "d2"]` in
   JSON value positions
6. **Literal control characters** -- replaces raw newlines/tabs inside JSON
   strings with their escape sequences (`\n`, `\t`)

### Empty Field Retry

The `retry_if_empty` parameter on `_extract_field_group()` handles a specific
failure mode: the model returns valid JSON but with an empty list for a
critical field. When set, the method checks if the named field has an empty
list and retries once. If the retry also returns empty, it accepts the result
and lets downstream validators handle it.

## Key Design Decisions

1. **Mini-schemas under 2000 chars** -- empirically determined threshold where
   3B Q3 models maintain reliable JSON output. Larger schemas cause exponential
   increase in malformed output.

2. **Reasoning-first, extract-second** -- the model does its thinking in
   unrestricted prose, then extraction is a mechanical reformatting task. This
   plays to small models' strengths (prose) while minimizing their weakness
   (structured output).

3. **Partial plan over no plan** -- failed extractions return `{}` instead of
   raising exceptions. Pydantic defaults fill gaps. A plan missing one section
   is more useful than a crashed pipeline.

4. **Selective context injection** -- not every extraction needs the full
   codebase. Injecting it everywhere wastes tokens and introduces noise.
   Only groups that reference real code paths receive gathered context.

5. **No aggregation step** -- extracted groups are merged with `dict.update()`
   then validated through Pydantic. There is no LLM call to "combine" results;
   that would reintroduce the large-schema problem this technique solves.

## Configuration

No user-facing configuration. The 13 field groups are defined as a module-level
constant in `synthesis.py`. The extraction budget (`max_tokens=4096`) is
hardcoded in `_extract_field_group()`.

## Files

| File | Role |
|------|------|
| `fitz_forge/planning/pipeline/stages/base.py` | `_extract_field_group()` method, `extract_json()` function |
| `fitz_forge/planning/pipeline/stages/synthesis.py` | All 13 field groups (context 4, architecture 2, design 4, roadmap 1, risk 2) |

## Related Features

- [Synthesis](../pipeline/06_synthesis.md) -- the only stage that uses
  per-field extraction in the current pipeline
- [Crash Recovery](crash-recovery.md) -- checkpoints after each stage so
  extracted fields are not lost on crash
