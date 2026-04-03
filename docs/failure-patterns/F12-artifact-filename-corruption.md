# F12: Artifact Filename Corruption

## Problem
Two filename corruption patterns in `needed_artifacts` extraction:

### Pattern A: Method suffix appended to filename
`fitz_sage/engines/fitz_krag/engine.py.answer_stream()` instead of `engine.py`

The model appends the method name to the file path. The artifact generator then creates files with nonsensical paths that don't exist on disk.

### Pattern B: Generic/invented filenames
`new_chat_stream_endpoint.py` instead of `fitz_sage/api/routes/query.py`

The model invents descriptive filenames instead of using real codebase paths. Creates standalone files disconnected from the codebase.

## Impact
- Pattern A: file_accuracy drops to 0% (path doesn't exist on disk), fabrication ratio inflates to 100% (structural index lookup fails)
- Pattern B: file_accuracy drops to 0%, artifacts are standalone rewrites instead of patches
- Sonnet scorer penalizes heavily on file identification and codebase alignment
- Observed: Plan 1 (fab=1.00, files=0%), Plan 4 (files=0%) in run 64

## Occurrence Rate
2/10 plans in run 64 (20%). Pattern A in 1 plan, Pattern B in 1 plan.

## Root Cause
The `needed_artifacts` field is extracted by LLM from synthesis reasoning. The model sometimes:
- A: Treats the entry as "file.method" format instead of "file -- purpose" format
- B: Invents descriptive filenames instead of using paths from the structural index

## Fix
Deterministic post-extraction cleanup in `_build_artifacts_per_file`:
1. Pattern A: Strip anything after `.py` in filenames (`re.sub(r'\.py\..+', '.py', filename)`)
2. Pattern B: If filename doesn't contain `/`, try to match it against the structural index by class/module name

## Affected Stage
`synthesis.py` → `_build_artifacts_per_file()`, filename parsing

## Status: NOT FIXED
