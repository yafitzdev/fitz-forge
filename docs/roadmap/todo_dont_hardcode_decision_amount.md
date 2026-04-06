  1) Dynamic decision count — Agreed. The 8-18 hardcoded range is wrong. A "rename a variable" task needs 2 decisions. A
  "rewrite the auth system" needs 25. The scorer shouldn't penalize based on absolute count. I'll save this for later.

---


● This is incredibly revealing. Look at the massive redundancy:

  - d1, d6, d10, d12 all say the same thing: chat_stream() returns Iterator[str], existing chat() unchanged. The same
  constraint is repeated in 4 separate decisions.
  - d2, d3, d4, d5 all repeat: answer() signature must remain unchanged, Answer is a static dataclass.
  - The evidence sections repeat the same method signatures across multiple decisions.

  These 8 decisions could collapse into 2-3:
  1. "Provider streaming contract: chat_stream() -> Iterator[str], chat() unchanged"
  2. "Engine + route: add parallel answer_stream() and /stream_query, existing methods frozen"
  3. "Answer is static, streaming must be a new API"

  That would cut decisions from ~17K to maybe ~5K — a 70% reduction.

  To answer your questions:

  1. Yes, prefer fewer decisions. We can add a penalty in the scorer for decision count above 10. Currently 8-18 get equal
   score. Making 8-10 score higher than 13-15 would select more compact decompositions.
  2. Yes, decisions are highly collapsible. The decomposition produces one question per nuance, but many nuances are about
   the same architectural decision. A post-decomposition collapse step could merge decisions that share >50% of their
  relevant_files and have overlapping constraints.

  Which do you want to try first — scoring penalty for more decisions, or a collapse/merge step?

✻ Sautéed for 59s

> several thoughts:
1) everything is dynamic! we cannot force it to use between 8-18 decisions, what if the plan realistically only requires
3 decisions? or what if it really needs 20 decisions? we shouldnt hard code or prefer a set amount of decisions. it
differs for each task. now this is something that we should remember for later for changing.
2) when in doubt, we actually should require the llm to force more decisions rather than fewer: as we saw in your
example, duplicate decisions are possible. that means we can remove duplicates and get the real amount of relevant
decisions. so we need to implement a new stage that checks the decisions made, deduplicates them semantically etc.this is
 the stage that will reduce and optimize everything downstream.
3) now is point 2 the collapser? probably not, probably the collapser should run just before we assemble the synth
prompt? this is up for debate.

● Sat Apr 4 16:08:49 WEDT 2026

  Great thinking. Let me respond to each:

  1) Dynamic decision count — Agreed. The 8-18 hardcoded range is wrong. A "rename a variable" task needs 2 decisions. A
  "rewrite the auth system" needs 25. The scorer shouldn't penalize based on absolute count. I'll save this for later.

  2) Over-generate then deduplicate — This is the right pattern. Same as best-of-3 reasoning: generate MORE than you need,
   then filter. The current F1 dedup only catches string-similar questions (SequenceMatcher >= 0.85). A semantic dedup
  would catch the d1/d6/d10/d12 case where 4 decisions all say "chat_stream returns Iterator[str]" in different words.

  3) Collapser placement — Two distinct stages:

  - Decision dedup/merge (after resolution, before synthesis): Merge decisions that resolved to the same conclusion.
  d1+d6+d10+d12 all concluded "chat_stream → Iterator[str]" — merge into one decision with combined evidence and combined
  constraints. This reduces the number of entries in the synth prompt.
  - Prompt assembly (right before synthesis): Take the merged decisions and format them compactly. This is where
  _format_resolutions lives today.

  The dedup/merge is the higher-value step because it reduces both the synth prompt AND the reasoning output (fewer
  decisions = fewer sections to write about = shorter output = better attention).

  I think the right order is:
  1. Resolution produces N decisions (currently ~13)
  2. New: semantic merge reduces to M decisions (maybe ~6-8)
  3. Merged decisions go into synth prompt
  4. Shorter prompt + shorter output = less fabrication

  Should I implement the semantic merge step? It would be an LLM call — give the model all resolved decisions and ask it
  to merge redundant ones, keeping all constraints and evidence from the merged set.

  Or we could do it deterministically: cluster decisions by relevant_files overlap + constraint text similarity, then pick
   one representative per cluster and union their constraints.