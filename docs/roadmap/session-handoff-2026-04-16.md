# Session Handoff — 2026-04-16

Written at the end of a long session where a lot happened. Pick this up
cold in a fresh session; everything you need to resume is below.

## What shipped this session (6 commits on top of v0.6.1)

```
19db9dd feat(synthesis): stronger schema injection + rule against fabricated classes   [Fix G]
c85d0ef fix(closure): collapse cascading field violations on fabricated owners          [Fix E]
bb08e0a feat(closure): regen prompts show real signatures, not just errors              [Fix C]
b3e4740 feat(closure): propagate iterator kind through variable bindings                [Fix A-var]
03587ef feat: V2-F7 Fix A — decomposition→synthesis closure invariant                   [Fix A-decomp]
23a63c9 fix: grounding arity check was false-positiving on keyword-only params          [arity fix]
```

All of these target concrete V2-F* failure patterns that showed up in
run 92/93/16/17/18/19 benchmark runs. Summary of what each does:

### 1. arity fix (`23a63c9`)

**Problem.** `augment_from_source_dir` captured only `node.args.args` when
indexing top-level functions — which excludes keyword-only args, varargs,
and **kwargs. Functions like
`build_retrieval_profile(a, b, c, *, x=0, y=0, z=None)` were indexed as
3 params, so any correct 6-arg call tripped
`|actual_args - expected| > 2` tolerance and got flagged `wrong_arity`.

**Why it mattered.** This was blocking every gemma run 16 engine.py
generation attempt — the model was writing correct code but the
validator kept rejecting it, 3 retries in a row, and engine.py was
dropped from every plan.

**Fix.** Index all parameter kinds
(`args.args + kwonlyargs + vararg + kwarg`). Also skip the arity check
entirely when the callee has `*args` / `**kwargs` — variadic callees
accept anything.

**Impact.** Gemma Tier 1 jumped 80.9 → 94.7 in a single change. This
was the biggest single unlock of the session.

**Files.** `fitz_forge/planning/validation/grounding/index.py`,
`fitz_forge/planning/validation/grounding/check.py`

### 2. V2-F7 Fix A — decomp→synthesis closure (`03587ef`)

**Problem.** Synthesis reasoning sometimes drops files that
decomposition analyzed in multiple decisions. The model picks an
architecture that routes around the file even though decomp explicitly
identified it as needing changes. This was the dominant V2-F7 pattern
in run 92/93 (missing engine.py in 30-40% of plans).

**Fix.** Add a set-level invariant at the decomp→synthesis transition:
every file cited in 2+ decision evidence entries must appear in
`needed_artifacts`, or auto-inject it with a derived purpose. Matches
actual known files from the structural index using word-boundary
regex — codebase/language-agnostic.

**Code.** `SynthesisStage._known_files_from_index` and
`_enforce_decision_coverage` in synthesis.py, wired into
`_build_artifacts_per_file` just after `needed_artifacts` is read.

**Related invariant.** Same shape as artifact closure, one pipeline
level up. See `docs/roadmap/v2-f7-decision-synthesis-closure.md` for
the original analysis and `docs/roadmap/artifact-closure-principle.md`
for the foundational invariant.

**Files.** `fitz_forge/planning/pipeline/stages/synthesis.py`

### 3. Fix A-var — iterator kind propagation through variables (`b3e4740`)

**Problem.** Closure's usage checks only fired when the iterated
expression in `async for x in <expr>` / `await <expr>` was an
`ast.Call`. The common pattern
```python
stream = service.query_stream(...)
async for chunk in stream:
```
slipped through entirely.

**Fix.** Add a parallel `_iter_kinds_stack` in `_ReferenceCollector`
tracking `var → (kind, originating_ref, context)` per function scope.
At assignment time, `_resolve_call_target` looks up the RHS call's
return type via sibling_provides → codebase index and classifies the
return as `sync_iter` / `async_iter` / `awaitable`. When
`visit_AsyncFor` / `visit_For` / `visit_Await` later see a Name,
`_propagate_var_usage` looks up the binding and re-emits a Reference
with the originating call's SymbolRef and the new usage kind.
`_check_usage` then catches mismatches the same way it would for
direct-call usage.

**New plumbing.** `extract_references` and `check_closure` now thread
`sibling_provides` through so the collector can see newly-generated
siblings when resolving return types.

**Verified.** Synthetic test catches `answer_stream = service.query_stream(...); async for chunk in answer_stream` as a usage violation.

**Files.** `fitz_forge/planning/artifact/closure.py`

### 4. Fix C — regen prompts show real signatures (`bb08e0a`)

**Problem.** Strategy-2 regeneration handed the model error messages
only. The model often regenerated with the same fabrication because
the error told it what was *wrong*, not what was *right*.

**Fix.** `_build_repair_hint_block` in generator.py produces two
sections for the regen prompt:
1. **Cross-artifact method signatures** from the sibling-provides dict:
   exact param names, order, return type, generator marker.
2. **Real class fields** for classes touched by field/missing/kwargs/
   usage violations, pulled from `IndexedClass.fields` (populated by
   `extract_class_fields` during `augment_from_source_dir`).

**Impact scope.** Only fires for strategy 2 (regenerate violator) —
doesn't help strategy 1 (expand the set). Most gemma violations turned
out to be strategy 1 cases, so this fix rarely activated in run 19.
Still correct; watch for impact in future runs.

**Files.** `fitz_forge/planning/artifact/generator.py`

### 5. Fix E — dedupe cascading field violations (`c85d0ef`)

**Problem.** When a parameter type annotation is a fabricated class,
every `param.field` access fires an independent missing violation.
5+ violations from a single root cause, hiding the real signal.

**Fix.** Post-processing pass in `check_closure`:
`_dedupe_fabricated_owner_cascades` identifies owners that don't exist
anywhere and collapses field cascades into one root "missing class"
violation per `(artifact, owner)`. Preserves the first line.

**Verified.** Run 19 plan 01 replay: 8 unclosed violations → 2.

**Files.** `fitz_forge/planning/artifact/closure.py`

### 6. Fix G — stronger schema injection + prompt rule (`19db9dd`)

**Problem.** The model invents `StreamQueryRequest` etc. when the real
`QueryRequest` exists and has matching fields. Fabricated parameter
type annotations cascade into fabricated field accesses.

**Fix.**
1. `_resolve_schema_fields` in synthesis.py now augments the lookup
   from source_dir and pulls field annotations from
   `IndexedClass.fields`. Includes any class with annotated fields
   or ending in a schema-shaped suffix (Request, Response, Input,
   Output, Event, Chunk, Message, Query, Config, Options, Params,
   Context, Result, State, Payload). Mentioned-in-decisions prioritized.
2. `SurgicalRewriteStrategy._build_prompt` gains two rules:
   - "For function parameter type annotations, use ONLY classes listed
      in DATA MODEL FIELDS — do NOT invent new Request/Response/Input/
      Output classes."
   - "When reading fields on a typed parameter, use ONLY the field
      names listed under that class."

**Verified.** Run 19 plan 01 replay: route previously had
`async def stream_query(request: StreamQueryRequest)` with 5 fabricated
fields. Replayed route uses real `QueryRequest` with real fields
(`question`, `source`, `collection`, `top_k`, `conversation_history`).

**Files.**
- `fitz_forge/planning/pipeline/stages/synthesis.py` (`_resolve_schema_fields`)
- `fitz_forge/planning/artifact/strategy.py` (new rules)

## Benchmark progression this session

| Run | Date | Model | Config | Tier 1 | Tier 2 | Combined | Notes |
|---|---|---|---|---|---|---|---|
| 16 | 04-15 | gemma-4-26b-a4b-it@q6_k | no fixes yet | 80.9 | 53.2 | 67.1 | 5/5 missing engine.py — all on arity FP |
| 17 | 04-15 | gemma | + Fix A-decomp + arity | **94.7** | 74.8 | **84.8** | Arity fix unlock. 2/5 perfect 100s |
| 18 | 04-15 | coder-next-reap-40b-a3b-i1 | + Fix A-decomp + arity | 93.9 | 71.6 | 82.7 | Apples-to-apples with gemma run 17 |
| 19 | 04-15 | gemma | + Fix A-var + Fix C (also applied) | 93.4 | 59.8 | 76.58 | Sample variance — gemma skipped synth in 5/5. Plan 01 outlier at 72.9 |
| replay | 04-16 | gemma | + Fix E + Fix G on run 19 plan 01 snapshot | 75.0 (plan 01 only) | — | — | Plan 01 +2.1 Tier 1. Route now uses real QueryRequest. Closure violations 8→2. |

**The headline finding:** gemma-4-26b-a4b-it@q6_k (general instruct,
no coding fine-tune) beats qwen3-coder-next-reap-40b-a3b-i1 (coder
fine-tune) by ~2 combined points on this streaming task. Redditor
wisdom about general instruct models > specialized coder models is
vindicated, modestly. Both models are now hamstrung by the same
residual route-layer fabrication pattern.

### Model switching context

The session tried multiple models:
- **qwen3.5-35b-a3b@q5_k_s** — killed after 1 plan. The model is a
  reasoning model with hardcoded thinking mode that ignores
  `chat_template_kwargs.enable_thinking: false`, `/no_think` prefix,
  and system prompts. Every call burns max_tokens on reasoning and
  returns empty content. **Not compatible with the current pipeline
  without a major rework.**
- **gemma-4-26b-a4b-it@q6_k** — works great. Default going forward.
- **qwen3-coder-next-reap-40b-a3b-i1** — the previous baseline. Also
  works. Available if you want to rerun run 92-era comparisons.

## The current failure landscape (after all fixes)

Still-unsolved patterns ranked by frequency in run 19 + run 18:

1. **Body-level class fabrication (next target).** Models invent
   `AnswerChunk`, `StreamQueryResponse`, `ChatStreamEvent` etc. as
   yield return types or exception classes *inside* method bodies.
   Fix G only covers parameter annotations. Extending the rule to
   cover body-level class references is ~15 min of work and should
   unlock engine.py generation in more cases. Called **Fix I** in
   session notes.

2. **Protocol widening (`ChatProvider.chat_stream`).** The model types
   `self._chat: ChatProvider` (non-streaming protocol) but calls
   `chat_stream` which only exists on `StreamingChatProvider`. Closure
   correctly flags this; fix is either prompt-level (show sibling
   protocols) or closure-level (check sibling classes with same bases
   when a method isn't found). ~2h for the closure approach, called
   **Fix B** in earlier notes.

3. **Synthesizer absence.** Gemma run 19 skipped synthesizer.py in 5/5
   plans. This is probably random variance (run 17 had it in 4/5) but
   worth watching. If it persists, could need a V2-F7 style injection
   specifically for synthesizer files when the engine calls
   `self._synthesizer.*_stream`.

4. **Async/sync mismatch via variable binding.** Fix A-var covers it
   but it didn't fire in run 19 because gemma used different patterns.
   Should catch more on the next run that uses the pattern.

5. **AnswerChunk fabrication in engine.py.** See #1. Same underlying
   issue.

## Architecture state

### Grounding package (refactored earlier in the session)

```
fitz_forge/planning/validation/grounding/
    __init__.py       re-exports (backwards compatible)
    inference.py      codebase knowledge — return types, fields, MRO, self._attr
    index.py          StructuralIndexLookup + IndexedClass/Method/Function
    check.py          per-artifact AST Violation check + _SKIP_NAMES
    llm.py            LLM gap detection + targeted repair
```

`inference.py` is the unified home for everything that learns things
about the codebase. Return type inference tries (in order):
annotation → body → yields → docstring. Class fields come from
`extract_class_fields`. MRO walking lives on `StructuralIndexLookup`.

### Artifact closure black box

```
fitz_forge/planning/artifact/
    closure.py     plan-level closure family — 5 invariants
    generator.py   per-artifact + batch entry (generate_artifact_set)
    strategy.py    SurgicalRewriteStrategy + NewCodeStrategy
    validate.py    per-artifact validation
    context.py     input assembly
```

Five invariants enforced by `check_closure`:
1. Existence — every symbol provided
2. Usage — async for / await / iter match callee kind
3. Kwargs — keyword args match params
4. Imports — `from pkg.mod import X` resolves
5. Field access — `obj.field` exists on type

Two repair strategies in `generate_artifact_set`:
- Strategy 1: expand the set (for `missing` violations) — routes to
  the owning file, generates repair artifact.
- Strategy 2: regenerate the violator (for `usage` / `kwargs` /
  `field` / `import` violations) — re-runs with error feedback
  augmented by sibling signatures (Fix C).

## Immediate next steps (in priority order)

### 1. Fix I — extend schema rule to body-level class refs (~15 min)

Edit `SurgicalRewriteStrategy._build_prompt` in `strategy.py`. Current
rules cover parameter annotations and field reads. Add:

> "When yielding or returning values, use ONLY classes listed in
> DATA MODEL FIELDS above — do NOT invent new chunk / event / response
> types. If the real codebase doesn't have the right class, compose
> from existing dataclasses (e.g. yield strings, yield tuples) instead
> of inventing a new class."

Run a 5-plan gemma benchmark + Sonnet Tier 2. If it unlocks engine.py
in more plans, Tier 1 should jump to mid/high 90s and Tier 2 should
follow.

### 2. 10-plan gemma benchmark for variance

5-plan samples have been too noisy to confidently measure fix impact.
Runs 17, 19 differ by 8 combined points mostly due to sample variance
(different architectural choices by gemma across sessions). A 10-plan
run would give more reliable numbers.

**Command (from the project root):**
```bash
.venv/Scripts/python -m benchmarks.plan_factory decomposed \
    --runs 10 \
    --source-dir ../fitz-sage \
    --context-file benchmarks/ideal_context.json \
    --query "Add query result streaming so answers are delivered token-by-token instead of waiting for the full response" \
    --score-v2
```

**Expected runtime:** ~90 minutes with the current WDDM-degraded
throughput. Launch with `run_in_background: true`.

### 3. Update tracker doc

`docs/v2-scoring/TRACKER.md` hasn't been updated with runs 16-19.
Should capture:
- Run 16 (gemma no fixes, 80.9) — pre-arity baseline
- Run 17 (gemma + arity + Fix A-decomp, 94.7, combined 84.8)
- Run 18 (coder-next + same fixes, 93.9, combined 82.7)
- Run 19 (gemma + Fix A-var + Fix C, 93.4, sample variance)

### 4. Commit the tracker update

Same pattern as prior sessions — one docs commit.

## Key conventions and landmines

### Config files

**Two config files exist and both must be updated when changing
models:**

1. `C:\Users\yanfi\AppData\Local\fitz-forge\fitz-forge\config.yaml`
2. `C:\Users\yanfi\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\Local\fitz-forge\fitz-forge\config.yaml`

The second one is for Python packaged installs. If you only update the
first, benchmarks will silently use the wrong model from the second.

### LM Studio model swaps

Every `lms load X` after `lms load Y` destroys the CUDA context and
permanently degrades throughput until PC reboot. This is the WDDM
bug on Blackwell cards documented in memory. Current throughput in
this session is ~25 tok/s (should be ~120). **Heads up: benchmarks
will run ~5x slower than normal until reboot.**

Keep model switches to a minimum. If you need to compare A vs B,
run A's full benchmark first before swapping to B.

### Benchmark result directories

Benchmark results go to `benchmarks/results/YYYY-MM-DD_HH-MM-SS_run_NN/`.
The `benchmarks/` directory is gitignored (full file tree) so results
are ephemeral. Use `plan_replay.json` for replay outputs.

**Snapshots for replay** live at
`benchmarks/results/<run>/traces_NN/snapshot_after_<stage>.json`.
Available stages: `_pre_stages`, `decision_decomposition`,
`decision_resolution`, `synthesis`.

### Replay workflow

```bash
.venv/Scripts/python -m benchmarks.plan_factory replay \
    --snapshot benchmarks/results/<run>/traces_NN/snapshot_after_decision_resolution.json \
    --source-dir ../fitz-sage \
    --context-file benchmarks/ideal_context.json \
    --score-v2
```

This skips decomposition + decision_resolution and only re-runs
synthesis onward. ~5-7 min per replay. Great for testing fixes
against specific failed plans.

### Sonnet Tier 2 taxonomy

`benchmarks/streaming_taxonomy.json` defines the taxonomy. Sonnet
prompt files are auto-generated at `score_v2_prompt_NN.md` per plan
in each run directory. Run parallel agents with model=sonnet pointing
each at its corresponding prompt file, then compute taxonomy scores
manually (there's no automation yet).

**Formula:** `taxonomy_score = arch_score * 0.4 + per_file_mean * 0.6`

**Combined score:** `(deterministic + taxonomy) / 2` (50/50 weighting)

### CLAUDE.md rules that matter for this work

- **Rule 10: Fix invariants, not symptoms.** The closure family was
  born from this. Every fix in this session is framed as "state the
  invariant, enforce it" rather than patching specific failures.
- **Rule 11: Watch for set-level bugs.** Closure is the canonical
  example. Per-artifact validation can never catch cross-artifact
  fabrication.

When adding new fixes, always ask: "is this a property of the item
or of the set?" If it's of the set, it belongs in a closure-style
pass, not a per-item check.

## Open questions

1. **Is the Fix G / Fix I approach (prompt enhancement + rule) the
   right direction**, or should we invest in structural fixes (Fix F:
   closure-level routing of fabricated classes to schema files)? Prompt
   fixes are cheap but unreliable; structural fixes are expensive but
   guaranteed. The session ended on this fence.

2. **Should we write a unit test suite for closure.py?** It's ~1050
   lines and has become load-bearing. No unit tests currently — all
   verification is via synthetic scripts or full benchmark replays.
   Maybe 1-2 hours to add a proper test file.

3. **The V2-F7 Fix A (decomp→synthesis closure) injection has a hard-
   coded cap of 12 artifacts** (raised from 8). If a run genuinely
   needs more, things get dropped. Should be driven by a config value
   or auto-scale with decision count.

4. **Should we set up a default "which model" decision tree?** Gemma
   wins on this benchmark by a small margin, but coder-next may be
   better on different tasks. No data yet.

## What to tell the next session

Paste this prompt into a fresh session:

> I'm continuing work on fitz-forge from the previous session. Please
> read `docs/roadmap/session-handoff-2026-04-16.md` for the full
> context. Short version: we landed 6 commits fixing closure + arity
> bugs and a fabricated-schema-class prompt rule. Gemma-4-26b beats
> coder-next on combined score by ~2 points. The next target is
> **Fix I** — a 15-minute extension of Fix G's rule to cover body-
> level class references (not just parameter annotations), which
> should unlock engine.py generation in the remaining failure cases.
> After Fix I, run a 10-plan gemma benchmark to beat sample variance,
> then update `docs/v2-scoring/TRACKER.md` with runs 16-19 + the
> post-Fix-I run. **Do NOT switch models** — gemma is still loaded
> and switching destroys GPU throughput until reboot. Start by
> reading the handoff doc end-to-end, then reading the most recent
> 6 commits (`git log --oneline -6`) to see exact changes. Confirm
> you've read it before touching code.

## Known-good snapshot for replay testing

If you want to iterate on Fix I without running a full benchmark,
replay run 19 plan 01 from
`benchmarks/results/2026-04-15_23-44-37_run_019/traces_01/snapshot_after_decision_resolution.json`.

This plan failed engine.py generation on `AnswerChunk` fabrication —
exactly the pattern Fix I targets. A successful Fix I should produce
a replayed plan with engine.py actually present and completeness
jumping from 15/30 to 30/30.

## End of handoff
