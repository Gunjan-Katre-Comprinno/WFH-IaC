variable "project" { type = string }
variable "environment" { type = string }
variable "tags" { type = map(string) }
variable "lambda_arn" { type = string }
variable "backup_lambda_arn" { type = string }
