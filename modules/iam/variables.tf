variable "project" { type = string }
variable "environment" { type = string }
variable "tags" { type = map(string) }
variable "cognito_user_pool_arn" { type = string }
variable "backup_bucket_arn" { type = string }
