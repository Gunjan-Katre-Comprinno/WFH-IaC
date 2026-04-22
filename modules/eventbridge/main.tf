resource "aws_cloudwatch_event_rule" "wfh_reminder" {
  name                = "WFH-Reminder-Hourly"
  description         = "Trigger WFH reminder emails hourly"
  schedule_expression = "rate(1 hour)"
  state               = "ENABLED"
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.wfh_reminder.name
  target_id = "1"
  arn       = var.lambda_arn
}

resource "aws_cloudwatch_event_rule" "wfh_backup" {
  name                = "WFH-DynamoDB-Backup-Daily"
  description         = "Trigger daily DynamoDB backup to S3"
  schedule_expression = "cron(0 1 * * ? *)"
  state               = "ENABLED"
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "backup_lambda" {
  rule      = aws_cloudwatch_event_rule.wfh_backup.name
  target_id = "1"
  arn       = var.backup_lambda_arn
}

resource "aws_lambda_permission" "reminder" {
  statement_id  = "WFH-Reminder-Hourly-Permission"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_arn
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.wfh_reminder.arn
}

resource "aws_lambda_permission" "backup" {
  statement_id  = "WFH-DynamoDB-Backup-Daily-Permission"
  action        = "lambda:InvokeFunction"
  function_name = var.backup_lambda_arn
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.wfh_backup.arn
}
