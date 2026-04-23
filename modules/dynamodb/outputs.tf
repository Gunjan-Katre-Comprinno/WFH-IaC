output "wfh_requests_arn" {
  value = aws_dynamodb_table.wfh_requests.arn
}

output "wfh_users_arn" {
  value = aws_dynamodb_table.wfh_users.arn
}

output "wfh_audit_log_arn" {
  value = aws_dynamodb_table.wfh_audit_log.arn
}

output "wfh_settings_arn" {
  value = aws_dynamodb_table.wfh_settings.arn
}

output "wfh_requests_name" {
  value = aws_dynamodb_table.wfh_requests.name
}

output "wfh_users_name" {
  value = aws_dynamodb_table.wfh_users.name
}

output "wfh_audit_log_name" {
  value = aws_dynamodb_table.wfh_audit_log.name
}

output "wfh_settings_name" {
  value = aws_dynamodb_table.wfh_settings.name
}
