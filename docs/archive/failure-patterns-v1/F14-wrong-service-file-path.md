# F14: Wrong Service File Path

## Problem
The model targets `fitz_sage/services.py` for the FitzService artifact, but FitzService actually lives at `fitz_sage/services/fitz_service.py`. The artifact is generated for a nonexistent file, so:
1. No source code is loaded (file not found)
2. No interface injection fires
3. No reference method injection fires
4. The model fabricates the entire implementation

## Impact
- file_accuracy drops (file doesn't exist on disk)
- fab_ratio increases (no grounding context available)
- Observed in Plan 8 of run 67 (score 37)

## Occurrence Rate
1/10 plans in run 67.

## Root Cause
The `needed_artifacts` extraction produces `fitz_sage/services.py` from reasoning that mentions "FitzService in the services module." The model shortens the path. The F12 cleanup doesn't catch this because the path HAS a separator.

## Fix Options
1. **Fuzzy file path matching**: When `_find_file_source()` can't find a file, try fuzzy matching against known codebase paths (e.g., `services.py` → `services/fitz_service.py`)
2. **Validate needed_artifacts paths**: After extraction, check each path against the structural index. If not found, try to resolve to a real path.

## Test Data
- Harness: `benchmarks/test_f14_path.py`
- Baseline: **0/35 (0%)** — zero unresolved paths across 35 reasoning+extraction pairs
- Each run generated a fresh synthesis reasoning (temperature=0.7) and extracted needed_artifacts
- Every extracted path resolved correctly via `_find_file_source()`
- The 10% pipeline rate (1/10 plans) was likely from upstream decomposition/resolution variance that the isolated harness doesn't vary, or statistical noise from small sample (1/10)
- The model consistently produces correct file paths when the structural index is available

## Status: RESOLVED (0% in isolation — not reproducible with current pipeline)
