resource "aws_iam_role" "lambda_exec" {
  name        = "${var.environment}-wfh-lambda-execution-role"
  description = "Allows Lambda functions to call AWS services on your behalf."
  tags        = merge(var.tags, { tool_name = "${var.environment}-wfh-lambda-execution-role" })

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# ── Managed policy attachments ────────────────────────────────────────────────

resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "ses_full" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSESFullAccess"
}

resource "aws_iam_policy" "cognito_access" {
  name        = "${var.environment}-wfh-cognito-access"
  description = "Allows Lambda to manage Cognito users for WFH system"
  tags        = merge(var.tags, { tool_name = "${var.environment}-wfh-cognito-access" })

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "cognito-idp:AdminGetUser",
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminSetUserPassword",
        "cognito-idp:AdminUpdateUserAttributes",
        "cognito-idp:AdminDeleteUser"
      ]
      Resource = var.cognito_user_pool_arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cognito_access" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.cognito_access.arn

}

# ── Inline policies ───────────────────────────────────────────────────────────

resource "aws_iam_role_policy" "dynamodb_crud" {
  name = "${var.environment}-wfh-dynamodb-inline-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          "arn:aws:dynamodb:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:table/WFH*",
          "arn:aws:dynamodb:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:table/WFH*/index/*",
          "arn:aws:dynamodb:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:table/wfh*",
          "arn:aws:dynamodb:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:table/wfh*/index/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "s3_backup_access" {
  name = "${var.environment}-wfh-s3-backup-access"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:PutObjectAcl"]
      Resource = "${var.backup_bucket_arn}/*"
    }]
  })
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
