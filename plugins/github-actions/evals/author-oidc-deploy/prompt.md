---
name: author-oidc-deploy
tags: [github-actions,authoring]
runs: 1
max_turns: 10
timeout_seconds: 300
---
Write a GitHub Actions workflow that builds a container image and deploys it to AWS by assuming an IAM role via OIDC, triggered on push to main. No long-lived AWS access keys.
