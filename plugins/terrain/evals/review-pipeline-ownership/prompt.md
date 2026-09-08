---
name: review-pipeline-ownership
tags: [terrain,review]
runs: 1
max_turns: 10
timeout_seconds: 300
---
Review this PR. It adds an aws_ecs_service on Fargate with task_definition = aws_ecs_task_definition.app.arn and desired_count = 2, no lifecycle block. The PR description says: "CI builds the image and updates the service with a new task definition revision after every merge; autoscaling is configured in the console." Return findings as JSON with a failure scenario each.
