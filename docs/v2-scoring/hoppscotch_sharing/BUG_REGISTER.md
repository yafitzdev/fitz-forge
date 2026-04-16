# Hoppscotch Collection Sharing — Bug Register

Task: "Add collection sharing via public link so users can share a
read-only view of their API collections with anyone who has the link"

Codebase: hoppscotch/packages/hoppscotch-backend (TypeScript/NestJS)
Taxonomy: `benchmarks/hoppscotch_sharing/taxonomy.json`
Context: `benchmarks/hoppscotch_sharing/ideal_context.json`

**Note:** This is a TypeScript codebase. Artifact validation (Python AST)
will not work for TS files. Artifact quality scores reflect text heuristics
only. Completeness and architecture scoring are fully language-agnostic.

## Run 024 Baseline (10 plans)

| Metric | Value |
|--------|-------|
| Plans | 10 |
| Average | **71.86** |
| Min / Max | 20.0 / 95.0 |
| >=90 | 4 |
| <70 | 2 (plans 06, 09 at 20.0 — 0 artifacts) |
| Consistency | 20/20 on all 10 plans (perfect) |

## Open

### B1-hopp — 2/10 plans produce 0 artifacts (synthesis formatting failure)
- **Impact:** 10
- **Evidence:** Plans 06, 09 have 8 decomposition decisions, 8 resolutions,
  but `needed_artifacts: 0`. Synthesis reasoning (9K chars) doesn't include
  the JSON-structured file list. Fallback to template extraction also fails.
- **Root cause:** LLM output formatting variance — the model writes a long
  prose plan but omits the structured `needed_artifacts` section.
- **Generalization:** any task where synthesis reasoning omits the artifact
  list. Language-agnostic — same bug could hit Python tasks.
- **Fix:** when `needed_artifacts` is empty AND resolutions exist, fall back
  to extracting file paths from resolution evidence (same data source as
  evidence-source injection). This is a robustness improvement, not
  task-specific.

### B2-hopp — Artifacts are 1-line signatures instead of implementations
- **Impact:** 7
- **Evidence:** Most artifacts are single-line type signatures like
  `createCollectionShortcode(collectionId: string): Promise<E.Either<...>>`
  instead of full method bodies. The model interprets "write ONLY the new
  or modified code" as "write the method signature."
- **Generalization:** applies to any language where signatures and
  implementations are syntactically distinct. Language-agnostic fix:
  make the prompt explicitly request full implementations.

### B3-hopp — Completeness varies widely (0-30)
- **Impact:** 5
- **Evidence:** Plans 03, 07 have 0-15 completeness despite having resolved
  decisions. Missing schema.prisma or team-collection.service.ts.
- **Fix:** evidence-source injection (already deployed) should help on fresh
  runs. Verify on next benchmark.

## Resolved

*(none yet)*
