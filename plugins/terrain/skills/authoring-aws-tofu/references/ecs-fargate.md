# ECS on Fargate

Contents: cluster · task definition · the two roles · logging · secrets ·
service · who owns the deploy · private subnets · sources.

## Cluster

```hcl
resource "aws_ecs_cluster" "this" {
  name = "app-${var.environment}"
  setting {
    name  = "containerInsights"
    value = "enhanced"                     # enhanced | enabled | disabled
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    base              = 1                  # only one provider may set base
    weight            = 1
  }
}
```

## Task definition

```hcl
resource "aws_ecs_task_definition" "app" {
  family                   = "app-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"      # required on Fargate
  cpu                      = "512"         # task-level, strings
  memory                   = "1024"        # must be a valid pair for the cpu tier
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"      # required on Fargate
    cpu_architecture        = "ARM64"      # or X86_64; all tasks in a service must match
  }
  container_definitions = jsonencode([{
    name         = "app"
    image        = "${data.aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment  = [{ name = "ENV", value = var.environment }]
    secrets      = [{ name = "DB_PASSWORD", valueFrom = aws_ssm_parameter.db_password.arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = data.aws_region.current.region
        "awslogs-stream-prefix" = "app"    # required on Fargate
      }
    }
  }])
}
```

The CPU tier fixes the allowed memory range (256 units allow only 512,
1024 or 2048 MiB; each larger tier a range in fixed steps). The table
lives in `preflighting-tofu/scripts/hcl_checks.py` next to the check that
enforces it, and in the ECS developer guide; anything else fails at apply
with "No Fargate configuration exists for given values".

Every argument change registers a new revision; the old one stays ACTIVE
unless `skip_destroy = false` (the default) deregisters it. `ephemeral_storage
{ size_in_gib }` is 21 to 200; the default 20 GiB includes image layers.

## The two roles

The **execution role** is what the ECS agent uses: pull the image, write
logs, read secrets. Attach `arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy`
(ECR read and `logs:CreateLogStream`, `logs:PutLogEvents`; note: no
`CreateLogGroup`) plus a policy for `ssm:GetParameters` or
`secretsmanager:GetSecretValue` on the specific secret ARNs, and
`kms:Decrypt` if they use a customer key.

The **task role** is what the application's SDK calls use. It holds only
what the code does. The same role for both means the application can pull
any image and read any secret the platform can; the preflight flags it.

Both trust `ecs-tasks.amazonaws.com`. See `iam.md` for the documents.

## Logging

Create the group here, with retention. The awslogs driver does not create
it unless `"awslogs-create-group" = "true"` and the execution role has
`logs:CreateLogGroup`, and a group created that way has no retention and
no key, and is not destroyed with the stack.

```hcl
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/app-${var.environment}"
  retention_in_days = 30                   # 1,3,5,7,14,30,60,90,... or 0 = never
}
```

## Secrets

`secrets` in the container definition, never `environment`, for anything
that is a credential. `valueFrom` is the SSM parameter or Secrets Manager
ARN; a JSON key in a Secrets Manager secret is addressed with the
`arn:...:secret:name:json-key::` suffix form. Secrets are read at container
start; rotating one needs a new deployment.

## Service

```hcl
resource "aws_ecs_service" "app" {
  name            = "app-${var.environment}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 2
  capacity_provider_strategy {            # or launch_type = "FARGATE"; the two conflict
    capacity_provider = "FARGATE"
    weight            = 1
  }
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = false               # private subnets: pull via endpoints or NAT
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn   # target_type = "ip"
    container_name   = "app"
    container_port   = 8080
  }
  health_check_grace_period_seconds = 60
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  enable_execute_command = false
  propagate_tags         = "SERVICE"
  depends_on = [aws_lb_listener.http, aws_iam_role_policy_attachment.execution]  # docs: else stuck DRAINING on delete
}
```

`deployment_minimum_healthy_percent` and `_maximum_percent` have no
documented default in the provider; the community module uses 66 and 200.
`wait_for_steady_state = true` makes apply wait (20 minute timeout) and is
worth it in CI so a failed rollout fails the apply.

## Who owns the deploy

Decide before writing the service, and write it in the PR body.

- **This module deploys** (image tag is a variable set per apply): keep
  `task_definition` managed; pin the tag or a digest, never `latest`. To
  redeploy the same tag, set `force_new_deployment = true` with a
  `triggers = { redeploy = plantimestamp() }` map.
- **A pipeline deploys** (CI registers revisions and calls UpdateService):
  either `lifecycle { ignore_changes = [task_definition] }` on the service,
  which the provider documents only for `desired_count` but which the
  community module implements for this case, or `track_latest = true` on
  the task definition (provider 5.37+), which keeps state pointing at the
  latest ACTIVE revision. Autoscaling owns `desired_count`: ignore it too.

Without one of these, the next apply reverts the pipeline's deploy with
no error. That is the most-missed finding in the review baseline.

## Private subnets

A Fargate task in a subnet without an internet route needs, per region:
`com.amazonaws.<region>.ecr.api` and `.ecr.dkr` interface endpoints (private
DNS on), the `com.amazonaws.<region>.s3` gateway endpoint (image layers),
`.logs` for awslogs, `.ssm` and/or `.secretsmanager` for secrets. The
endpoint security group admits 443 from the task subnets. The ECS agent
endpoints (`ecs`, `ecs-agent`, `ecs-telemetry`) are for EC2 launch type
only. A NAT gateway replaces all of these. `assign_public_ip = true` in a
private subnet gives an address with no route and does not help.

## Sources

hashicorp/terraform-provider-aws docs `r/ecs_cluster`,
`r/ecs_cluster_capacity_providers`, `r/ecs_task_definition` (`track_latest`
added 5.37.0), `r/ecs_service` (`ignore_changes` on `desired_count`,
DRAINING note), `r/cloudwatch_log_group`; AWS ECS developer guide: task
CPU and memory, task definition parameters (awslogs options, secrets),
task execution IAM role and `AmazonECSTaskExecutionRolePolicy`, deployment
circuit breaker, Fargate capacity providers, VPC endpoints for Fargate,
ECR VPC endpoints; terraform-aws-modules/terraform-aws-ecs README
(`ignore_task_definition_changes`, defaults). All fetched 2026-09-08.
