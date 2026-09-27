---
name: brief-writes-file
tags: [brief,supervisor]
runs: 1
max_turns: 12
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, AskUserQuestion]
---
/supervisor:brief make the checkout tests under tests/e2e stop failing intermittently
