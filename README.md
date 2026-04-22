# WFH Management System — Terraform

Modular Terraform code that recreates the complete WFH Management System infrastructure on AWS.

## Structure

```
terraform/
├── main.tf                  # Root module — wires all child modules
├── variables.tf             # Input variables
├── locals.tf                # Common tags
├── outputs.tf               # Key outputs
├── terraform.tfvars.example # Example variable values
└── modules/
    ├── iam/                 # IAM role + all policies for Lambda
    ├── cognito/             # Cognito User Pool + App Client
    ├── dynamodb/            # 4 DynamoDB tables with GSIs
    ├── s3/                  # Frontend (public) + backup (private) buckets
    ├── lambda/              # WFH-Management-Function + WFH-DynamoDB-Backup
    ├── api_gateway/         # REST API (EDGE) + prod stage
    ├── eventbridge/         # Hourly reminder rule → Lambda
    ├── cloudwatch/          # Lambda error alarms
    └── cloudfront/          # CDN + OAC + HTTPS + WAF
```

## Prerequisites

1. AWS CLI configured with sufficient permissions
2. An S3 bucket for Terraform remote state
3. An ACM certificate in **us-east-1** for `wfh.comprinno.net`
4. Lambda deployment packages (replace placeholder zips in `modules/lambda/`)

## Usage

```bash
# 1. Copy and fill in variables
cp terraform.tfvars.example terraform.tfvars

# 2. Initialise with remote state
terraform init \
  -backend-config="bucket=<your-state-bucket>" \
  -backend-config="key=wfh-management-system/terraform.tfstate" \
  -backend-config="region=ap-south-1"

# 3. Plan
terraform plan

# 4. Apply
terraform apply
```

## Deploying Lambda code

After `terraform apply`, upload the actual Lambda packages:

```bash
# Main function
aws lambda update-function-code \
  --function-name WFH-Management-Function \
  --zip-file fileb://lambda_function.zip \
  --region ap-south-1

# Backup function
aws lambda update-function-code \
  --function-name WFH-DynamoDB-Backup \
  --zip-file fileb://backup_lambda.zip \
  --region ap-south-1
```

## Notes

- **No VPC** — Lambda communicates with DynamoDB, SES, Cognito, and API Gateway over public AWS endpoints secured by IAM.
- **CloudFront WAF** — the `waf_web_acl_arn` variable in the `cloudfront` module is optional; leave blank to skip WAF attachment.
- **S3 circular dependency** — the `s3` module needs the CloudFront distribution ARN for its bucket policy, and `cloudfront` needs the S3 bucket. Terraform resolves this correctly because the ARN is computed before the policy is applied.
