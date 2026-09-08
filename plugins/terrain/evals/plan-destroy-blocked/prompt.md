---
name: plan-destroy-blocked
tags: [terrain,plan]
runs: 1
max_turns: 8
timeout_seconds: 240
---
Here is tfplan.json from `tofu show -json` for our production stack. Is it safe to apply? The task was "bump the service memory to 2 GB".
