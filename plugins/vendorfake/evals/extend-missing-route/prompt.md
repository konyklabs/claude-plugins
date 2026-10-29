---
name: extend-missing-route
tags: [vendorfake, extending]
runs: 1
max_turns: 8
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill]
---
Our Clover integration calls `GET /v3/merchants/{mId}/shifts` and vendorfake answers 404 with a Vendorfake-Near-Miss header. Do we stub it in the e2e tier, or what? Walk me through it, including how we use the fix before vendorfake tags a release.
