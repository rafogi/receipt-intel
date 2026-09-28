# No profile is set here on purpose: locally, credentials come from
# AWS_PROFILE (your SSO profile); in CI, from the OIDC role.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Repository  = var.repository
    }
  }
}
