# B9 — Engine stream method calls blocking `generate()` instead of `stream_generate()`

**Status:** superseded by semantic-review gate
**Impact:** 10/10
**Opened:** 2026-04-17
**Superseded:** 2026-04-18

**Supersession note (2026-04-18):** The streaming-sibling closure check
(`_StreamingSiblingScanner` in `closure.py`) was removed along with its
B15/B16/B17 metadata-extraction fixes. The check worked in unit tests
but accumulated four narrow tree-sitter predicates to fire on production
shapes — classic bandaid stacking (CLAUDE rule 12). Replaced with the
LLM semantic-review gate in `fitz_forge/planning/artifact/semantic_review.py`
which reads design intent + artifact contents together and reports
contradictions like "engine yields from self._synthesizer.generate()
but the plan defines stream_query — call that instead." Repair uses
the same regeneration-with-feedback pathway as the old closure check,
so the repair side of the fix is preserved; only the detection moves
from shape matching to LLM semantics.

**Earlier fix attempt (preserved for history):** Closure-set check
`_StreamingSiblingScanner` (commits 35918a9 + 5a15949) detected
streaming methods that called blocking siblings on the same class.
Required B15 (extract_provides surgical-class ownership), B16
(extract_init_self_attrs walks init helper methods), and B17
(yielded-names walks complex yield expressions) to fire on production
code shapes. B15/B16 survive as real pre-existing fixes to grounding
infrastructure; B17's broader yield walk is superseded alongside B9.

**Validation:** Fresh 5-plan benchmark on streaming_implementation
(run_031). Tier-1 avg 100.0/100, Tier-2 avg 77.3/100 (was 97.7/61.8).
Architecture distribution: A1×4, A4×1 (was A4-dominant 14/30).
engine.py: 5/5 classified E1.
**Source:** Tier-2 Sonnet scoring of run_021 (30 plans, streaming_implementation)

## Symptom

Pattern observed in **~25 of 30 plans**:

The plan correctly adds a `stream_generate()` / `stream_query()` method to the
synthesizer that wraps `chat_stream()` (artifact earns S1 on Tier-2).

The plan also adds a `stream_query()` / `stream_answer()` method to the engine
that dutifully replicates the full RAG pipeline (analyze, detect, retrieve,
rerank, read, expand, guardrails, assemble) and has `yield` statements.

**But at the final generation step, the engine calls `self._synthesizer.generate()`
— the blocking method — and then `yield answer`.** The synthesizer's
`stream_generate` method is defined but never called from the engine. The
model has literally left a comment admitting it:

> `# Note: In a real streaming implementation, the synthesizer would need to
> support an async/generator interface. Since we must use existing methods
> and cannot invent new ones, we call generate() and yield its text.`

End-to-end effect: no real streaming. The full blocking answer is produced,
then yielded once as a single Answer object.

## Why Tier-1 misses this

- Tree-sitter parses `yield answer.text` (after blocking call) identically to
  a real streaming yield. `yield=True` on the artifact check.
- Method-name consistency passes: the engine method exists, the synthesizer
  method exists, both are referenced where they're defined.
- No fabricated imports, no missing classes, no unparseable code.
- Deterministic score on the same 30 plans: **97.7 avg**. Tier-2: **37.3 avg.**

## Generalization

Invariant the pipeline should enforce:

> **When an artifact defines a streaming variant of a method (`stream_*` /
> `*_stream` / returns an Iterator/Generator and yields), any sibling artifact
> method that claims to stream and has access to the object defining the
> streaming variant must invoke the streaming variant — not the blocking one.**

Specifically for this task family: if `synthesizer.stream_generate` is defined
in the plan, then `engine.stream_query`'s call site for the synthesizer must
be `stream_generate`, not `generate`.

## Scope of the class

- Same failure shape will appear in any "wrap a blocking pipeline for
  streaming" task — not just LLM synthesis. E.g. database cursors, file
  readers, network clients.
- Will also appear when the "orphaned streaming method" is in a different
  layer (route defines async generator, service never yields from it; etc.).

## Fix direction (not yet applied — needs alignment)

Candidate levers, in order of preference:

1. **Closure check (invariant at artifact-set level, per CLAUDE.md rule 11).**
   After artifact generation, for each artifact that claims to stream
   (return-type is Iterator/Generator, method has yield):
     - Collect the synthesizer-like methods it calls.
     - For each such call, check whether a sibling artifact defines a
       streaming variant (name pattern `stream_*` or `*_stream`, or return-
       type iterator/generator with same signature).
     - If a streaming variant exists and the artifact called the blocking
       version, emit a closure violation; regenerate the artifact with
       targeted feedback ("you called X.generate() but X.stream_generate()
       exists — call the streaming variant").
2. **Synthesis reasoning-prompt hint.** Cheaper to try. Add a decision in
   `decision_decomposition` that asserts "if you define a streaming synth
   method, the engine's stream method must call it."
3. **Per-field critique.** The self-critique pass in synthesis already
   catches scope inflation. Teach it to catch "streaming method calls
   blocking sibling method."

(1) is the most generalisable and catches the invariant at the set level.
(2) and (3) are faster wins but don't generalise.

## Acceptance

- Rerun (replay from `snapshot_after_decision_resolution.json`) on 5 plans.
- Tier-2 architecture distribution shifts from A4-dominated to A1/A2-dominated.
- Tier-2 taxonomy average rises from current 61.8 baseline toward ~80+.
- No regression on Tier-1 deterministic (stays ~97).

## Implementation status (2026-04-17)

Two commits landed:
- `35918a9` — stem-match check (`stream_<X>` / `<X>_stream` siblings).
  Too narrow — misses real variants where the streaming method has a
  different verb root (e.g. `stream_query` vs blocking `generate`).
- `5a15949` — broader any-name check (any sibling on the same class
  with iterator/generator return type or yield-in-body). 12 new unit
  tests cover both stem-match and broad cases. False-positive guards:
  only fires when the call result is yielded (assignment then yield,
  yield-from, direct yield); suppressed when the streaming sibling is
  also called somewhere in the body.

**Validation status: blocked.** The replay-validate step produced the
same broken plan (engine still calls `self._synthesizer.generate()` and
yields the result). Diagnosis: closure check sees no streaming sibling
on `Synth` because the synthesizer.py artifact's `stream_query` method
was registered as a top-level function, not as a method on `Synth`. See
**B15** — `extract_provides` loses class ownership for dedented surgical
artifacts. The B9 check is correct in isolation but starves on missing
metadata in production.

Next cycle: fix B15, then re-replay-validate B9.
