variable "project" {
  description = "Short project name used as a prefix for resource names."
  type        = string
  default     = "receipt-intel"
}

variable "region" {
  description = "AWS region for all project resources (see ADR-0001)."
  type        = string
  default     = "us-west-2"
}

variable "github_owner" {
  description = "GitHub user or organization that owns the repo. Must match GitHub's casing exactly."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name. Must match GitHub's casing exactly."
  type        = string
}

variable "deploy_environment" {
  description = "GitHub Actions environment name allowed to assume the apply role."
  type        = string
  default     = "dev"
}
