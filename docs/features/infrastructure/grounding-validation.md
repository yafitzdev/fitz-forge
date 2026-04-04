# Grounding Validation

## Problem

LLMs hallucinate method names, class APIs, and function signatures. When the
Architecture+Design stage produces code artifacts (implementation files), those
artifacts may call `self.get_context()` when the real method is
`self.gather_context()`, instantiate `PlanEngine` when the real class is
`PlanningPipeline`, or call functions with the wrong number of arguments. These
fabricated references compile in the LLM's imagination but fail in reality.

Verification agents catch architectural-level issues (wrong integration points,
missing layers) but operate on the reasoning text, not the generated code.
A second validation pass is needed that checks the actual artifacts against the
actual codebase.

## Solution

Two-path validation of generated artifacts against the target codebase:

- **Path 1 (AST)**: deterministic, zero-hallucination -- parses artifacts with
  Python's `ast` module and checks every symbol reference against the structural
  index. Reports fabricated methods, missing classes, wrong arities.
- **Path 2 (LLM)**: architectural judgment -- checks for gaps that AST cannot
  see (missing intermediate layers, wrong assumptions about helper methods,
  files that need modification but are not listed).

When AST violations are found, a targeted repair mechanism sends exact violation
messages to the LLM for correction. This is far more reliable than asking the
LLM to self-audit, because the violations are machine-detected facts, not
guesses.

## How It Works

### Structural Index Lookup

`StructuralIndexLookup` parses the AST-extracted structural index (produced
during agent context gathering) into programmatic lookup tables:

- `classes` -- maps class name to list of `IndexedClass` (name, file, bases,
  methods, decorators). A class may appear in multiple files.
- `functions` -- maps function name to list of `IndexedFunction` (name, file,
  params, return_type).
- Quick predicates: `class_exists()`, `function_exists()`,
  `method_exists_anywhere()`, `class_has_method()`.
- Fuzzy matching: `suggest_method()`, `suggest_class()`, `suggest_function()`
  using `difflib.get_close_matches()` with cutoff 0.6.

The index format uses `## file_path` headers with `classes:` and `functions:`
lines. Example:

```
## fitz_forge/planning/pipeline/stages/base.py
classes: PipelineStage(ABC) [name, progress_range, build_prompt, parse_output]
functions: extract_json(raw_output) -> dict
```

### AST Validation (Path 1)

`check_artifact()` parses each artifact's content with `ast.parse()` and walks
the AST looking for:

**`self.method()` calls** -- checks if the method exists on classes defined
within the artifact, classes in the target file (matched by filename), or any
class in the codebase index. Missing methods are reported with fuzzy-match
suggestions.

**Standalone function calls** -- uppercase names check `class_exists()`,
lowercase names check `function_exists()` and validate arity (flags when args
differ from expected params by more than 2, allowing slack for defaults).

**Parallel method signatures** -- `_check_parallel_signatures()` checks that
streaming variants (e.g., `generate_stream()`) have compatible parameters with
their base methods. Suffixes checked: `_stream`, `_async`, `_streaming`.

**Skip list** -- builtins, stdlib modules, common third-party types
(`BaseModel`, `APIRouter`), and Pydantic validators are excluded.

### LLM Validation (Path 2)

`build_llm_grounding_prompt()` constructs a prompt with:
- The AST-detected violations (machine-detected, so the LLM knows what is
  already confirmed wrong)
- A summary of all artifacts being validated
- Relevant sections of the structural index (filtered to files mentioned in
  artifacts)
- Resolved decisions from the decomposed pipeline

The LLM returns a JSON assessment: `missing_layers`, `missing_files`,
`wrong_assumptions`, and a summary. This path is optional -- if no LLM client
is provided, only AST validation runs.

### Violation Repair

`repair_violations()` fixes AST-detected fabrications through targeted LLM
calls:

1. **Group violations by file** -- violations are grouped by artifact filename.
2. **One LLM call per artifact** -- the prompt includes exact violation
   messages and source code. The LLM produces
   `{"replacements": [{"old": "exact text", "new": "corrected text"}]}`.
3. **Apply replacements** -- `str.replace()` applies each correction where the
   old text is found verbatim. Replacements that do not match are silently
   skipped (the old text may have been slightly different due to whitespace).
4. **Return corrected artifacts** -- the artifact list is returned with
   repaired content. Artifacts with no violations pass through unchanged.

### Combined Report

`validate_grounding()` runs both paths and returns a `GroundingReport`:

```python
@dataclass
class GroundingReport:
    ast_violations: list[Violation]     # deterministic findings
    llm_gaps: dict[str, Any] | None     # LLM architectural assessment
    total_violations: int               # count of AST violations
```

Each `Violation` records: artifact filename, line number, symbol name, kind
(`missing_method`, `missing_class`, `missing_function`, `wrong_arity`,
`parse_error`, `param_mismatch`), detail message, and optional suggestion.

### Integration Point

Grounding validation runs in the `DecomposedPipeline` after synthesis
(progress ~0.94), before the coherence check. This positioning means:

- All artifacts have been generated (synthesis is complete)
- Violations can be repaired before the final plan output
- The coherence check sees the corrected artifacts

## Key Design Decisions

1. **AST over regex** -- `ast.parse()` understands Python structure. Regex
   matching on `self.method_name(` would produce false positives from comments,
   strings, and variable names. AST gives precise line numbers and argument
   counts.

2. **Machine-detected violations for repair** -- telling the LLM "this method
   does not exist (verified by AST)" is dramatically more effective than
   "check if your method calls are correct." The LLM does not need to guess
   what is wrong; it only needs to fix the specific, confirmed error.

3. **Fuzzy suggestions over hard blocks** -- when a method is not found, the
   validator suggests close matches. This helps both the repair LLM and human
   reviewers understand the likely intent.

4. **Generous arity slack** -- allows a difference of 2 between actual and
   expected argument counts. Python defaults, `*args`, and `**kwargs` make
   exact matching impractical. A difference of 3+ likely indicates a mismatch.

5. **One repair call per file** -- grouping violations by file minimizes LLM
   calls while preserving cross-reference context within each artifact.

6. **Skip list is conservative** -- errs toward not flagging. Missing a
   fabrication is less harmful than flooding the report with false positives.

## Configuration

No user-facing configuration. Grounding validation runs automatically in the
decomposed pipeline when artifacts are present. The LLM validation path
requires a client to be available; AST validation always runs.

## Files

| File | Role |
|------|------|
| `fitz_forge/planning/validation/grounding.py` | `StructuralIndexLookup`, `check_artifact()`, `check_all_artifacts()`, `repair_violations()`, `validate_grounding()`, `GroundingReport` |
| `fitz_forge/planning/pipeline/orchestrator.py` | Calls `validate_grounding()` after synthesis in the decomposed pipeline |
| `fitz_forge/planning/pipeline/stages/base.py` | `extract_json()` used by repair to parse LLM responses |

## Related Features

- [Verification Agents](verification-agents.md) -- pre-extraction architectural
  verification that complements post-synthesis AST grounding
- [Per-Field Extraction](per-field-extraction.md) -- produces the artifacts
  that grounding validation checks
- [LLM Providers](llm-providers.md) -- provides the client for LLM validation
  path and repair calls
