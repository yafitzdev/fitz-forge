# B15 — `extract_provides` loses class ownership for dedented surgical artifacts

**Status:** open
**Impact:** 8/10 (blocks B9 broader fix from firing on the dominant real-world variant)
**Opened:** 2026-04-17
**Source:** Replay-validation of B9 broader fix (commit 5a15949) on
streaming_implementation. The fix is correct in unit tests but doesn't
trigger in production synthesis runs because the closure index is missing
the data it needs.

## Symptom

In `fitz_forge/planning/artifact/closure.py`, `extract_provides` decides whether
an artifact is "surgical" (just method bodies meant to merge into an existing
class) by checking whether the content has leading whitespace:

```python
# closure.py:1154
is_surgical = False
if content and content.lstrip() != content:        # <-- whitespace check
    for c in root.children:
        ...
```

When the model emits a surgical artifact **without leading indentation** (e.g.
`def stream_query(\n    self,\n    ...` starting at column 0), the check fails.
The function is then registered as a top-level function (`owner=None,
kind="function"`) instead of as a method on the target class
(`owner="Synth", kind="method"`).

Downstream effect: every closure invariant that asks *"does class C have
method X?"* — including the new B9 streaming-sibling check — sees `Synth`
with only its disk methods, missing the new `stream_query`.

## Evidence

Real plan from replay (`plan_replay.json` in
`benchmarks/challenges/streaming_implementation/results/2026-04-16_03-17-29_run_021/`):

- Strategy log line: `artifact[.../synthesizer.py]: strategy=surgical, ...`
- Artifact body first lines:
  ```
  def stream_query(
      self,
      query: str,
      ...
  ```
  `def line indent: 0 spaces`
- Engine artifact still calls `self._synthesizer.generate(...)` and yields the
  result.
- B9 broader check (commit 5a15949) does not fire — because `Synth.stream_query`
  was never registered as a method on `Synth`.

The `strategy=surgical` log line proves the **artifact pipeline already knows**
this is surgical. `extract_provides` re-detects surgical from content shape and
gets a different answer. Two independent classification paths disagree.

## Generalization

Invariant: **the artifact strategy classification (surgical / new_code) is the
single source of truth for how to interpret the artifact's contents. Closure
analysis must not re-derive that classification from a heuristic that may
disagree.**

## Scope of the class

This affects every closure invariant that uses `provides` to know which
class owns a method:
- B9 streaming-sibling (just landed)
- The existence check, when a sibling artifact adds a method to a class
  the call site references
- The kwargs check, when checking against a method signature added by a
  sibling artifact
- Any future invariant that needs cross-artifact class-method awareness

The wins from those invariants are silently muted by this metadata loss.

## Fix direction

Two acceptable fixes:

1. **Pass strategy classification into `extract_provides`** (or pass the
   target class explicitly when the strategy is surgical). The caller in
   `generate_artifact_set` already knows the strategy; thread it through.
   Drop the whitespace-based heuristic. Fall back to the heuristic only
   when strategy info is missing (e.g. when extract_provides is called
   from a test or one-off context).

2. **Strengthen the heuristic**: in addition to the whitespace check,
   treat content as surgical when:
   - All top-level items are function_definitions (not class_definitions),
     AND
   - `_target_class_for_file(filename, lookup)` resolves to a class that
     exists on disk (i.e. the file is known to host a class, so a bare
     def can only be intended as a method on it).

   This is fully heuristic but doesn't require API changes. May still
   miss corner cases (multiple classes in one file).

Recommendation: **(1)** — single source of truth, no heuristic guessing
when we already have the answer. Per CLAUDE.md rule 10, this is enforcing
the invariant ("strategy classification is canonical") rather than
patching one symptom.

## Acceptance

- B9 broader check fires on the replay's synthesizer.py + engine.py
  artifact pair — currently it doesn't.
- Unit test: a surgical artifact with `def name(` at column 0 (no leading
  whitespace) returns `provides` keyed under the target class, not as a
  bare function.
- Integration test: replay-validate the B9 fix; engine.py is regenerated
  with `self._synthesizer.stream_query(...)` instead of `.generate(...)`.
- No regression on existing extract_provides tests.

## Relationship to B9

B9's broader check (commit 5a15949) is implemented correctly for full-file
artifacts (verified by 12 unit tests). B15 prevents it from running on the
dominant real-world shape (surgical edits). **B9 is effectively dormant
until B15 is fixed.** Recommend: fix B15 next cycle, then re-validate B9.
