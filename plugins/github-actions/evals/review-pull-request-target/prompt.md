---
name: review-pull-request-target
tags: [github-actions,review]
runs: 1
max_turns: 10
timeout_seconds: 300
---
Review this workflow. It triggers on `pull_request_target`, checks out the PR head with `actions/checkout@v4` and `ref: ${{ github.event.pull_request.head.sha }}`, then runs `npm ci && npm test` from the checked-out tree. Return findings as JSON with a failure scenario each.
