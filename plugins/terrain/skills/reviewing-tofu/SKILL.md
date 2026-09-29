---
name: reviewing-tofu
description: The review lens for OpenTofu and Terraform changes on AWS — the ordered checklist of defect classes reviewers keep finding late (deploys a pipeline owns that the next apply rolls back, a task that cannot start, a load balancer nothing can reach, roles that hold more than the container should, secrets in plan output, a replace nobody asked for), each with the failure scenario a finding must carry. Use when reviewing a pull request or diff that touches .tf files, when asked to review terraform or tofu, or as a lens in a local review round on infrastructure.
---

# Reviewing OpenTofu on AWS

The preflight in `preflighting-tofu` has run, or you run it first and paste
the tables. Everything it prints is settled ground: do not re-report a
`tflint` warning or a `hcl_checks` rule as a finding. Your mandate is what
no tool sees: whether the module does what the change claims, starts,
stays deployed, and can be reached, without holding more than it needs.

Every finding is `{file, line, severity, summary, failure_scenario}`. The
failure scenario names the state or input and the wrong outcome. If you
cannot write one, it is not a finding. `blocking` means apply fails, the
service cannot start, a credential is exposed, a deploy is silently rolled
back, or data is destroyed. `minor` means it works and is fragile. Report
on lines the change touches or behaviour those lines change.

## The checklist, in the order reviewers miss them

**1. Ownership after apply.** Which attributes does something other than
this module change? A deploy pipeline sets `task_definition` and image
tags; autoscaling sets `desired_count`; Lambda deploys update code. Every
one of those needs `lifecycle { ignore_changes = [...] }` on the resource,
or `track_latest = true` on the task definition, or the next apply rolls
production back to what state remembers. The PR description usually says
who deploys; the diff usually forgets. A mutable image tag (`latest`,
`main`) with no digest and no `force_new_deployment` trigger is the twin
defect: a new push produces no diff, so nothing deploys.

**2. The start-up chain.** Walk what a task or function needs before its
first log line: the image (ECR endpoints `ecr.api`, `ecr.dkr`, the S3
gateway, or a NAT route; `assign_public_ip` is not a substitute in a
private subnet), the log group (must exist, with retention; the managed
execution policy cannot create it), the secrets (execution role needs
`ssm:GetParameters` or `secretsmanager:GetSecretValue`, plus `kms:Decrypt`
for a customer key), the health check (`health_check_grace_period_seconds`
for anything that warms up; a target group with `target_type = "ip"`), and
the Lambda log group and `AWSLambdaBasicExecutionRole` or equivalent. A
missing link is `blocking`: the service loops on failed placements and
never reaches steady state.

**3. Reachability.** Trace one request: DNS or listener port, the load
balancer's subnets (internet-facing needs public subnets; `internal =
true` otherwise), the load balancer's security group (must admit the
listener port), the service's security group (admits the container port
from the load balancer's security group by reference, not from
`0.0.0.0/0`), the target group port and health check path. A chain with a
gap applies cleanly and serves nothing.

**4. Two roles, least privilege.** Execution role pulls and logs; task role
is what the code holds. The same role for both, or `Action = "*"`, is
`blocking`. Then: `iam:PassRole` where a service creates resources with
roles, trust policies with the right principal and a condition on
`aud`/`sub` for federated ones, and the provider 6 rule that
`inline_policy` and `managed_policy_arns` are deprecated in favour of the
`aws_iam_role_policy` and `_attachment` resources (plus the `_exclusive`
resources when exclusive management is wanted).

**5. Secrets and sensitive values.** A credential in a container
`environment` map instead of `secrets`, a variable or output holding one
without `sensitive = true`, a default value for one, a plan or state that
will carry it. Ask where the value ends up: task definition JSON (readable
by `ecs:DescribeTaskDefinition`), CI logs, the S3 state object. OpenTofu
1.11+ has `ephemeral` variables and resources for values that must not
reach state at all.

**6. The plan's replace and destroy rows.** Every `replace` and `destroy` in
the plan summary has a sentence in the PR body or is a finding. Usual
causes: a `name` change on a resource that cannot rename, a `region`
argument added to an existing resource (provider 6 forces replacement), a
`for_each` key change (a `moved` block prevents it), a `count` list that
reordered. Stateful resources (databases, buckets with data, queues with
messages) carry `prevent_destroy` or the PR says why not.

**7. Async plumbing.** SQS visibility timeout at least six times the
consumer's timeout; a dead-letter queue with a redrive policy; the event
source mapping with `function_response_types = ["ReportBatchItemFailures"]`
and the function returning partial failures; encryption on the queue.

**8. Provider drift.** Arguments that moved in provider 4 to 6: inline
bucket settings, `aws_region` data `name`, inline security group rules
where the rule resources are the documented practice, `aws_eip.vpc`,
`aws_flow_log.log_group_name`. A hardcoded region or account id in an
ARN or image URI where a data source belongs.

**9. Conventions the repository states.** Read its CLAUDE.md and rules
files; cite the line. If it is not written down, it is not a convention.

## Not findings

- Anything the preflight tables already show, by rule name.
- WAF, X-Ray, cross-region replication, access logging, customer-managed
  KMS keys, deletion protection, on a service whose repository rules do not
  require them. Trivy and checkov print these on every stack.
- Style: file layout, naming, comment density, module boundaries, unless
  the repository's rules state them.
- Missing tests for infrastructure. `tofu test` is available (mocked
  providers since OpenTofu 1.8); its absence is not a defect unless the
  repository requires it.

## Worked example

`aws_ecs_service.app` with `task_definition = aws_ecs_task_definition.app.arn`,
no `lifecycle` block, and a PR description saying the pipeline deploys.

```json
{"file": "ecs.tf", "line": 36, "severity": "blocking",
 "summary": "service pins the state's task definition while the pipeline deploys new revisions",
 "failure_scenario": "CI registers revision 12 and updates the service; the next tofu apply sees task_definition drift and updates the service back to revision 11 from state, reverting the deploy with no error"}
```

The checklist item (1) names the class, the scenario names the sequence,
and the fix (`ignore_changes = [task_definition]` or `track_latest`) is in
`authoring-aws-tofu` under ECS Fargate.

## Sources

Built from a planted-defect baseline (34 defects, 2026-09-08: the four
linters catch 12, an unaided Sonnet reviewer 20, missing exactly items 1
to 3 above) and: AWS ECS developer guide (`awsvpc`, target type `ip`,
execution versus task role, awslogs options, secrets permissions, VPC
endpoints for Fargate), AWS Lambda docs (SQS visibility timeout, partial
batch responses, log groups), hashicorp/aws provider docs for
`aws_ecs_service` (`ignore_changes` on `desired_count`), `aws_iam_role`
(deprecations), `aws_security_group` (inline rules discouraged), version 6
upgrade guide; OpenTofu docs (ephemerality, `moved`, `removed`, `tofu
test`). All fetched 2026-09-29 (first 2026-09-08).
