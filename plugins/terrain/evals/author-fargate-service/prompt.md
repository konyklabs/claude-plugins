---
name: author-fargate-service
tags: [terrain,authoring]
runs: 1
max_turns: 12
timeout_seconds: 300
---
Write the OpenTofu for a Fargate web service behind an application load balancer: cluster, task definition (0.5 vCPU, 1 GB), service, target group, log group, the two IAM roles. The VPC and subnets come in as variables. AWS provider 6.
