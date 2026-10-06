variable "name_prefix" {
  description = "Prefix for every resource name, e.g. receipt-intel-dev."
  type        = string
}

variable "lambda_source_dir" {
  description = "services/pipeline/src: shared package for the Analyze and Validate functions."
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "deletion_protection" {
  description = "Protect the receipts table from deletion (turn off only to tear an environment down)."
  type        = bool
  default     = true
}
