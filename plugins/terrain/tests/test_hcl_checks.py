import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "preflighting-tofu" / "scripts"))
import hcl_checks as h  # noqa: E402


def rules(*files: str):
    texts = {f"f{i}.tf": t for i, t in enumerate(files)}
    blocks = [b for name, t in texts.items() for b in h.scan_blocks(t, name)]
    return {line.split(" ", 1)[1] for line in h.run_checks(blocks, texts)}


GOOD_TASK = '''
resource "aws_cloudwatch_log_group" "app" { name = "/ecs/app-${var.env}" }
resource "aws_ecs_task_definition" "app" {
  family                   = "app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{ logConfiguration = { options = { "awslogs-group" = "/ecs/app-${var.env}" } } }])
}
resource "aws_lb_target_group" "app" { target_type = "ip" }
'''


def test_correct_fargate_task_has_no_findings():
    assert rules(GOOD_TASK) == set()


def test_fargate_task_traps():
    bad = GOOD_TASK.replace('network_mode             = "awsvpc"\n', "").replace('"1024"', '"8192"').replace("aws_iam_role.task.arn", "aws_iam_role.exec.arn").replace('"/ecs/app-${var.env}" } } }', '"/ecs/other" } } }').replace('target_type = "ip"', "")
    assert rules(bad) == {"fargate_network_mode_not_awsvpc", "fargate_cpu_memory_invalid", "task_role_same_as_execution_role", "awslogs_group_not_managed", "target_group_not_ip_for_fargate"}


def test_awslogs_create_group_option_suppresses_log_group_finding():
    t = GOOD_TASK.replace('"awslogs-group" = "/ecs/app-${var.env}"', '"awslogs-group" = "/ecs/x", "awslogs-create-group" = "true"')
    assert "awslogs_group_not_managed" not in rules(t)


def test_cpu_memory_table_edges():
    for cpu, mem, ok in [(256, 512, True), (256, 4096, False), (1024, 8192, True), (1024, 8193, False), (8192, 16384, True), (8192, 17408, False), (16384, 122880, True), (32768, 249856, True), (32768, 65536, False)]:
        t = GOOD_TASK.replace('cpu                      = "512"', f'cpu = "{cpu}"').replace('memory                   = "1024"', f'memory = "{mem}"')
        assert ("fargate_cpu_memory_invalid" in rules(t)) is (not ok), (cpu, mem)


def test_ec2_task_definition_is_not_checked_as_fargate():
    t = GOOD_TASK.replace('["FARGATE"]', '["EC2"]').replace('network_mode             = "awsvpc"\n', "")
    assert rules(t) == set()


def test_lambda_hash_and_sqs_visibility():
    t = '''
resource "aws_lambda_function" "w" { filename = "w.zip" timeout = 60 }
resource "aws_sqs_queue" "q" { name = "q" }
resource "aws_lambda_event_source_mapping" "m" {
  event_source_arn = aws_sqs_queue.q.arn
  function_name    = aws_lambda_function.w.arn
}
'''
    t = t.replace("{ filename", "{\n  filename").replace(" timeout = 60 }", "\n  timeout = 60\n}")
    assert rules(t) == {"lambda_no_source_code_hash", "sqs_visibility_below_lambda_timeout"}
    fixed = t.replace('filename = "w.zip"', 'filename = "w.zip"\n  source_code_hash = data.archive_file.w.output_base64sha256').replace('name = "q"', 'name = "q"\n  visibility_timeout_seconds = 360')
    assert rules(fixed) == set()
    assert "lambda_no_source_code_hash" not in rules(t.replace('filename = "w.zip"', 'filename = "w.zip"\n  code_sha256 = data.archive_file.w.output_base64sha256'))


def test_secrets_on_variables_and_outputs():
    t = '''
variable "db_password" {
  type    = string
  default = "x"
}
output "api_token" { value = "t" }
variable "db_password_ok" {
  type      = string
  sensitive = true
}
output "token_ok" {
  value     = "t"
  sensitive = true
}
variable "bucket_name" { default = "b" }
'''
    assert rules(t) == {"secret_variable_not_sensitive", "secret_variable_has_default", "secret_output_not_sensitive"}


def test_backend_locking():
    nolock = 'terraform {\n  backend "s3" {\n    bucket = "b"\n    key = "k"\n  }\n}\n'
    assert rules(nolock) == {"s3_backend_no_locking"}
    assert rules(nolock.replace('key = "k"', 'key = "k"\n    use_lockfile = true')) == set()
    assert rules(nolock.replace('key = "k"', 'key = "k"\n    dynamodb_table = "t"')) == set()


def test_text_patterns():
    t = 'locals {\n  a = "arn:aws:sqs:us-east-1:123456789012:q"\n  b = "123456789012.dkr.ecr.us-east-1.amazonaws.com/app"\n  c = "arn:aws:iam::aws:policy/X"\n  p = jsonencode({ Statement = [{ Action = ["*"], Resource = "*" }] })\n}\n'
    got = h.run_checks(h.scan_blocks(t, "l.tf"), {"l.tf": t})
    assert got == ["l.tf:2 hardcoded_account_id", "l.tf:3 hardcoded_account_id", "l.tf:5 iam_star_action"]


def test_scanner_survives_braces_in_strings_heredocs_and_comments():
    t = '''
# comment with { brace
resource "x" "a" {
  s = "a { b } ${var.c["k"]}"
  h = <<-EOT
    { not a block }
  EOT
  /* multi
  line } */
}
resource "y" "b" {}
'''
    blocks = h.scan_blocks(t, "t.tf")
    assert [(b["kind"], b["labels"], b["line"]) for b in blocks] == [("resource", ["x", "a"], 3), ("resource", ["y", "b"], 11)]


def test_planted_stack_findings_match_answer_key():
    """The fixture stack carries 34 planted defects (ANSWER-KEY.md). The
    structural checks must find exactly these, so a change to a rule that
    gains or loses one shows up here."""
    fixture = Path(__file__).resolve().parent / "fixtures" / "planted-stack"
    texts = {p.name: p.read_text() for p in sorted(fixture.glob("*.tf"))}
    blocks = [b for name, t in texts.items() for b in h.scan_blocks(t, name)]
    assert h.run_checks(blocks, texts) == [
        "ecs.tf:5 fargate_network_mode_not_awsvpc",
        "ecs.tf:9 fargate_cpu_memory_invalid",
        "ecs.tf:11 task_role_same_as_execution_role",
        "ecs.tf:16 hardcoded_account_id",
        "ecs.tf:26 awslogs_group_not_managed",
        "iam.tf:21 iam_star_action",
        "iam.tf:57 hardcoded_account_id",
        "lambda.tf:12 lambda_no_source_code_hash",
        "network.tf:35 target_group_not_ip_for_fargate",
        "outputs.tf:5 secret_output_not_sensitive",
        "providers.tf:11 s3_backend_no_locking",
        "variables.tf:7 secret_variable_not_sensitive",
        "variables.tf:9 secret_variable_has_default",
    ]
