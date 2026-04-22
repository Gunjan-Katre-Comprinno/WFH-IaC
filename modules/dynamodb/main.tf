# ── WFH_Requests ─────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "wfh_requests" {
  name         = "WFH_Requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "request_id"
  tags         = var.tags

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
  name         = "wfh-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  tags         = var.tags

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
  name         = "WFH_Audit_Log"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "log_id"
  tags         = var.tags

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
  name         = "WFH-Settings"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "setting_id"
  tags         = var.tags

  attribute {
    name = "setting_id"
    type = "S"
  }
}
