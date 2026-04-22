# ── WFH-Management-Function ───────────────────────────────────────────────────

resource "aws_lambda_function" "main" {
  function_name = "WFH-Management-Function"
  role          = var.lambda_exec_role_arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 128
  architectures = ["x86_64"]
  tags          = var.tags

  # Upload a placeholder zip; replace with actual deployment package
  filename         = "${path.module}/placeholder.zip"
  source_code_hash = filebase64sha256("${path.module}/placeholder.zip")

  logging_config {
    log_format = "Text"
    log_group  = "/aws/lambda/WFH-Management-Function"
  }

  depends_on = [aws_cloudwatch_log_group.main]
}

# ── WFH-DynamoDB-Backup ───────────────────────────────────────────────────────

resource "aws_lambda_function" "backup" {
  function_name = "WFH-DynamoDB-Backup"
  role          = var.lambda_exec_role_arn
  handler       = "backup_lambda.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 256
  architectures = ["x86_64"]
  tags          = var.tags

  filename         = "${path.module}/placeholder_backup.zip"
  source_code_hash = filebase64sha256("${path.module}/placeholder_backup.zip")

  logging_config {
    log_format = "Text"
    log_group  = "/aws/lambda/WFH-DynamoDB-Backup"
  }

  depends_on = [aws_cloudwatch_log_group.backup]
}

# ── CloudWatch Log Groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "main" {
  name              = "/aws/lambda/WFH-Management-Function"
  retention_in_days = 30
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "backup" {
  name              = "/aws/lambda/WFH-DynamoDB-Backup"
  retention_in_days = 30
  tags              = var.tags
}

# ── Resource-based policies ───────────────────────────────────────────────────

# Resource-based policies are managed in the api_gateway and eventbridge modules
# to avoid circular dependencies.
