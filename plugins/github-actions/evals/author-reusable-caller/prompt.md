---
name: author-reusable-caller
tags: [github-actions,authoring]
runs: 1
max_turns: 10
timeout_seconds: 300
---
Write a caller workflow for our org's reusable deploy workflow at `konyklabs/.github/.github/workflows/deploy.yml@main`. It takes an `environment` input and a `DEPLOY_TOKEN` secret, and produces a `deployed_url` output. Pass the secret through and print the deployed URL from a job that runs after the call.
