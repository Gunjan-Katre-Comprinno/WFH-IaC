resource "aws_cognito_user_pool" "main" {
  name = "${var.environment}-wfh-user-pool"
  tags = merge(var.tags, { tool_name = "${var.environment}-wfh-user-pool" })

  username_attributes      = ["email"]
  auto_verified_attributes = []

  deletion_protection = "INACTIVE"

  password_policy {
    minimum_length                   = 8
    require_uppercase                = true
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  mfa_configuration = "OFF"

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = false

    invite_message_template {
      email_subject = "WFH Management System — Your Account Has Been Created"
      email_message = <<-HTML
        <div style='max-width:500px;margin:0 auto;font-family:Arial,sans-serif;'>
          <div style='background:#1b2a49;padding:20px;text-align:center;border-radius:8px 8px 0 0;'>
            <p style='color:#ff9472;margin:4px 0 0;font-size:13px;'>Learning & Development Platform</p>
          </div>
          <div style='background:#fff;padding:24px;border:1px solid #e0e0e0;'>
            <h2 style='color:#1b2a49;margin-top:0;'>Welcome!</h2>
            <p>An account has been created for you on WFH Management System.</p>
            <table style='width:100%;border-collapse:collapse;margin:16px 0;'>
              <tr><td style='padding:8px;background:#f8f9fa;font-weight:bold;border:1px solid #e0e0e0;'>Username</td><td style='padding:8px;border:1px solid #e0e0e0;'>{username}</td></tr>
              <tr><td style='padding:8px;background:#f8f9fa;font-weight:bold;border:1px solid #e0e0e0;'>Temporary Password</td><td style='padding:8px;border:1px solid #e0e0e0;font-family:monospace;'>{####}</td></tr>
            </table>
            <p>Please log in and change your password on first login.</p>
          </div>
          <div style='padding:12px;text-align:center;color:#999;font-size:11px;'>Comprinno Technologies</div>
        </div>
      HTML
      sms_message = "Username: {username} Temp password: {####}"
    }
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
    email_subject        = "WFH Management System — Verify Your Email Address"
    email_message        = "Your verification code is: {####}"
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # Custom attributes
  schema {
    name                     = "role"
    attribute_data_type      = "String"
    mutable                  = true
    developer_only_attribute = false
    string_attribute_constraints {}
  }

  schema {
    name                     = "team"
    attribute_data_type      = "String"
    mutable                  = true
    developer_only_attribute = false
    string_attribute_constraints {
      min_length = "0"
      max_length = "256"
    }
  }

  schema {
    name                     = "manager"
    attribute_data_type      = "String"
    mutable                  = true
    developer_only_attribute = false
    string_attribute_constraints {
      min_length = "0"
      max_length = "256"
    }
  }

  lambda_config {
    pre_sign_up = aws_lambda_function.presignup.arn
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "prod-wfh-management-comprinno"
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.environment}-wfh-web-client"
  user_pool_id = aws_cognito_user_pool.main.id

  refresh_token_validity = 30
  token_validity_units {
    refresh_token = "days"
  }

  explicit_auth_flows = [
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH"
  ]

  enable_token_revocation               = true
  prevent_user_existence_errors         = "ENABLED"
  allowed_oauth_flows_user_pool_client  = false
}

# ── Pre-signup Lambda (restricts signup to @comprinno.net) ────────────────────

resource "aws_iam_role" "presignup_lambda" {
  name = "${var.environment}-wfh-cognito-presignup-role"
  tags = merge(var.tags, { tool_name = "${var.environment}-wfh-cognito-presignup-role" })

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "presignup_basic" {
  role       = aws_iam_role.presignup_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "presignup" {
  function_name = "${var.environment}-wfh-cognito-presignup"
  role          = aws_iam_role.presignup_lambda.arn
  handler       = "presignup_lambda.lambda_handler"
  runtime       = "python3.11"
  timeout       = 5
  memory_size   = 128
  tags          = merge(var.tags, { tool_name = "${var.environment}-wfh-cognito-presignup" })

  filename         = "${path.module}/presignup_lambda.zip"
  source_code_hash = filebase64sha256("${path.module}/presignup_lambda.zip")
}

resource "aws_lambda_permission" "cognito_presignup" {
  statement_id  = "AllowCognitoInvokePreSignup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.presignup.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.main.arn
}
