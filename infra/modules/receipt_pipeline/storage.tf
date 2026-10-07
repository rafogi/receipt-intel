data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # Uploads land under uploads/{userId}/{receiptId}.{ext}; the workflow writes
  # the raw Textract response under textract/ (outside the trigger prefix).
  upload_prefix = "uploads/"
  raw_prefix    = "textract/"
}

# ---------------------------------------------------------------- receipts bucket

resource "aws_s3_bucket" "receipts" {
  bucket = "${var.name_prefix}-receipts-${local.account_id}"
}

resource "aws_s3_bucket_ownership_controls" "receipts" {
  bucket = aws_s3_bucket.receipts.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "receipts" {
  bucket                  = aws_s3_bucket.receipts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3, not a customer-managed KMS key (see the cost notes in docs/PLAN.md).
resource "aws_s3_bucket_server_side_encryption_configuration" "receipts" {
  bucket = aws_s3_bucket.receipts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning guards against accidental overwrite or delete of a receipt photo.
resource "aws_s3_bucket_versioning" "receipts" {
  bucket = aws_s3_bucket.receipts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "receipts" {
  bucket = aws_s3_bucket.receipts.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.receipts]
}

data "aws_iam_policy_document" "receipts_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.receipts.arn, "${aws_s3_bucket.receipts.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "receipts" {
  bucket = aws_s3_bucket.receipts.id
  policy = data.aws_iam_policy_document.receipts_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.receipts]
}

# Send "Object Created" events to EventBridge (the trigger rule filters them).
resource "aws_s3_bucket_notification" "receipts" {
  bucket      = aws_s3_bucket.receipts.id
  eventbridge = true
}

# ---------------------------------------------------------------- receipts table

# One item per receipt: userId + receiptId, so the app can get, edit, and
# reprocess a receipt by id. The local secondary indexes serve the two list
# views: by purchase date (dateKey = date#receiptId) and by status
# (statusKey = status#timestamp). LSIs must be created with the table.
resource "aws_dynamodb_table" "receipts" {
  name                        = "${var.name_prefix}-receipts"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "userId"
  range_key                   = "receiptId"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "receiptId"
    type = "S"
  }

  attribute {
    name = "dateKey"
    type = "S"
  }

  attribute {
    name = "statusKey"
    type = "S"
  }

  local_secondary_index {
    name            = "byDate"
    range_key       = "dateKey"
    projection_type = "ALL"
  }

  local_secondary_index {
    name            = "byStatus"
    range_key       = "statusKey"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }
}
