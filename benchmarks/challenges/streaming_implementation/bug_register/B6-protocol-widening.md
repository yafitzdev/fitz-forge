# B6 — Protocol widening

**Status:** resolved
**Impact:** 8/10
**Closed:** 2026-04-16

**Evidence:** Run 20 — 4/5 plans hit this exact pattern. Model types `self._chat` as the base protocol `ChatProvider` (which has `chat`) but the new streaming method calls `chat_stream` which only exists on the subclass `StreamingChatProvider`. Closure flags `ChatProvider.chat_stream — missing`.

**Generalization:** the invariant "`obj.method` must exist on the declared type of `obj`" is too strict when the declared type is a Protocol. Protocols are structural: any object with the right methods satisfies them.

**Fix:** `closure.py:_owner_is_protocol` + `_method_exists_anywhere` + updated `_ref_in_codebase`. When the declared owner is a `Protocol` and the method exists on any class in the codebase, the call is accepted as protocol widening / duck-typing.
