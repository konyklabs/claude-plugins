---
type: llm
target: trace
---
Score 1 if the assistant runs (or delegates to prod-readiness:scanner) the readiness.py scan and reasons from its table, delegating review rows to the auditor, rather than reading source files one by one for security issues. Score 0 if it starts a manual file-by-file review.
