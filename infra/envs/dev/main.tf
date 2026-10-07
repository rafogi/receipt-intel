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
  table_name         = module.pipeline.table_name
  table_arn          = module.pipeline.table_arn
  log_retention_days = 14

  # Local dev server for now; the CloudFront URL is added with the web app (Phase 3b).
  web_origins = ["http://localhost:5173"]

  # Lets an admin fetch tokens from the CLI for smoke tests (needs IAM admin credentials).
  allow_admin_password_auth = true
}
