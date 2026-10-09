# Both functions ship the same package (services/pipeline/src) with
# different handlers, so they share receipt_schema.py and the parsing code.

data "aws_iam_policy_document" "analyze" {
  statement {
    sid       = "AnalyzeExpense"
    actions   = ["textract:AnalyzeExpense"]
    resources = ["*"] # Textract has no resource-level permissions
  }

  # Textract reads the photo from S3 with the caller's permissions.
  statement {
    sid       = "ReadUploads"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.receipts.arn}/${local.upload_prefix}*"]
  }

  statement {
    sid       = "WriteRawTextract"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.receipts.arn}/${local.raw_prefix}*"]
  }
}

module "analyze" {
  source = "../python_lambda"

  name               = "${var.name_prefix}-analyze"
  source_dir         = var.lambda_source_dir
  handler            = "analyze.handler"
  timeout            = 60
  memory_size        = 256
  log_retention_days = var.log_retention_days
  inline_policies    = { "textract-and-s3" = data.aws_iam_policy_document.analyze.json }

  environment_variables = {
    RAW_PREFIX = local.raw_prefix
  }
}

# Validate reads (never writes): same-date receipts, to flag a second photo
# of a receipt already saved.
data "aws_iam_policy_document" "validate" {
  statement {
    sid       = "FindDuplicates"
    actions   = ["dynamodb:Query"]
    resources = ["${aws_dynamodb_table.receipts.arn}/index/byDate"]
  }
}

module "validate" {
  source = "../python_lambda"

  name               = "${var.name_prefix}-validate"
  source_dir         = var.lambda_source_dir
  handler            = "validate.handler"
  timeout            = 10
  memory_size        = 128
  log_retention_days = var.log_retention_days
  inline_policies    = { "find-duplicates" = data.aws_iam_policy_document.validate.json }

  environment_variables = {
    TABLE = aws_dynamodb_table.receipts.name
  }
}
