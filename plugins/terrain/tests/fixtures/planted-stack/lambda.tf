data "archive_file" "worker" {
  type        = "zip"
  source_dir  = "${path.module}/../worker"
  output_path = "${path.module}/worker.zip"
}

resource "aws_lambda_function" "worker" {
  function_name = "app-worker-${var.environment}"
  role          = aws_iam_role.lambda.arn
  handler       = "handler.main"
  runtime       = "python3.8"
  filename      = data.archive_file.worker.output_path
  timeout       = 30

  environment {
    variables = {
      QUEUE_URL = aws_sqs_queue.jobs.url
    }
  }
}

resource "aws_sqs_queue" "jobs" {
  name = "app-jobs-${var.environment}"
}

resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn = aws_sqs_queue.jobs.arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 10
}

resource "null_resource" "warm" {
  provisioner "local-exec" {
    command = "aws lambda invoke --function-name ${aws_lambda_function.worker.function_name} /dev/null"
  }
}
