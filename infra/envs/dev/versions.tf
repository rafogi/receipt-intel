terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Partial backend config: the bucket name is supplied at init time.
  #   Locally: terraform init -backend-config=backend.hcl
  #   In CI:   terraform init -backend-config="bucket=$TF_STATE_BUCKET"
  backend "s3" {
    key          = "envs/dev/terraform.tfstate"
    region       = "us-west-2"
    use_lockfile = true
    encrypt      = true
  }
}
