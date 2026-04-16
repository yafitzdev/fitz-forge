# 07 — Artifact Generation

## Problem

Synthesis produces a plan that *describes* new code but doesn't write it. The
plan's `needed_artifacts` list names files that have to exist for the plan to
be implementable (`api/routes/chat.py` with a `ws_chat()` method,
`services/chat_service.py` with a `query_stream()` generator, etc.). Turning
that list into real, parseable code that references the actual codebase — not
hallucinated classes, not imaginary methods, not wrong kwarg names — is its
own problem.

Small local LLMs asked to write a single file in isolation will routinely:

- Invent classes that don't exist in the codebase or siblings
- Call methods on protocols that don't carry those methods
- Use keyword arguments that aren't in the target function's signature
- Import from modules that don't exist
- Access attributes on a typed local variable that the type doesn't expose

Per-artifact validation catches some of this but not cross-file problems — a
route artifact calling `service.query_stream()` looks fine on its own; the
violation only appears at the *set* level, when you ask "does any sibling
artifact or the real codebase provide `query_stream` on `service`?"

## Solution

A subsystem (`fitz_forge/planning/artifact/`) with a two-level black box:

1. **Per-artifact** (`generate_artifact`) — one file in, one validated file
   out. Picks a strategy (surgical rewrite if a reference method exists, else
   new code), generates raw code, validates it, retries up to 3 times with
   specific error feedback.
2. **Set-level** (`generate_artifact_set`) — runs per-artifact generation for
   every spec, then runs the **closure family of checks** on the whole set.
   When cross-file invariants are violated, repair kicks in.

Set-level closure is the key insight: some invariants can only be checked over
the *set*, not any individual artifact. Per-artifact validation alone can
never catch cross-file inconsistency.

## How It Works

### Placement in the Pipeline

Artifact generation runs inside the synthesis stage (progress ~0.85), after
the `needed_artifacts` list has been extracted from the synthesis reasoning
and before grounding validation (stage 8). Synthesis calls
`generate_artifact_set(specs, ...)` and receives a closed set back.

### Context Assembly

`assemble_context()` (`artifact/context.py`) gathers every deterministic input
a generation call needs:

- **Source file** — compressed source of the target file if it exists;
  uncompressed source read from disk on fallback.
- **Class interfaces** — AST-parsed from the target class's `__init__` /
  `_init_components`: real method list, real field names, `self._attr` →
  type map.
- **Reference method** — if an existing method in the target class resembles
  what's being asked for, it's the seed for surgical rewrite.
- **Data-model fields** — Pydantic / dataclass / named-tuple field names
  pulled from the structural index for every class mentioned in the
  reasoning.
- **Target self-methods** — the real method list of the target class,
  injected with "do NOT invent new helper names" grounding.
- **Prior sibling signatures** — method signatures extracted from artifacts
  generated earlier in the same set, so later artifacts use real names.

### Strategy Selection

Two strategies in `artifact/strategy.py`, chosen deterministically:

| Strategy | When | Prompt basis |
|----------|------|--------------|
| `SurgicalRewriteStrategy` | `reference_method` exists | Given a real method from the target class, rewrite it for the new purpose |
| `NewCodeStrategy` | No reference method | Write a full new method/function body grounded in the supplied context |

Both emit **raw code** (no JSON wrapping) — this eliminates the quote-mangling
and schema-formatting failure mode that earlier JSON-based strategies had.
Output is cleaned with `_strip_fences()` (which preserves indentation).

### Per-Artifact Validation

`validate()` (`artifact/validate.py`) checks each artifact against a small set
of invariants:

- **Parseable** — AST parses without error. Non-Python files (`.ts`, `.rs`,
  `.prisma`, etc.) skip the AST parse.
- **Non-empty** — contains `def`/`class` for Python, or
  `function`/`async`/`export`/`const`/`model`/`interface`/`enum`/`struct`/`fn`/`pub`
  for other languages.
- **No fabrication of target-class helpers** — methods called on `self` must
  exist on the target class or be defined in the artifact itself.
- **Yield for streaming** — streaming artifacts must contain at least one
  `yield`.
- **Correct return type** — when the spec implies a specific return shape
  (`Iterator[Answer]`, `AsyncIterator[str]`), the signature is verified.

Failed artifacts are retried with the specific validation errors injected into
the prompt. Up to 3 attempts before returning `success=False`.

### Closure Family (Set-Level)

After all artifacts generate, `check_closure()` (`artifact/closure.py`) runs
five invariants over the set. Each operates on the whole set of artifacts +
the real codebase — no artifact is checked in isolation.

1. **Existence** — every cross-file symbol (`service.query_stream`, `FitzService`,
   `QueryResult.source_id`) must be satisfied by the codebase's structural
   index or a sibling artifact.
2. **Usage** — `async for` only on async iterators; `await` only on
   coroutines; `for` not on async iterables; async context managers only
   where supported.
3. **Kwargs** — every keyword argument name at a call site must be a
   parameter of the callee (real or sibling).
4. **Imports** — `from pkg.mod import X` → `X` must resolve in the codebase
   or a sibling artifact's exports.
5. **Field access** — `obj.field` on a typed local (from parameter
   annotations, `var = ClassName(...)`, service-locator returns, or
   `self._attr` whose type was parsed from the target class's `__init__`) —
   the field must exist on that type.

All five use the **type tracking** engine in `closure.py`: it reads parameter
annotations, assignment RHS class names, service-locator return types, and
`self._attr` types from the target class's real `__init__` on disk. Without
type tracking, field access and protocol method calls are unenforceable.

### Repair Loop

When closure violations are found, `generate_artifact_set` runs up to
`max_repair_iters=2` iterations, choosing one of two strategies per violation:

- **Strategy 1 — Expand** (for `missing` violations): route the missing
  symbol to a target file, then add a new sibling artifact that provides it.
  Example: a route calls `service.query_stream()` that doesn't exist
  anywhere — add `services/fitz_service.py` with `query_stream` as a new
  sibling artifact.
- **Strategy 2 — Regenerate** (for `usage`, `kwargs`, `field` violations):
  regenerate the offending artifact with the exact violation messages and a
  **real-signatures hint block** (sibling method signatures + real class
  fields for every class touched by a violation) injected into the prompt.

After both repair strategies exhaust, if violations remain, the set is
returned with `closed=False` and the unsatisfied violations listed.

### Output

`ArtifactSetResult` carries:

- `results` — list of `ArtifactResult` (one per spec; `success=False` allowed
  when validation couldn't be satisfied)
- `closed` — `True` if all five closure checks pass on the final set
- `closure_violations` — remaining violations when `closed=False`
- `repair_iterations` — how many repair passes ran
- `expanded_files` — files added by strategy-1 repair

Synthesis calls `result.as_artifact_dicts()` to get the
`[{filename, content, purpose}, …]` list it embeds in the plan's design
section.

## Key Design Decisions

1. **Closure is a property of the set, not the item.** Per-artifact
   validation cannot catch a route calling a service method that no sibling
   and no codebase class provides. The check is lifted to the set level so
   the invariant is enforceable.
2. **Raw code output, not JSON-wrapped.** Generation prompts ask for Python
   (or whatever target language) directly. No `{"content": "..."}` wrapping
   means no quote-escape breakage, no JSON-parse failures on valid code.
3. **Type tracking funds four of five closure checks.** Existence, usage,
   kwargs, and field access all need to know the type of a receiver. One
   type-tracking pass supplies all of them.
4. **Two repair strategies, not one.** Missing symbols usually mean the plan
   is under-specified (the set needed another artifact). Usage/kwargs/field
   violations usually mean the LLM got confused. Different causes need
   different fixes.
5. **Signatures propagate forward.** Each generated artifact's method
   signatures are accumulated as `prior_sigs` and injected into the next
   artifact's prompt. Later artifacts can't fabricate names for earlier
   artifacts' methods.

## Configuration

No user-facing configuration.

| Internal | Value | Description |
|----------|-------|-------------|
| `max_attempts` | 3 | Retries per artifact on per-artifact validation failures |
| `max_repair_iters` | 2 | Closure repair iterations before accepting unclosed set |

## Files

| File | Role |
|------|------|
| `fitz_forge/planning/artifact/generator.py` | `generate_artifact()`, `generate_artifact_set()`, result dataclasses |
| `fitz_forge/planning/artifact/context.py` | `assemble_context()` — deterministic input assembly |
| `fitz_forge/planning/artifact/strategy.py` | `SurgicalRewriteStrategy`, `NewCodeStrategy`, `_strip_fences()` |
| `fitz_forge/planning/artifact/validate.py` | Per-artifact `validate()` + `ArtifactError` |
| `fitz_forge/planning/artifact/closure.py` | Set-level `check_closure()`, five invariant checks, `route_missing_symbol()` |

## Related Features

- [Synthesis](06_synthesis.md) — produces `needed_artifacts` specs and
  consumes the closed artifact set.
- [Grounding Validation](08_grounding-validation.md) — second line of
  defense; runs AST checks on the artifact set after generation, in case the
  closure checks missed something.
