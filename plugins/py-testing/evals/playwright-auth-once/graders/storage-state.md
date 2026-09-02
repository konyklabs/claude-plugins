---
type: regex
target: last_message
pattern: "storage_state"
---
The answer logs in once per run and reuses storage_state through browser_context_args instead of logging in per test.
