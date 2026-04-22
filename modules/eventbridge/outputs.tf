output "rule_arn" {
  value = aws_cloudwatch_event_rule.wfh_reminder.arn
}

output "backup_rule_arn" {
  value = aws_cloudwatch_event_rule.wfh_backup.arn
}
