output "lambda_exec_role_arn" {
  value = aws_iam_role.lambda_exec.arn
}

output "lambda_exec_role_name" {
  value = aws_iam_role.lambda_exec.name
}
