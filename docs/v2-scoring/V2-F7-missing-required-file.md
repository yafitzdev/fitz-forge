# V2-F7: Missing Required File

**Occurrence:** 4/9 plans (run 85), 1/10 (run 83), 0/7 (run 84)
**Impact:** -15 to -30 pts (completeness drops from 30/30 to 0-15/30)

## What Happens

The plan doesn't produce an artifact for engine.py (or routes/query.py). The taxonomy defines these as required because streaming needs both the engine-level `answer_stream()` AND the API endpoint.

## 5-Why Root Cause

1. **Why is engine.py missing?** — `needed_artifacts` (extracted by synthesis LLM) doesn't list it
2. **Why doesn't the LLM list it?** — It chose an adapter/wrapper architecture instead of modifying engine.py
3. **Why adapters?** — The model sees engine.py's `answer()` (300+ lines) as too complex to modify. Creating `streaming.py` or `service.py` wrappers is "safer"
4. **Why is this allowed?** — The synthesis extraction has no constraint that decision-referenced files must appear in needed_artifacts
5. **Root cause:** Decisions analyze engine.py → synthesis discards that and proposes a different architecture → no engine.py artifact → no surgical rewrite fires → shortcut pattern

## Architectures Chosen by Missing-Engine Plans

| Plan | Architecture | Pattern |
|------|-------------|---------|
| 85/plan_01 | Provider-level streaming (llm/client.py, factory.py) | A3 shortcut |
| 85/plan_02 | New adapter file (engines/fitz_krag/streaming.py) | Composition |
| 85/plan_04 | Complete failure (0 artifacts) | Pipeline error |
| 85/plan_08 | Service-level streaming (service.py) | A3 shortcut |

## Fix

**Deterministic post-extraction check in synthesis:** After `needed_artifacts` is extracted, compute which files are referenced in 2+ decisions but absent from needed_artifacts. Inject them with purpose derived from the decisions.

This is codebase-agnostic — it uses the decision references, not hardcoded filenames. It ensures the synthesis doesn't discard the analysis work from the decomposition stage.
