# IAM

Contents: policy documents · a role, the current way · trust policies ·
GitHub OIDC · exclusive management · least-privilege habits · sources.

## Policy documents

Write policies with `aws_iam_policy_document`, not `jsonencode`. The data
source validates the shape at plan time, composes with
`source_policy_documents` and `override_policy_documents` (statements with
the same `sid` override earlier ones; statements without a `sid` cannot be
overridden), and the preflight's star-action check reads both forms.

```hcl
data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ReadJobs"
    effect    = "Allow"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.jobs.arn]
  }
  statement {
    sid       = "ObjectsInPrefix"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/uploads/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}
```

`condition` needs all three of `test`, `variable`, `values`. `principals`
types: `AWS`, `Service`, `Federated`, `CanonicalUser`, `*`.

## A role, the current way

```hcl
resource "aws_iam_role" "task" {
  name               = "app-task-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "task" {
  name   = "task"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
```

`inline_policy` and `managed_policy_arns` on `aws_iam_role` are deprecated
in provider 6 and validate warns on them. The replacements above do not
remove policies attached outside this module; when they should, add the
exclusive resources below.

## Trust policies

```hcl
data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]   # lambda.amazonaws.com for functions
    }
    condition {                                    # confused-deputy guard, optional but cheap
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}
```

Two roles for ECS (see `ecs-fargate.md`), one for each Lambda, never a
shared "app role" across services: the blast radius of a compromise is
exactly what the role holds.

## GitHub OIDC

The provider docs have no example for this; the shape below is the data
source's block syntax with GitHub's documented claims.

```hcl
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # thumbprint_list is optional: AWS trusts GitHub's issuer through its CA library
}

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:<org>@<org_id>/<repo>@<repo_id>:ref:refs/heads/main"]
    }
  }
}
```

Use the ID-pinned subject form (`org@id/repo@id`); the name-only form is
subject to rename hijacking. `StringLike` with a `*` on `sub` is what
turns one repository's role into the organization's. Separate roles for
plan (read-only) and apply (write), with the apply subject restricted to
the default branch.

## Exclusive management

`aws_iam_role_policies_exclusive { role_name, policy_names }` and
`aws_iam_role_policy_attachments_exclusive { role_name, policy_arns }` take
ownership of everything on the role: anything not listed is removed at
apply, an empty list removes all. Every `aws_iam_role_policy` and
`_attachment` in the module for that role must be listed or it drifts.
Destroying the exclusive resource does not remove the policies.

## Least-privilege habits

- `Action = "*"` with `Resource = "*"` is never right; the preflight flags
  the JSON form and trivy/checkov the data-source form.
- `iam:PassRole` is scoped to the role being passed and, where the service
  supports it, `iam:PassedToService`.
- Build ARNs from `data.aws_partition`, `data.aws_caller_identity` and
  `data.aws_region`, never literals; a policy with a hardcoded account id
  is a policy for one account.
- `permissions_boundary` on roles CI creates, so a deploy role cannot mint
  a role wider than itself.

## Sources

hashicorp/terraform-provider-aws docs `d/iam_policy_document`
(`source_policy_documents`, `override_policy_documents`, `condition`,
`principals`), `r/iam_role` (deprecations), `r/iam_role_policies_exclusive`,
`r/iam_role_policy_attachments_exclusive`, `r/iam_openid_connect_provider`;
GitHub docs "Configuring OpenID Connect in Amazon Web Services" (claims,
ID-pinned subject form). Fetched 2026-09-08.
