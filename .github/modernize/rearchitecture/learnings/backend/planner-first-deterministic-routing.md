# Planner First Deterministic Routing

Canonical planner wrappers should preserve dict compatibility while adding deterministic route execution before legacy fallback.

## What Happened
In c90d1ef9 (1) task t1, planner-first execution was integrated into /nl-query and /query using a canonical QueryPlan wrapper and explicit deterministic routing helpers in intents.py. The endpoint logic now attempts planner execution first, then falls back to existing rule-based and LLM behavior.

## Takeaway
When introducing planner-first execution in this codebase, keep build_query_plan returning dict for compatibility, add structured wrappers separately, and place planner execution before existing fallback branches to avoid response-shape regressions.

## History
- 2026-06-11 (c90d1ef9 (1)/t1): initial
