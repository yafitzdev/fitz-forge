# Floor Plan Fixes

Identified from 10-plan benchmark (2026-04-03, run 60). Mean: 40.1/60, Floor: 30, Ceiling: 49.

## A. Engine.py artifact fabricates methods (HIGHEST IMPACT)

**Problem:** The model reimagines engine.py from scratch instead of adapting the existing `answer()` method. It invents methods like `_governor.available()`, `_query_analyzer.analyze()`, `c.evaluate()`, `AnswerMode.CONCISE` — none of which exist.

**Root cause:** The artifact prompt buries the interface injection ("AVAILABLE METHODS ON INSTANCE ATTRS") after 10K+ chars of reasoning. The model reads reasoning + source code, then starts generating before paying attention to the interface list. Classic "lost in the middle" effect.

Current prompt order:
```
1. "Write artifact for: engine.py" + purpose
2. RELEVANT DECISIONS
3. PLAN CONTEXT (reasoning — 10K+ chars)   ← model reads this first
4. SOURCE CODE                              ← model reads this
5. AVAILABLE METHODS ON INSTANCE ATTRS      ← BURIED, model ignores
6. JSON schema + Rules
```

**Fix 1 — Prompt reordering:** Move interface section and source code BEFORE reasoning. The model sees grounding data first, then reasoning context. Reasoning is the lowest-priority section (model already made all decisions upstream — reasoning is just narrative).

**Fix 2 — Post-generation method validation:** Extend existing `_repair_fabricated_refs()` to also validate method calls on known attrs. We already have the interface map (`_governor → GovernanceDecider: decide(...)`). Parse every `self._attr.method()` call in the artifact, check if `method` is in the valid methods for `_attr`. If not, fuzzy-match against the valid methods for that attr.

**Affected files:**
- `synthesis.py` — `_generate_single_artifact()` prompt assembly (fix 1)
- `synthesis.py` — `_repair_fabricated_refs()` method validation (fix 2)

---

## B. Empty roadmap phases

**Problem:** Extraction produces `total_phases: 5` but `phases: []`. The synthesis reasoning contains valid roadmap content, but the JSON extraction returns empty arrays.

**Fix:** After roadmap field group extraction, if `phases` is empty, retry that extraction group once. Simple and safe — worst case we get empty again and fall back to Pydantic defaults (same as current behavior).

**Affected files:**
- `synthesis.py` — `execute()`, after roadmap extraction loop

---

## C. FitzService artifact gap

**Problem:** 7/10 plans reference `FitzService.generate_stream()` in their design but never produce an artifact for it. The service layer file isn't in the gathered context so the model doesn't know how to write it.

**Fix:** Post-artifact check: compare `needed_artifacts` filenames against produced artifact filenames. If any `needed_artifact` has no matching produced artifact, log a warning. Optionally: generate a focused artifact for the missing file.

**Affected files:**
- `synthesis.py` — `_build_artifacts_per_file()`, after the loop

---

## D. Iterator[dict] vs Iterator[str]

**Problem:** The model sometimes returns dicts from streaming methods when the protocol specifies `Iterator[str]`. It writes `yield {"token": text, "type": "message"}` instead of `yield text`.

**Fix:** Add explicit rule to artifact prompt: "Streaming methods MUST return `Iterator[str]` with raw string tokens, not `Iterator[dict]` or structured objects." This is a prompt-only fix, no code change needed beyond the rules string.

**Affected files:**
- `synthesis.py` — `_generate_single_artifact()`, `rules` string

---

## E. Phantom phase references

**Problem:** `critical_path: [1,2,4]` references non-existent phases. `parallel_opportunities: [[3,5]]` references phase 5 that doesn't exist. The scheduling extraction runs independently from the phases extraction and doesn't know which phases were actually produced.

**Fix:** Deterministic post-extraction validation. After both `phases` and `scheduling` are extracted, filter `critical_path` and `parallel_opportunities` to only include phase numbers that exist in the `phases` array. Pure code fix, no LLM call needed.

**Affected files:**
- `synthesis.py` — `execute()`, after roadmap + risk extraction

---

## Testing Methodology

For each fix, use isolated prompt replay:

1. Run 3 full pipeline plans to capture the exact prompts and LLM responses
2. Find the prompts that produce the specific failure (e.g., engine.py artifact prompt that generates fabricated methods)
3. Replay that exact prompt 50 times → measure baseline failure rate
4. Implement fix (e.g., reorder prompt)
5. Replay the fixed prompt 50 times → measure new failure rate
6. Compare before/after

This requires full prompt provenance logging — every prompt sent to the LLM and every response received must be saved to disk per run.

## Priority Order

1. **A** — highest impact, directly attacks floor plans (30, 33)
2. **E** — trivial deterministic fix, no LLM cost
3. **B** — simple retry, small LLM cost
4. **D** — prompt rule addition, no LLM cost
5. **C** — detection + optional generation, medium complexity
