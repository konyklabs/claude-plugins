# Planted defects (answer key; reviewers never see this)

| id | file | defect | class |
|---|---|---|---|
| D01 | providers.tf | aws provider has no version constraint | pinning |
| D02 | providers.tf | archive provider has no version constraint | pinning |
| D03 | providers.tf | S3 backend without use_lockfile/encrypt: no state locking | state |
| D04 | variables.tf | `environment` has no type or description | hygiene |
| D05 | variables.tf | `image_tag` defaults to `latest` (mutable tag) | deploy |
| D06 | variables.tf | `db_password` has a plaintext default, not `sensitive` | secrets |
| D07 | network.tf | port 22 open to 0.0.0.0/0 on a Fargate task SG | security |
| D08 | network.tf | port 8080 open to 0.0.0.0/0 instead of from the ALB SG | security |
| D09 | network.tf | internet-facing ALB placed in private subnets | networking |
| D10 | network.tf | ALB reuses the service SG, which has no port-80 ingress: listener unreachable | networking |
| D11 | network.tf | target group missing `target_type = "ip"` (required for awsvpc/Fargate) | ecs |
| D12 | iam.tf | inline policy Action * Resource * | iam |
| D13 | iam.tf | `inline_policy` block (deprecated in provider 5/6) | provider-drift |
| D14 | iam.tf | one role serves as both execution and task role, with admin | iam |
| D15 | iam.tf, ecs.tf | hardcoded account id and region in ARNs and image | portability |
| D16 | iam.tf | lambda role lacks logs permissions (no basic execution policy) | lambda |
| D17 | ecs.tf | cpu 256 with memory 4096: invalid Fargate combination | ecs |
| D18 | ecs.tf | `network_mode` missing; Fargate requires awsvpc | ecs |
| D19 | ecs.tf | awslogs group `/ecs/app-*` never created; exec role cannot create it; task fails to start | ecs |
| D20 | ecs.tf | DB_PASSWORD as plaintext `environment` instead of `secrets` | secrets |
| D21 | ecs.tf | `assign_public_ip = true` in private subnets | networking |
| D22 | ecs.tf | comment says pipeline deploys, no `ignore_changes = [task_definition]`: apply rolls back deploys | deploy |
| D23 | ecs.tf | no deployment_circuit_breaker / rollback | ecs (minor) |
| D24 | lambda.tf | no `source_code_hash`: code changes never redeploy | lambda |
| D25 | lambda.tf | runtime python3.8 is deprecated/unsupported | lambda |
| D26 | lambda.tf | no log group with retention | lambda (minor) |
| D27 | lambda.tf | SQS visibility timeout left at the default 30 s while the function timeout is 60 s: a message is delivered again mid-invocation (AWS guidance: at least 6x) | lambda |
| D28 | lambda.tf | `null_resource` local-exec, no triggers, runs once, needs aws CLI; null provider undeclared | provisioner |
| D29 | lambda.tf | no DLQ/redrive on the queue; no ReportBatchItemFailures | lambda (minor) |
| D30 | storage.tf | `acl` and inline `versioning` on aws_s3_bucket: removed/deprecated since 4.0 | provider-drift |
| D31 | storage.tf | uploads public access block leaves public policy allowed | security |
| D32 | storage.tf | data bucket: no encryption config, no public access block, no versioning resource | security |
| D33 | outputs.tf | `db_password` output not `sensitive` | secrets |
| D34 | lambda.tf | archive zip built at plan time from ../worker: non-reproducible across machines | lambda (minor) |
