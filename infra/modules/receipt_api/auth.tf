data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
}

# Accounts are created by an admin only: nobody can register from the internet.
#   aws cognito-idp admin-create-user --user-pool-id <id> --username you@example.com
resource "aws_cognito_user_pool" "users" {
  name                = "${var.name_prefix}-users"
  user_pool_tier      = "ESSENTIALS" # free under 10,000 monthly active users
  deletion_protection = "ACTIVE"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  username_configuration {
    case_sensitive = false
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  # Optional TOTP (authenticator app) MFA; each user can turn it on.
  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

# Cognito-hosted sign-in pages (managed login) at
# https://<prefix>.auth.<region>.amazoncognito.com
resource "aws_cognito_user_pool_domain" "login" {
  domain                = "${var.name_prefix}-${local.account_id}"
  user_pool_id          = aws_cognito_user_pool.users.id
  managed_login_version = 2
}

# The phone web app: a public client (no secret), authorization code + PKCE.
resource "aws_cognito_user_pool_client" "web" {
  name         = "${var.name_prefix}-web"
  user_pool_id = aws_cognito_user_pool.users.id

  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = [for origin in var.web_origins : "${origin}/"]
  logout_urls                          = [for origin in var.web_origins : "${origin}/"]

  explicit_auth_flows = concat(
    ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"],
    var.allow_admin_password_auth ? ["ALLOW_ADMIN_USER_PASSWORD_AUTH"] : [],
  )

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# Managed login v2 shows no page for a client without a branding style
# (the console creates one automatically; the API doesn't).
resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id                = aws_cognito_user_pool.users.id
  client_id                   = aws_cognito_user_pool_client.web.id
  use_cognito_provided_values = true
}
