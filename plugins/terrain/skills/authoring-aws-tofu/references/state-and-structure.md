# State and structure

Contents: root modules per environment · what goes in one state · moved ·
removed · import · replace you did not ask for · drift · tofu test · state
encryption · sources.

## Root modules per environment

One directory per environment, each with its own `backend.tf` key and its
own `terraform.tfvars`; shared code in `modules/` consumed by version or
pinned ref. Not workspaces: the same code with a different selected
workspace is an apply to production waiting on a forgotten `tofu
workspace select`. AWS's guidance and Google's agree on this; HashiCorp's
style guide allows workspaces only in HCP Terraform.

```
envs/
  dev/    backend.tf  main.tf  terraform.tfvars
  prod/   backend.tf  main.tf  terraform.tfvars
modules/
  service/  main.tf  variables.tf  outputs.tf  versions.tf
```

Modules declare `required_providers` and never configure a provider;
variables carry `type` and `description`; environment-specific values
have no `default`. `count` for a conditional resource, `for_each` keyed by
a stable string for collections; a `count` over a list is positional and
reorders into destroy-and-create.

## What goes in one state

Small. A state with a hundred resources is slow to plan and wide to
break. Split by blast radius and by rate of change: networking, then
shared platform (cluster, log groups, roles), then each service. Data
sources or `terraform_remote_state` read across the seam; outputs are
the contract.

## `moved`: rename without replace

```hcl
moved {
  from = aws_ecs_service.app
  to   = module.service.aws_ecs_service.this
}
```

No labels, `from` and `to` only. Works across module boundaries within
the same configuration, for `for_each` key changes (`from =
aws_instance.a["old"]`), and since OpenTofu 1.10 between resource types.
Cannot target an ephemeral block. The plan shows the move as a no-op with
a note; a replace after adding one means the addresses do not match.

## `removed`: forget without destroy

```hcl
removed {
  from = aws_cloudwatch_log_group.legacy
  lifecycle {
    destroy = false          # keep the object, drop it from state
  }
}
```

`from` takes no instance key. OpenTofu 1.12 also accepts `lifecycle {
destroy = false }` directly on a managed resource; the plan action is
`forget` and the plan summary lists it as such.

## `import`: adopt what exists

```hcl
import {
  to = aws_cloudwatch_log_group.app
  id = "/ecs/app-prod"          # or, 1.12+: identity = { ... }
}
```

`for_each` on import blocks since 1.7. `tofu plan -generate-config-out=
generated.tf` writes HCL for imported resources without configuration
(experimental; not with `for_each`). Provider 6 imports a regional
resource in another region with `<id>@<region>`.

## A replace you did not ask for

Read the plan summary's `reason` column:

- `replace_because_cannot_update`: an argument the API cannot change in
  place. `name` on most resources, `region` on any resource in provider
  6, `family` on a task definition, subnet on an ENI-bound resource.
  Either accept it (and say so in the PR) or find the rename path (a new
  resource plus `moved` for the address, or `name_prefix`).
- `replace_by_request`: someone ran with `-replace`.
- `replace_because_tainted`: a failed create left a tainted object.
- `delete_because_no_resource_config` / `_each_key` / `_count_index`: the
  configuration no longer names it. A key change means `moved`; a removal
  of something still wanted means `removed { destroy = false }`.

`lifecycle { prevent_destroy = true }` on databases, buckets that hold
data, queues with messages, and log groups worth keeping; OpenTofu 1.12
lets the value reference variables.

## Drift

`drift:` in the plan summary lists objects changed outside this module.
Expected drift (a pipeline-owned `task_definition`, autoscaled
`desired_count`) is answered with `ignore_changes`; anything else is
either imported into intent or reverted deliberately, never applied over
without a decision.

## `tofu test`

`tests/*.tftest.hcl` with `run` blocks (`command = plan` by default
applies; use `plan` for schema and assertion checks without AWS),
`assert { condition, error_message }`, `expect_failures` for variable
validation, and `mock_provider "aws" {}` since 1.8 so a test needs no
credentials. Worth having for a module's input validation and the plan
shape it promises; not a substitute for the preflight on a root module.

## State encryption

OpenTofu-only: an `encryption` block in `terraform {}` with a
`key_provider "aws_kms"` (`kms_key_id`, `region`, `key_spec = "AES_256"`),
a `method "aes_gcm"`, and `state { method = ..., enforced = true }`. The
exact wiring is in the OpenTofu state encryption page; verify there
before enabling, and note that Terraform cannot read an encrypted state.

## Sources

OpenTofu docs: refactoring (`moved`, 1.10 cross-type), resource syntax
(`removed`), import (`for_each` 1.7, `identity` 1.12), plan
(`-generate-config-out`, `-detailed-exitcode`), JSON format (`action_reason`
values), test command (mocking since 1.8), state encryption, settings
(`language` block, 1.12); OpenTofu 1.12 changelog (resource `destroy =
false`, dynamic `prevent_destroy`); AWS Prescriptive Guidance for the
Terraform AWS Provider (structure, backend per environment, no shared
workspaces); Google Cloud Terraform best practices (root modules, state
size, default workspace only); HashiCorp Terraform style guide. Fetched
2026-09-29 (first 2026-09-08).
