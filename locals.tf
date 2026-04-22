data "aws_caller_identity" "current" {}

locals {
  common_tags = {
    tool_name   = "wfh-management-system"
    purpose     = "internal-tool"
    environment = var.environment
    project     = var.project
    owner       = data.aws_caller_identity.current.arn
    managed_by  = "terraform"
  }
}
