variable "project" { type = string }
variable "environment" { type = string }
variable "tags" { type = map(string) }
variable "lambda_exec_role_arn" { type = string }
variable "cognito_user_pool_id" { type = string }
variable "backup_bucket_name" { type = string }
variable "ses_sender" { type = string }
variable "cors_origin" {
  type    = string
  default = ""
}
variable "table_wfh_requests" { type = string }
variable "table_wfh_users" { type = string }
variable "table_wfh_audit_log" { type = string }
variable "table_wfh_settings" { type = string }
