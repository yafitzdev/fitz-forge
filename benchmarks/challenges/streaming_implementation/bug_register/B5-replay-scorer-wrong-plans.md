# B5 — Replay scorer wrong plans

**Status:** resolved
**Impact:** 4/10
**Closed:** 2026-04-16

**Evidence:** Replay logs reported `Scorer V2: 5 plans, avg 93.4/100` — which matched the original run 19 Tier 1. Replay writes `plan_replay.json` but the scorer iterated `plan_01.json..plan_05.json` in the run dir, ignoring the replay output. Every cycle required manual scoring.

**Fix:** `benchmarks/plan_factory.py:replay_cmd`. After the batch scoring, the command loads `plan_replay.json` and prints a dedicated `=== REPLAY DETERMINISTIC SCORE ===` block.
