# Log groups are created by the Lambda module to avoid race conditions.
# This module manages any additional CloudWatch alarms or dashboards.

# ── Optional: Alarm for Lambda errors ────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "main_lambda_errors" {
  alarm_name          = "WFH-Management-Function-Errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when WFH-Management-Function has errors"
  treat_missing_data  = "notBreaching"
  tags                = var.tags

  dimensions = {
    FunctionName = "WFH-Management-Function"
  }
}

resource "aws_cloudwatch_metric_alarm" "backup_lambda_errors" {
  alarm_name          = "WFH-DynamoDB-Backup-Errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when WFH-DynamoDB-Backup has errors"
  treat_missing_data  = "notBreaching"
  tags                = var.tags

  dimensions = {
    FunctionName = "WFH-DynamoDB-Backup"
  }
}
