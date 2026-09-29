# Lambda

Contents: packaging and the hash · the function · role and log group ·
VPC · triggers · SQS specifics · sources.

## Packaging and the hash

```hcl
data "archive_file" "worker" {
  type             = "zip"
  source_dir       = "${path.module}/../worker"
  output_path      = "${path.module}/.build/worker.zip"
  output_file_mode = "0644"                # same checksum on every OS
  excludes         = ["**/__pycache__/**", "**/*.pyc"]
}

resource "aws_lambda_function" "worker" {
  function_name    = "app-worker-${var.environment}"
  role             = aws_iam_role.worker.arn
  runtime          = "python3.13"
  handler          = "handler.main"
  architectures    = ["arm64"]
  filename         = data.archive_file.worker.output_path
  code_sha256      = data.archive_file.worker.output_base64sha256   # Lambda's own hash; see below
  timeout          = 60
  memory_size      = 512
  environment {
    variables = { QUEUE_URL = aws_sqs_queue.jobs.url }
  }
  logging_config {
    log_format = "JSON"
  }
  depends_on = [aws_cloudwatch_log_group.worker, aws_iam_role_policy_attachment.worker_logs]
}
```

Without `code_sha256` (or `source_code_hash`) the provider never sees a
code change: the zip is rebuilt, the function keeps the old code.
`code_sha256` must be `output_base64sha256`: the provider compares it with
Lambda's own base64 SHA-256, so `output_md5` and `output_sha` never match
and force an update on every plan. `source_code_hash` is the older form and
is now documented as a synthetic trigger the provider never compares with
Lambda's hash: any digest works, and an out-of-band deploy is not caught
(provider docs, 2026-09-29; the 2026-09-08 digest had the two the other way
round). The
archive is built at plan time and must exist at apply; in a multi-stage
pipeline persist the zip or build it in a step before plan. Artifacts
larger than a few MB go through S3 (`s3_bucket`, `s3_key`,
`s3_object_version`) or a container image (`package_type = "Image"`,
`image_uri`); `filename` conflicts with both.

`publish = true` plus `aws_lambda_alias` gives a stable ARN for
permissions and weighted rollouts; permissions cannot be attached to
`$LATEST`.

## Runtimes

The valid identifier list and the deprecation schedule live in the AWS
Lambda runtimes page. Deprecation blocks create after about 30 days and
update after about 60; `tflint`'s `aws_lambda_function_deprecated_runtime`
rule tracks the schedule. Pick the newest supported major of the language
and pin the minor in code, not in the runtime string.

## Role and log group

```hcl
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/app-worker-${var.environment}"   # the name Lambda would create
  retention_in_days = 30
}

resource "aws_iam_role_policy_attachment" "worker_logs" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
```

Lambda creates `/aws/lambda/<name>` on first invoke if the role can, with
no retention. Creating it here first, and listing it in the function's
`depends_on`, is the provider's own example; otherwise the first invoke
wins and the module's create fails with "already exists". A custom name
via `logging_config.log_group` must not start with `aws/`. The role trusts
`lambda.amazonaws.com`.

## VPC

Only when the function must reach something private. `vpc_config {
subnet_ids, security_group_ids }` needs the ENI permissions
(`AWSLambdaVPCAccessExecutionRole`), loses internet access unless the
subnet has a NAT route, and holds ENIs for up to 20 minutes after delete;
destroy the role after the function.

## Triggers

`aws_lambda_permission` for push invokers: `principal =
"apigateway.amazonaws.com"` with `source_arn = "${execution_arn}/*"`;
`events.amazonaws.com` with the rule ARN; `s3.amazonaws.com` with the
bucket ARN and `source_account`, and the bucket notification `depends_on`
the permission. Poll invokers (SQS, Kinesis, DynamoDB streams) use
`aws_lambda_event_source_mapping` and the role needs read permissions on
the source instead.

## SQS specifics

```hcl
resource "aws_sqs_queue" "jobs_dlq" {
  name                    = "app-jobs-dlq-${var.environment}"
  sqs_managed_sse_enabled = true
}

resource "aws_sqs_queue" "jobs" {
  name                       = "app-jobs-${var.environment}"
  visibility_timeout_seconds = 360        # at least 6 x the function timeout
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn        = aws_sqs_queue.jobs.arn
  function_name           = aws_lambda_function.worker.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 10 }   # minimum 2
}
```

The function returns `{"batchItemFailures": [{"itemIdentifier": messageId}]}`
for the records that failed, or the whole batch is retried. Visibility
below the function timeout delivers the same message twice; the preflight
flags that case. `batch_size` above 10 requires
`maximum_batching_window_in_seconds`. The role needs `sqs:ReceiveMessage`,
`sqs:DeleteMessage`, `sqs:GetQueueAttributes` on the queue
(`AWSLambdaSQSQueueExecutionRole` covers them).

## Sources

hashicorp/terraform-provider-aws docs `r/lambda_function` (packaging,
`source_code_hash`, `code_sha256`, `logging_config` since 5.32.0, the
CloudWatch logging example with `depends_on`), `r/lambda_alias`,
`r/lambda_permission`, `r/lambda_event_source_mapping`,
`r/s3_bucket_notification`; hashicorp/terraform-provider-archive
`data-sources/file` (plan-time build, `output_file_mode`); AWS Lambda
developer guide: runtimes and deprecation policy, VPC networking,
CloudWatch log groups, SQS event source parameters and error handling;
terraform-aws-modules/terraform-aws-lambda README. Fetched 2026-09-29 (first 2026-09-08; `code_sha256` versus `source_code_hash` corrected on 2026-09-29 against provider 6.66.0).
The `AWSLambdaSQSQueueExecutionRole` action list is from memory of the
AWS managed policy reference and was not re-fetched.
