---
type: regex
target: last_message
pattern: "execution_role_arn\\s*=\\s*aws_iam_role\\.(\\w+)\\.arn[\\s\\S]*task_role_arn\\s*=\\s*aws_iam_role\\.(?!\\1\\b)\\w+\\.arn"
---
Execution role and task role are different roles. The regex fails when the same role name is used for both.
