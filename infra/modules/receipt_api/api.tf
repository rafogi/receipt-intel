# HTTP API -> one Lambda (services/pipeline/src/api.py). Every route requires
# a valid Cognito access token; the Lambda takes the user id from its `sub`.

locals {
  routes = [
    "POST /uploads",
    "GET /receipts",
    "GET /receipts/{id}",
    "PATCH /receipts/{id}",
  ]
}

data "aws_iam_policy_document" "api_function" {
  statement {
    sid     = "ReceiptsTable"
    actions = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [
      var.table_arn,
      "${var.table_arn}/index/*",
    ]
  }

  # Presigned URLs carry the signer's permissions: POST for new uploads,
  # GET for showing the photo. Both limited to the uploads prefix.
  statement {
    sid       = "PresignUploads"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${var.bucket_arn}/${var.upload_prefix}*"]
  }
}

module "api_function" {
  source = "../python_lambda"

  name               = "${var.name_prefix}-api"
  source_dir         = var.lambda_source_dir
  handler            = "api.handler"
  timeout            = 10
  memory_size        = 256
  log_retention_days = var.log_retention_days
  inline_policies    = { "receipts-api" = data.aws_iam_policy_document.api_function.json }

  environment_variables = {
    BUCKET        = var.bucket_name
    TABLE         = var.table_name
    UPLOAD_PREFIX = var.upload_prefix
  }
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name_prefix}-api"
  protocol_type = "HTTP"

  # API Gateway answers CORS preflight (OPTIONS) itself, without auth.
  cors_configuration {
    allow_origins = var.web_origins
    allow_methods = ["GET", "POST", "PATCH", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.http.id
  name             = "cognito"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  # Access tokens carry the app client id as client_id, which the JWT
  # authorizer accepts as the audience.
  jwt_configuration {
    issuer   = "https://${aws_cognito_user_pool.users.endpoint}"
    audience = [aws_cognito_user_pool_client.web.id]
  }
}

resource "aws_apigatewayv2_integration" "api_function" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api_function.function_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "this" {
  for_each = toset(local.routes)

  api_id             = aws_apigatewayv2_api.http.id
  route_key          = each.value
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
  target             = "integrations/${aws_apigatewayv2_integration.api_function.id}"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${var.name_prefix}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  # A personal app: low limits cap the cost of a leaked token or a buggy loop.
  default_route_settings {
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }

  # Request metadata only; no bodies (they contain receipt data).
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      time      = "$context.requestTime"
      routeKey  = "$context.routeKey"
      status    = "$context.status"
      latencyMs = "$context.responseLatency"
      authError = "$context.authorizer.error"
    })
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = module.api_function.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

# Browsers POST photos straight to S3 with the presigned form.
resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = var.bucket_name

  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = var.web_origins
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}
