terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket = "terraform-tfstate-wfh"
    key    = "wfh-management-system/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# ACM certificate must live in us-east-1 for CloudFront
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# ── Modules ──────────────────────────────────────────────────────────────────

module "iam" {
  source = "./modules/iam"

  project     = var.project
  environment = var.environment
  tags        = local.common_tags

  cognito_user_pool_arn = module.cognito.user_pool_arn
  backup_bucket_arn     = module.s3.backup_bucket_arn
}

module "dynamodb" {
  source = "./modules/dynamodb"

  project     = var.project
  environment = var.environment
  tags        = local.common_tags
}

module "cognito" {
  source = "./modules/cognito"

  project     = var.project
  environment = var.environment
  tags        = local.common_tags
}

module "s3" {
  source = "./modules/s3"

  project                     = var.project
  environment                 = var.environment
  tags                        = local.common_tags
  cloudfront_distribution_arn = module.cloudfront.distribution_arn
}

module "lambda" {
  source = "./modules/lambda"

  project     = var.project
  environment = var.environment
  tags        = local.common_tags

  lambda_exec_role_arn = module.iam.lambda_exec_role_arn
}

module "api_gateway" {
  source = "./modules/api_gateway"

  project     = var.project
  environment = var.environment
  tags        = local.common_tags

  lambda_invoke_arn = module.lambda.main_function_invoke_arn
  lambda_arn        = module.lambda.main_function_arn
}

module "eventbridge" {
  source = "./modules/eventbridge"

  project     = var.project
  environment = var.environment
  tags        = local.common_tags

  lambda_arn        = module.lambda.main_function_arn
  backup_lambda_arn = module.lambda.backup_function_arn
}

module "cloudwatch" {
  source = "./modules/cloudwatch"

  project     = var.project
  environment = var.environment
  tags        = local.common_tags
}

module "cloudfront" {
  source = "./modules/cloudfront"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  project                          = var.project
  environment                      = var.environment
  tags                             = local.common_tags
  domain_name                      = var.domain_name
  acm_certificate_arn              = var.acm_certificate_arn
  frontend_bucket_id               = module.s3.frontend_bucket_id
  frontend_bucket_regional_domain  = module.s3.frontend_bucket_regional_domain
}
