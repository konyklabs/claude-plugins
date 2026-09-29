---
name: vitest-config-projects
tags: [vitest,ts-testing]
runs: 1
max_turns: 8
timeout_seconds: 240
---
We have a two-package monorepo (packages/api and packages/web) and want one Vitest setup that runs both packages' tests in CI.
