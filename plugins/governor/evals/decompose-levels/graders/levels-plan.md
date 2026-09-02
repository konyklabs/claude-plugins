---
type: llm
target: last_message
---
Score 1 if the plan has a level 0 containing the shared fixture/helper work (tests/_support/db.py and the root conftest) that lands first, later levels of independent slices that can run in parallel worktrees, one test command per slice, and a decisions section workers must not re-open. Score 0 if it is a flat to-do list or if it starts editing files.
