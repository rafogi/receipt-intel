variable "project" {
  type    = string
  default = "receipt-intel"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "notification_email" {
  description = "Where budget alerts and weekly summaries go. Set in CI from the NOTIFICATION_EMAIL repository variable, not committed."
  type        = string
  default     = ""
}

variable "repository" {
  description = "Repo name recorded in resource tags, for tracing resources back to code."
  type        = string
  default     = "receipt-intel"
}
