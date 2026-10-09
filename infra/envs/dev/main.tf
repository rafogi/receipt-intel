locals {
  name_prefix = "${var.project}-${var.environment}"
}

# Phase 2: S3 upload -> EventBridge -> Step Functions -> DynamoDB / review queue.
module "pipeline" {
  source = "../../modules/receipt_pipeline"

  name_prefix        = local.name_prefix
  lambda_source_dir  = "${path.root}/../../../services/pipeline/src"
  log_retention_days = 14
}

# Phase 3: Cognito sign-in + HTTP API for the phone web app.
module "api" {
  source = "../../modules/receipt_api"

  name_prefix        = local.name_prefix
  lambda_source_dir  = "${path.root}/../../../services/pipeline/src"
  bucket_name        = module.pipeline.bucket_name
  bucket_arn         = module.pipeline.bucket_arn
  upload_prefix      = module.pipeline.upload_prefix
  raw_prefix         = module.pipeline.raw_prefix
  table_name         = module.pipeline.table_name
  table_arn          = module.pipeline.table_arn
  log_retention_days = 14

  # The deployed app, plus the local dev server (npm run dev).
  web_origins = [module.web.url, "http://localhost:5173"]

  # Lets an admin fetch tokens from the CLI for smoke tests (needs IAM admin credentials).
  allow_admin_password_auth = true
}

# Phase 3b: the phone web app (S3 + CloudFront). CI uploads the build.
module "web" {
  source = "../../modules/web_hosting"

  name_prefix = local.name_prefix
}

# Runtime settings for the app; CI's file sync leaves this object alone.
resource "aws_s3_object" "web_config" {
  bucket        = module.web.bucket_name
  key           = "config.json"
  content_type  = "application/json"
  cache_control = "no-cache"
  content = jsonencode({
    apiUrl      = module.api.api_url
    issuer      = module.api.issuer
    clientId    = module.api.web_client_id
    loginDomain = module.api.login_domain
  })
}
