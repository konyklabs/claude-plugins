---
name: authoring-aws-tofu
description: How to write OpenTofu for AWS so it survives the preflight and the review — the docs-first rule for resource arguments (fetch the provider's page for the pinned version, never write from memory), the provider 6 facts that changed the shape of common resources, and the reference patterns for ECS Fargate services, Lambda functions, IAM roles and policies, VPC networking, and state and module structure with moved, removed and import blocks. Use when writing or changing .tf files for AWS, when validate rejects an argument, when a plan shows an unexpected replace, or when deciding where a new resource or environment belongs.
---

# Authoring OpenTofu for AWS

Three rules, then the references.

## 1. Arguments come from the docs, not from memory

The AWS provider ships a minor about weekly and moved most common
resources between versions 4 and 6. An argument recalled from an older
version validates as "Unsupported argument" at best and applies with the
wrong meaning at worst (`region` on a resource now means the resource's
region). Before writing a resource you have not written this week, read
its current page, pinned to the version in `.terraform.lock.hcl`:

- **Context7 when the MCP server is present**: resolve the library id for
  the hashicorp/aws provider once, then query the resource by name
  (`aws_ecs_service arguments`). That is the whole reference; this skill
  does not duplicate it.
- **Otherwise the raw markdown the registry renders**, which a fetcher
  can read while the registry's HTML cannot:
  `https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/<ref>/website/docs/r/<name>.html.markdown`
  (`d/` for data sources, `guides/` for the upgrade guides; `<ref>` is a
  tag such as `v6.66.0` or `main`).

Read the argument list and the notes at the top of the page; that is
where "conflicts with", "forces replacement" and "deprecated" live. The
references in this skill carry what a page does not say: which arguments
together make a service start, deploy and stay deployed.

## 2. Pin, lock, and lock the state

```hcl
terraform {
  required_version = ">= 1.10"        # use_lockfile needs 1.10; ephemeral needs 1.11
  required_providers {
    aws     = { source = "hashicorp/aws",     version = "~> 6.60" }
    archive = { source = "hashicorp/archive", version = "~> 2.7" }
  }
  backend "s3" {
    bucket       = "<state-bucket>"
    key          = "<stack>/terraform.tfstate"
    region       = "<region>"
    use_lockfile = true               # native S3 locking; dynamodb_table also still supported
    encrypt      = true
  }
}
```

Commit `.terraform.lock.hcl`. OpenTofu 1.12 writes checksums for every
platform, so a lockfile made on macOS works in Linux CI. Never commit
`terraform.tfvars` with values, and never a `.tfstate`.

## 3. Data sources, not literals

`data.aws_caller_identity.current.account_id`, `data.aws_region.current.region`
(the `name` attribute is deprecated in provider 6), `data.aws_partition.current.partition`
for ARNs; `data.aws_ecr_repository` for image URIs; `data.aws_ssm_parameter`
for AMIs. A twelve-digit account id in a `.tf` file is a finding.

## Where the patterns are

Each reference is self-contained, one level deep, with a contents list, and
ends with its sources and fetch date. Load the one the task needs.

| task | reference |
|---|---|
| ECS cluster, Fargate task definition, service, deploy ownership, logging, secrets, private-subnet pulls | `references/ecs-fargate.md` |
| Lambda packaging and hash, runtimes, roles, log group, VPC, SQS and API Gateway triggers | `references/lambda.md` |
| Roles, the two-role rule, policy documents, trust policies, GitHub OIDC, the `_exclusive` resources | `references/iam.md` |
| Security groups by rule resource, load balancer placement and chain, VPC endpoints versus NAT | `references/networking.md` |
| Root modules per environment, `moved`, `removed`, `import`, drift, `tofu test`, state encryption | `references/state-and-structure.md` |

## Provider 6 facts that change how you write

- **`region` on a resource** selects that resource's region; one provider
  block serves many regions. Adding it to an existing resource forces
  replacement. Global services (IAM, CloudFront, Route 53) have none.
  Import with `<id>@<region>`.
- **`aws_s3_bucket` is a shell.** ACL, versioning, encryption, policy,
  lifecycle, logging, website, CORS are each their own resource. The
  inline arguments still parse with a deprecation warning and are gone in 7.
- **`aws_iam_role`**: `inline_policy` and `managed_policy_arns` are
  deprecated. Use `aws_iam_role_policy` and `aws_iam_role_policy_attachment`;
  add `aws_iam_role_policies_exclusive` or `_policy_attachments_exclusive`
  when the role must hold nothing else.
- **Security groups**: the resource page says to avoid inline `ingress`
  and `egress`; use `aws_vpc_security_group_ingress_rule` and
  `_egress_rule`, and never mix the two styles on one group.
- **`default_tags`** on the provider merges into every resource's
  `tags_all`. A tag value unknown until apply can produce "inconsistent
  final plan" on `tags_all`; keep tag values literal.
- Everything else removed or renamed in 6 is in the upgrade guide, by
  service; read it when validate rejects an argument that used to exist.

## Before handing over

Run `preflighting-tofu`. Paste its tables. Then write the PR body with:
who deploys what after apply (item 1 of `reviewing-tofu`), each replace or
destroy the plan shows and why, and the rule names from `hcl_checks` you
answered rather than fixed.

## Sources

hashicorp/terraform-provider-aws `website/docs`: `index` (provider
configuration), `guides/version-6-upgrade`, `guides/enhanced-region-support`,
`r/iam_role`, `r/s3_bucket`, `r/security_group`, `d/region`; registry v1
API for versions (6.66.0 latest on 2026-09-21); OpenTofu docs for the S3
backend (`use_lockfile`, 1.10), dependency lock file (all-platform
checksums, 1.12), settings. Fetched 2026-09-29 (first 2026-09-08; OpenTofu 1.12.6 current).
