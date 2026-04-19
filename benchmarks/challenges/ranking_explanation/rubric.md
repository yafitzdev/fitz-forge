# Quality criteria — ranking_explanation

These are domain-level expectations that the model cannot infer from the
task prompt or the codebase alone. They capture what "a high-quality
ranking-explanation implementation" means for downstream consumers:
API clients need to see *why* each source was ranked where it was, not
just *that* it was returned.

## Signal preservation through the pipeline

The plan MUST preserve ranking signals from producer to consumer
without collapsing them into a single opaque number.

- **Ranker (`CrossStrategyRanker` / `_compute_score`).** When the ranker
  computes a composite score from multiple signals (base embedding /
  BM25 score, strategy weight, entity bonus, keyword boost), it MUST
  record each signal *separately* in `Address.metadata` alongside the
  final composite. Storing only the composite loses the breakdown —
  downstream code cannot reconstruct which signal dominated.
  Required keys (or equivalent names): `base_score`, `strategy_weight`,
  `entity_bonus`, `keyword_boost`, `composite_score`.
- **Reranker (`AddressReranker`).** Before overwriting `addr.score`
  with the cross-encoder result, save the original under
  `metadata['pre_rerank_score']` so pre-rerank rank is recoverable.
  Record the new score under `metadata['rerank_score']`. Overwriting
  `addr.score` without preserving the baseline discards the most
  important diagnostic: did the rerank actually move this source?
- **Retrieval method tag.** Record `metadata['retrieval_method']` with
  the strategy class name (e.g. `"DenseRetrievalStrategy"`) so
  consumers can see which retrieval path produced each source.

## Propagation through the synthesizer

The synthesizer's `_build_provenance` MUST copy the ranking metadata
from each `Address` into `Provenance.metadata` (either as a nested
`ranking` dict or as typed fields). Dropping the metadata at this
boundary means the API layer has nothing to surface to clients,
regardless of how carefully the ranker and reranker recorded it.

## Exposure at the API boundary

The route handler SHOULD map `Provenance.metadata` ranking fields onto
typed response fields (e.g. `SourceInfo.base_score`,
`SourceInfo.rerank_score`, `SourceInfo.retrieval_method`) rather than
returning a bare dict. Typed fields are self-documenting and survive
schema evolution; a generic dict does not.

## Engine orchestration

The engine's `answer()` is responsible for *ensuring* the ranker and
reranker actually populate these metadata fields before addresses
reach the synthesizer. If the existing ranker/reranker do not record
the signals, the engine must extend them (via a new method or a
wrapping call) — passing addresses through untouched is a silent
failure mode where the pipeline *appears* to support explanations
while stripping them.

## Anti-patterns to avoid

- Adding a post-hoc LLM call that "explains" rankings without access
  to the actual scores — the model will fabricate plausible-sounding
  reasons disconnected from real signals.
- Storing a single aggregate label (`"retrieved by code search"`) on
  the final `Answer.metadata` instead of per-source breakdowns.
- Replacing ranker/reranker return types with a new wrapper dict
  (e.g. `list[{"address": addr, "score": x}]`) — downstream code
  expects to receive `Address` instances with enriched `.metadata`,
  not wrapped structures.
