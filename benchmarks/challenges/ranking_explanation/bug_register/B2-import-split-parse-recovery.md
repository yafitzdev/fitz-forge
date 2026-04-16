# B2 — Import split parse recovery

**Status:** resolved
**Impact:** 7/10
**Closed:** 2026-04-16

**Evidence:** After B1 started injecting ranker.py and reranker.py, the new_code strategy produced outputs like `import dataclasses\n\n    def rank(...)` — a module-level import at indent 0 followed by an indented method body. Neither raw parse, dedent, nor class-wrap handled this hybrid shape.

**Fix:** `inference.py:try_parse` 4th recovery step. When top-level imports precede indented method bodies, split the imports and class-wrap only the body, then prepend the imports.
