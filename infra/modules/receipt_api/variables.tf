variable "name_prefix" {
  description = "Prefix for every resource name, e.g. receipt-intel-dev."
  type        = string
}

variable "lambda_source_dir" {
  description = "services/pipeline/src: the shared package (api.py reuses the validation rules)."
  type        = string
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "upload_prefix" {
  description = "Where the pipeline expects uploads, e.g. uploads/."
  type        = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "web_origins" {
  description = "Origins allowed to sign in and call the API, e.g. https://xxxx.cloudfront.net (no trailing slash)."
  type        = list(string)
}

variable "allow_admin_password_auth" {
  description = "Allow ADMIN_USER_PASSWORD_AUTH, so an admin with IAM credentials can get tokens from the CLI for smoke tests. Dev only."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  type    = number
  default = 14
}
