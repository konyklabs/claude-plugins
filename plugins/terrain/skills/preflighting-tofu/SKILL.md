---
name: preflighting-tofu
description: The deterministic checks to run on an OpenTofu or Terraform root module before a plan is trusted, a review is asked for, or a branch is pushed — fmt, validate, whatever linters are on PATH reduced to counts, structural checks for the Fargate, Lambda, IAM, secrets and state-locking traps no linter catches, and a plan summary that fails closed on destroy or replace. Use before pushing infrastructure changes, when asked whether a plan is safe to apply, when a tofu apply failed on something validate accepted, or as the first step of any Terraform review.
---

# Preflighting OpenTofu

Validate accepts a Fargate task definition whose CPU and memory pair AWS
will reject, a log group nobody creates, and a Lambda whose code never
redeploys. The review lens is told to skip anything a linter catches. So a
module that reaches a reviewer without this preflight arrives with the
cheap defects still in it, and the reviewer spends the round on those. Run
this first, paste the tables, then ask for review.

Every script here is standard-library Python, installs nothing, opens no
socket, and prints `path:line rule` only: no message text, no source text,
no attribute values. A missing or broken tool is a `skip` row, never a pass.

## The sequence

From the root module directory (`DIR`). Do not vary the order; each step
assumes the previous one is clean.

```sh
tofu fmt -recursive                                   # write, then re-run -check
tofu init -backend=false -input=false                 # only if .terraform is absent; needs network
python3 "${CLAUDE_SKILL_DIR}/scripts/preflight.py" DIR      # fmt, validate, tflint, trivy, checkov
python3 "${CLAUDE_SKILL_DIR}/scripts/hcl_checks.py" DIR     # the structural traps, modules included
tofu plan -out=tfplan -input=false && tofu show -json tfplan > tfplan.json
python3 "${CLAUDE_SKILL_DIR}/scripts/plan_summary.py" tfplan.json   # fails closed on destroy, replace, forget
```

`terraform` is used automatically when `tofu` is not on PATH.

## Reading the preflight table

- **skip rows are not evidence.** `validate skip not initialized` means run
  `tofu init -backend=false` and re-run. `tflint skip ... tflint --init`
  means the AWS ruleset plugin is declared but not installed. A row that
  stays `skip` is stated as skipped in the evidence, by name.
- **validate warnings are findings.** The row shows `path:line
  validate:warning` and the resource address, never the message (a
  diagnostic is free text, and the table is untrusted input to the model
  that reads it). Run `tofu validate` yourself to read it. Since provider
  6 these are deprecated arguments (`inline_policy`, inline `acl`,
  `versioning`) and the message names the replacement resource. Fix
  them; they become errors in 7.
- **trivy and checkov are noisy by design.** Their counts go in the evidence
  as counts. Act on the rows that match the checklist in `reviewing-tofu`
  (open ingress, public access block, same role for execution and task,
  public IP on a Fargate task, unencrypted queue). A WAF or cross-region
  replication row on an internal service is answered in one line, not
  fixed.
- **tflint** is the cheapest signal there is: the bundled terraform
  preset for unpinned providers and untyped variables, the AWS ruleset
  plugin (declared in `.tflint.hcl`, installed by `tflint --init`) for an
  end-of-life Lambda runtime and invalid arguments. Zero warnings is the
  bar, and a `skip` row on tflint means the bar was not measured.

## Reading the structural checks

Each rule maps to a failure that validate does not see. The rule name is
the search key in `reviewing-tofu`, which carries the failure scenario.

| rule | what breaks |
|---|---|
| `fargate_network_mode_not_awsvpc` | RegisterTaskDefinition rejects the task |
| `fargate_cpu_memory_invalid` | "No Fargate configuration exists for given values" |
| `task_role_same_as_execution_role` | app code holds the pull-and-log role's permissions |
| `awslogs_group_not_managed` | task fails to start; the managed execution policy has no CreateLogGroup |
| `target_group_not_ip_for_fargate` | service creation fails against an `instance` target group |
| `lambda_no_source_code_hash` | code changes never redeploy |
| `sqs_visibility_below_lambda_timeout` | a message is delivered twice while the first invocation still runs |
| `secret_variable_not_sensitive`, `secret_output_not_sensitive`, `secret_variable_has_default` | credential in plan output, CI logs, state, or git |
| `s3_backend_no_locking` | two concurrent applies corrupt state |
| `hardcoded_account_id` | the module breaks in every other account |
| `iam_star_action` | admin from any application compromise |

A finding the module cannot avoid (a legacy log group the platform owns,
a deliberate `Action = "*"` scoped by `Resource` and a condition) is
answered in the PR body, next to the rule name. It is not silenced in the
script.

## Reading the plan summary

```
plan: create=4  update=1  replace=1  destroy=0

action   type                                     address
replace  aws_ecs_service                          aws_ecs_service.app  (replace_because_cannot_update)
...
BLOCKED: destructive change(s) not on the allow list:
  replace  aws_ecs_service.app
```

Exit 2 is the answer "not safe to apply as it stands". The only way past
it is `--allow ADDRESS` for each destroy, replace or forget that the task
brief names as intended (the address exactly as the plan prints it), and
the brief is quoted in the evidence. A `forget` (a `removed` block or
`destroy = false`) is on the list because the object stops being managed,
which is as hard to undo as a destroy. A replace the
brief does not mention is a finding: usually a `name` change on a resource
that cannot rename, a `region` argument added to an existing resource, or
a `for_each` key change. `moved` blocks fix the last one without a replace;
see `authoring-aws-tofu`.

`drift:` lines mean something changed outside the plan. For an ECS service
a pipeline deploys, `task_definition` drift is expected and the service
needs `ignore_changes`; anything else is investigated before apply.

The plan JSON contains every attribute value, secrets included. Keep
`tfplan.json` out of git and out of PR bodies; the summary table is what
gets pasted.

## What this does not cover

Design and intent: an ALB in private subnets, a security group that never
opens the listener port, `ignore_changes` missing on a service a pipeline
updates, a mutable image tag. Those are the reviewer's ground, in
`reviewing-tofu`. A clean preflight is the precondition for that review,
not a substitute.

## Sources

OpenTofu docs: `plan`, `show -json` and the JSON plan format
(`resource_changes[].change.actions`, `action_reason`, `resource_drift`),
`validate -json`, `fmt -check`, S3 backend `use_lockfile`; tflint README
and `formatter/json.go` (issue shape, exit codes 0/1/2); trivy config and
checkov CLI docs (JSON shapes verified by running 0.74.0 and 3.3.16); AWS
ECS developer guide (Fargate CPU/memory table, `awsvpc` requirement, `ip`
target type, `AmazonECSTaskExecutionRolePolicy` actions); AWS Lambda docs
(SQS visibility timeout). All fetched 2026-09-08.
