# ── WFH_Requests ─────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "wfh_requests" {
  name         = "${var.environment}-wfh-requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "request_id"
  tags         = merge(var.tags, { tool_name = "${var.environment}-wfh-requests" })

  attribute {
    name = "request_id"
    type = "S"
  }
  attribute {
    name = "member_id"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name            = "member-index"
    hash_key        = "member_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "status-index"
    hash_key        = "status"
    projection_type = "ALL"
  }
}

# ── wfh-users ─────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "wfh_users" {
  name         = "${var.environment}-wfh-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  tags         = merge(var.tags, { tool_name = "${var.environment}-wfh-users" })

  attribute {
    name = "user_id"
    type = "S"
  }
  attribute {
    name = "team"
    type = "S"
  }
  attribute {
    name = "role"
    type = "S"
  }

  global_secondary_index {
    name            = "team-role-index"
    hash_key        = "team"
    range_key       = "role"
    projection_type = "ALL"
  }
}

# ── WFH_Audit_Log ─────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "wfh_audit_log" {
  name         = "${var.environment}-wfh-audit-log"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "log_id"
  tags         = merge(var.tags, { tool_name = "${var.environment}-wfh-audit-log" })

  attribute {
    name = "log_id"
    type = "S"
  }
  attribute {
    name = "member_id"
    type = "S"
  }

  global_secondary_index {
    name            = "member-index"
    hash_key        = "member_id"
    projection_type = "ALL"
  }
}

# ── WFH-Settings ──────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "wfh_settings" {
  name         = "${var.environment}-wfh-settings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "setting_id"
  tags         = merge(var.tags, { tool_name = "${var.environment}-wfh-settings" })

  attribute {
    name = "setting_id"
    type = "S"
  }
}
