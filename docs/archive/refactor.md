● 716 passed (up from the original 388). The one failure is the pre-existing test_truncated_json_repaired which was
  failing before too.

  We're back to v0.5.0 source with the new tests. Ready to bisect the refactor changes one at a time. The refactor
  had these categories of changes:

  1. Constants extraction — magic numbers → named constants (e.g., 4096 → _EXTRACTION_MAX_TOKENS)
  2. Exception narrowing — except Exception → except (ValueError, TypeError) etc.
  3. _build_closing_suffix — LIFO JSON repair replacing count-based closing
  4. exc_info=True — added to warning logs for stack traces
  5. ~~Deleted stub files — architecture.py, design.py, risk.py, roadmap.py (raise ImportError stubs)~~