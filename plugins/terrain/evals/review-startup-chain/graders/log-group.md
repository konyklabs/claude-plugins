---
type: regex
target: last_message
pattern: "(?i)(log group|CreateLogGroup|awslogs)"
---
The awslogs group /ecs/app-<env> is never created and the managed execution policy cannot create it; the task fails to start. This is item 2 of the checklist (the start-up chain) and must appear as a blocking finding.
