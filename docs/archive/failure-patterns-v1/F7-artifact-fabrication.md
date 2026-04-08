# F7: Artifact Method Fabrication (FIXED)

## Problem
Engine.py artifacts invented method names: `_governor.available()`, `_query_analyzer.analyze()`, `c.evaluate()`, `AnswerMode.CONCISE`, `hook.on_stream_token()`, etc.

## Impact
- Generated code would crash with AttributeError at runtime
- Was the #1 contributor to floor plans (scores 30-33)

## Occurrence Rate
**Before fix:** 62% of artifacts had fabrications (19/50 clean = 38% clean)
**After fix:** 2% of artifacts had fabrications (49/50 clean = 98% clean)

## Root Cause
Interface injection ("AVAILABLE METHODS ON INSTANCE ATTRS") was buried after 10K+ chars of reasoning in the artifact prompt. Model ignored it due to lost-in-the-middle effect.

## Fix (IMPLEMENTED)
Prompt reorder: moved source code, schema fields, and interface section BEFORE reasoning context. Reasoning demoted to "background — lower priority."

Commit: `5ec3a0b2` (2026-04-03)

## Status: FIXED
