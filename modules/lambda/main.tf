# ── WFH-Management-Function ───────────────────────────────────────────────────

resource "aws_lambda_function" "main" {
  function_name = "${var.environment}-wfh-management-function"
  role          = var.lambda_exec_role_arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  timeout       = 30
  memory_size   = 128
  architectures = ["x86_64"]
  tags          = merge(var.tags, { tool_name = "${var.environment}-wfh-management-function" })

  filename         = "${path.module}/lambda_function.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda_function.zip")

  environment {
    variables = {
      COGNITO_USER_POOL_ID = var.cognito_user_pool_id
      CORS_ORIGIN          = var.cors_origin
      SES_SENDER           = var.ses_sender
      TABLE_WFH_REQUESTS   = var.table_wfh_requests
      TABLE_WFH_USERS      = var.table_wfh_users
      TABLE_WFH_AUDIT_LOG  = var.table_wfh_audit_log
      TABLE_WFH_SETTINGS   = var.table_wfh_settings
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = "/aws/lambda/${var.environment}-wfh-management-function"
  }

  depends_on = [aws_cloudwatch_log_group.main]
}

# ── WFH-DynamoDB-Backup ───────────────────────────────────────────────────────

resource "aws_lambda_function" "backup" {
  function_name = "${var.environment}-wfh-dynamodb-backup"
  role          = var.lambda_exec_role_arn
  handler       = "backup_lambda.lambda_handler"
  runtime       = "python3.11"
  timeout       = 300
  memory_size   = 256
  architectures = ["x86_64"]
  tags          = merge(var.tags, { tool_name = "${var.environment}-wfh-dynamodb-backup" })

  filename         = "${path.module}/backup_lambda.zip"
  source_code_hash = filebase64sha256("${path.module}/backup_lambda.zip")

  environment {
    variables = {
      BACKUP_BUCKET_NAME  = var.backup_bucket_name
      TABLE_WFH_REQUESTS  = var.table_wfh_requests
      TABLE_WFH_USERS     = var.table_wfh_users
      TABLE_WFH_SETTINGS  = var.table_wfh_settings
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = "/aws/lambda/${var.environment}-wfh-dynamodb-backup"
  }

  depends_on = [aws_cloudwatch_log_group.backup]
}

# ── CloudWatch Log Groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "main" {
  name              = "/aws/lambda/${var.environment}-wfh-management-function"
  retention_in_days = 30
  tags              = merge(var.tags, { tool_name = "${var.environment}-wfh-management-function-logs" })
}

resource "aws_cloudwatch_log_group" "backup" {
  name              = "/aws/lambda/${var.environment}-wfh-dynamodb-backup"
  retention_in_days = 30
  tags              = merge(var.tags, { tool_name = "${var.environment}-wfh-dynamodb-backup-logs" })
}

# ── Resource-based policies ───────────────────────────────────────────────────

# Resource-based policies are managed in the api_gateway and eventbridge modules
# to avoid circular dependencies.
