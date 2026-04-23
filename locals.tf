data "aws_caller_identity" "current" {}

locals {
  common_tags = {
    purpose     = "internal-tool"
    environment = var.environment
    project     = var.project
    owner       = var.owner
    managed_by  = "terraform"
  }
}
