locals {
  name_prefix = "${var.project}-${var.environment}"
}

# Phase 0 smoke test: proves the full path from pull request to deployed code.
# Replaced by the real pipeline modules from Phase 2 onward.
module "hello" {
  source = "../../modules/hello_lambda"

  name               = "${local.name_prefix}-hello"
  source_dir         = "${path.root}/../../../services/hello"
  log_retention_days = 14

  environment_variables = {
    APP_ENV = var.environment
  }
}
