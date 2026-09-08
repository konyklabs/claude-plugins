---
type: regex
target: last_message
pattern: "(ignore_changes|track_latest)"
---
The most-missed class in the baseline: a pipeline owns task_definition and autoscaling owns desired_count, so the next apply rolls both back. The review must report it, at blocking severity, with the rollback as the failure scenario, and name ignore_changes or track_latest as the fix.
