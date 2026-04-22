output "main_function_arn" {
  value = aws_lambda_function.main.arn
}

output "main_function_invoke_arn" {
  value = aws_lambda_function.main.invoke_arn
}

output "backup_function_arn" {
  value = aws_lambda_function.backup.arn
}
