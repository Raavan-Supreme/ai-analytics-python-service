## [t1] Planner-first canonical routing integration for Python query endpoints
- Added a canonical QueryPlan dataclass wrapper while preserving dict-based plan compatibility.
- Added planner execution routing helpers to run deterministic routes before fallback flow.
- Wired planner-first execution into both /nl-query and /query without changing response schema.
- Kept chart behavior and trace/debug payload handling aligned with existing endpoint behavior.
- Validation: py_compile passed for both changed files; main.py diagnostics clean; intents.py has pre-existing broad typing diagnostics.
- Learnings consumed: [(none)]
