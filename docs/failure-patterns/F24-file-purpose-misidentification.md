# F24: Semantic File Purpose Misidentification

## Problem
The model misidentifies what a file does based on its name or a superficial read, then places new functionality in the wrong file. This is a semantic confusion — the model misunderstands the file's purpose, not just its path.

Note: Wrong file PATHS (typos, singular vs plural) are covered by F12 and F14.

## Examples from run 73
- Plan 73a: places `_run_query_stream()` in `fitz_sage/core/firstrun.py` — this file handles interactive first-run Ollama setup and config writing, has nothing to do with query orchestration. Model may have read "firstrun" as "first run of a query."
- Plan 73e: bypasses the synthesis layer entirely by calling `chat_stream()` directly from `answer_stream()`, skipping retrieval + guardrails + context assembly — because it misidentified the synthesis layer's role (related to F21 stub confusion).

## Occurrence
2/5 plans (40%) in run 73. Costs -1 to -3 pts on file_identification and alignment.

## Root Cause
The structural overview provides module docstrings and class/function signatures. But the model sometimes infers purpose from the filename alone rather than reading the docstring. `firstrun.py` has docstring "Interactive first-run config setup" but the model placed query orchestration there anyway.

This pattern is amplified by F21 (stub confusion): if the model thinks the correct file's methods are stubs, it looks for an alternative file and picks one based on name alone.

## Potential Fixes
1. **Module purpose annotations in context**: Add explicit `[PURPOSE: first-run Ollama setup, NOT query execution]` markers for commonly-confused files. Cost: 0 LLM calls, prompt change.
2. **Artifact path validation**: After extraction, validate that every file in `needed_artifacts` exists in the structural index and that its docstring is semantically compatible with the proposed changes. Cost: 0 for path check, 1 LLM call for semantic check.

## Status: ❌ Not yet fixed
