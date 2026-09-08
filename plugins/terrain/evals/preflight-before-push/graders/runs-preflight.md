---
type: regex
target: transcript
pattern: "(preflight\\.py|hcl_checks\\.py)"
---
The deterministic checks must run before any opinion is offered: the transcript contains an invocation of preflight.py or hcl_checks.py from the preflighting-tofu skill, not a read-and-reason pass alone.
