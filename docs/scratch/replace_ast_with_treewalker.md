> question: is most of my pipeline language agnostic?

● Mon Apr  6 00:48:26 WEDT 2026

  Let me check.

  ┌─────────────────────┬────────────────────────────────────────────────────────────────────────┬────────────────────┐
  │   Pipeline stage    │                           Language-agnostic?                           │        Why         │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Agent context       │ Mostly yes — BM25, embeddings, reranking work on any text. But         │ Python AST for     │
  │ gathering           │ build_structural_index uses Python AST                                 │ indexing           │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Decision            │ Yes — LLM generates questions from text                                │ Pure LLM           │
  │ decomposition       │                                                                        │                    │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Decision resolution │ Yes — LLM resolves questions from text                                 │ Pure LLM           │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Synthesis reasoning │ Yes — LLM writes plan from decisions                                   │ Pure LLM           │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Field extraction    │ Yes — LLM extracts JSON from text                                      │ Pure LLM           │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Per-artifact        │ Yes — LLM generates code                                               │ Pure LLM           │
  │ generation          │                                                                        │                    │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Post-gen repair     │ NO — Python AST everywhere                                             │ Python-only        │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Grounding           │ NO — Python AST                                                        │ Python-only        │
  │ validation          │                                                                        │                    │
  ├─────────────────────┼────────────────────────────────────────────────────────────────────────┼────────────────────┤
  │ Cross-stage         │ Yes — LLM check                                                        │ Pure LLM           │
  │ coherence           │                                                                        │                    │
  └─────────────────────┴────────────────────────────────────────────────────────────────────────┴────────────────────┘

  The pipeline is ~80% language-agnostic (LLM stages). The Python-specific parts are:

  1. Structural index — ast.parse() for class/function extraction
  2. All repair functions — _repair_fabricated_refs, _detect_fabricated_calls, _repair_fabricated_calls
  3. All grounding functions — _resolve_class_interfaces, _resolve_imported_type_apis, _resolve_schema_fields,
  _extract_reference_method, _build_type_attr_map
  4. Import graph — _extract_full_imports
  5. Class cache — Python AST parsing

  These are all in the artifact generation + repair layer. The core planning pipeline (decompose → resolve → synthesize →
  extract) is pure LLM and works on any language.

  So: the planning works on any language. The artifact grounding and repair is Python-only. The embed_batch fix is fine —
  it's in the Python-only repair layer that already assumes Python everywhere.

> what do we need to do to make it truly language agnostic?

● Mon Apr  6 00:49:20 WEDT 2026

  Replace Python AST with tree-sitter. That's it.

  Tree-sitter has parsers for 100+ languages with the same API. Every place we call ast.parse() would call
  tree_sitter.parse() instead. The queries change (Python ClassDef → Rust struct_item, TypeScript class_declaration) but
  the logic is identical: find classes, extract methods, resolve types.

  The structural index, class cache, repair pipeline, grounding functions — all of them do the same thing: "find class X,
  list its methods, check if method Y exists." Tree-sitter does this for any language.

  But that's a big refactor — touching every function in the Python-only layer. Not something to do right now in the
  middle of benchmarking. Write it up as a roadmap item?