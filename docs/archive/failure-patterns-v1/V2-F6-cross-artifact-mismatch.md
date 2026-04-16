# V2-F6: Cross-Artifact Method Mismatch

**Occurrence:** 8/10 plans (run 89)
**Impact:** -8 pts avg (consistency score 12.1/20 vs potential 20/20)

## Two Sub-Patterns

### V2-F6a: Scorer parse recovery gap (dominant, 8/10 plans)

The consistency checker's `_extract_method_definitions()` uses raw `ast.parse()` — no parse recovery. But the artifact checker's `_try_parse()` uses dedent + class wrap recovery.

Surgical rewrite artifacts start with 4-space indentation (method bodies meant to be inside a class). The artifact checker recovers these fine (dedent → parse OK → parseable=true, score=100). But the consistency checker can't parse them → sees "(no methods)" → every caller fails the method agreement check.

**Concrete example (plan_06):**
- `engine.py` content starts with `    def answer_stream(self, ...)` (4-space indent)
- Artifact checker: dedent → parse OK → **parseable=true**
- Consistency checker: raw `ast.parse()` → **SyntaxError → "(no methods)"**
- `routes/query.py` calls `engine.answer_stream()` → **false consistency failure**

This is a **scorer bug**, not a pipeline bug. The methods actually match.

**Evidence:**
```
$ python -c "ast.parse(content)"  # FAILS: unexpected indent
$ python -c "ast.parse(textwrap.dedent(content))"  # OK
```

### V2-F6b: Genuine method name disagreement (2/10 plans)

The model uses different method names across artifacts. Examples from run 89:
- `engine.py` defines `answer_stream()` but `synthesizer.py` calls `_synthesizer.stream()` instead of `_synthesizer.generate_stream()`
- `engine.py` calls `_synthesizer._build_abstain_message()` but synthesizer artifact doesn't define it

Root cause: each artifact is generated independently with only prior method signatures for context. The model invents names based on what "sounds right" and sometimes diverges.

## Relationship to Other Patterns

- V2-F6a is entirely a scorer bug — fixing `_extract_method_definitions()` to use parse recovery would eliminate ~80% of consistency failures
- V2-F6b is a real pipeline quality issue — prior signature injection already helps but doesn't fully prevent it
- V2-F1 (engine.py truncation) was previously the main cause of false consistency failures via cascade — that path is now fixed by the cascade exclusion

## Potential Fixes

### V2-F6a (scorer bug)
Add parse recovery to `_extract_method_definitions()` — same dedent/class-wrap strategy as `_try_parse()`.

### V2-F6b (genuine mismatches)
1. **Decision-level method naming**: decomposition or resolution could commit to exact method names, not just "add streaming support"
2. **Full artifact context**: inject full artifact content (not just signatures) into subsequent artifact prompts
3. **Post-generation reconciliation**: after all artifacts are generated, check for method name mismatches and re-generate mismatched artifacts with the correct names
