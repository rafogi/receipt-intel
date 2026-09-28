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

variable "repository" {
  description = "Repo name recorded in resource tags, for tracing resources back to code."
  type        = string
  default     = "receipt-intel"
}
