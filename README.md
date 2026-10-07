# receipt-intel

Event-driven document intelligence on AWS: receipt photos, PDFs, and forwarded
email receipts become categorized spending data, using Textract, Amazon Bedrock,
and Step Functions, with everything deployed from Terraform.

> Status: Phase 2 (core pipeline). See [docs/PLAN.md](docs/PLAN.md) for the
> roadmap. Architecture, ADRs, and results are added here as each phase lands.

## Repository layout

```
receipt-intel/
├── bootstrap/              # State bucket + GitHub OIDC roles (run once, locally)
├── infra/
│   ├── modules/            # Reusable Terraform modules
│   └── envs/dev/           # The deployed environment (applied by CI)
├── services/               # Lambda code (Python)
├── web/                    # Phone web app (Phase 3)
├── agents/                 # Strands agents (Phase 7)
├── edge/                   # Raspberry Pi edge gateway (Phase 8)
├── eval/                   # Test set tooling and evaluation (Phase 1)
├── docs/adr/               # Architecture decision records
└── .github/workflows/      # CI/CD
```

**Rule of thumb:** one-time account setup is manual; anything that makes up the
application is Terraform; CI applies it. Plan locally if you like, but let
GitHub Actions do the applies to `dev`.

## Phase 0 setup

### 1. Prerequisites
- AWS account with an IAM Identity Center admin user (MFA on), budget alerts set
- AWS CLI v2, Terraform 1.10 or newer, Python 3.12+, Git
- A GitHub repository named `receipt-intel` (private is fine)

### 2. Sign in to AWS from the terminal
```bash
aws configure sso
#   SSO start URL:  your access portal URL (https://d-xxxxxxxxxx.awsapps.com/start)
#   SSO region:     us-west-2
#   Default region: us-west-2
#   Profile name:   receipt-intel-admin
aws sso login --profile receipt-intel-admin
export AWS_PROFILE=receipt-intel-admin     # PowerShell: $env:AWS_PROFILE="receipt-intel-admin"
aws sts get-caller-identity                # should show your account and admin role
```

### 3. Bootstrap state and CI roles
```bash
cd bootstrap
cp terraform.tfvars.example terraform.tfvars   # set github_owner / github_repo
terraform init
terraform plan
terraform apply
terraform output                                # keep these values
```
Then move the bootstrap state into the bucket it just created:
1. In `bootstrap/versions.tf`, uncomment the `backend "s3"` block and set
   `bucket` to the `state_bucket` output.
2. Run `terraform init -migrate-state` and answer `yes`.
3. Delete the local `terraform.tfstate` files once the migration succeeds.

### 4. Configure GitHub
In the repository settings:
- **Secrets and variables → Actions → Variables** (variables, not secrets):
  - `TF_STATE_BUCKET` = `state_bucket` output
  - `AWS_PLAN_ROLE_ARN` = `plan_role_arn` output
  - `AWS_APPLY_ROLE_ARN` = `apply_role_arn` output
- **Environments → New environment → `dev`**, then under deployment branches
  choose *Selected branches* and allow only `main`.

Required reviewers on environments and branch protection on `main` need a
public repo or a paid GitHub plan. They're worth turning on when you make the
repo public.

### 5. First deploy
```bash
git checkout -b phase-0-hello
git push -u origin phase-0-hello
```
Open a pull request: the **plan** job runs and posts the plan to the job
summary. Merge it: the **apply** job deploys the `dev` environment.
(Phase 0 deployed a hello-world Lambda here; Phase 2 replaced it with the
receipt pipeline below.)

### Working locally
```bash
cd infra/envs/dev
cp backend.hcl.example backend.hcl        # set the bucket name
terraform init "-backend-config=backend.hcl"   # quotes needed in PowerShell
terraform plan
```

## Receipt pipeline (Phase 2)

```
S3 uploads/{userId}/{receiptId}.jpg
  → EventBridge → Step Functions
      Claim (idempotency) → file type → Analyze (Textract) → Extract → Validate
      → Save → review queue if needed → ReceiptProcessed event
  failures: retries with backoff → needs_manual_entry + pipeline DLQ
```

Terraform: `infra/modules/receipt_pipeline`. Lambda code and tests:
`services/pipeline` (`src/` is deployed; `tests/` runs in CI).

Until the extraction approach is chosen (ADR-0003), the Extract step uses
Textract's own fields with no category, so every receipt lands in the review
queue with `category_missing`. That is expected.

### Try it
```powershell
cd infra/envs/dev
$bucket = terraform output -raw receipts_bucket
$table  = terraform output -raw receipts_table
aws s3 cp C:\path\to\receipt.jpg "s3://$bucket/uploads/test-user/r001.jpg"   # ~1¢ of Textract
# A few seconds later:
aws dynamodb get-item --table-name $table --key '{\"userId\":{\"S\":\"test-user\"},\"receiptId\":{\"S\":\"r001\"}}'
```
Executions are visible in the Step Functions console (`receipt-intel-dev-pipeline`).

### Run the unit tests
```powershell
python -m pip install -r services/pipeline/requirements-dev.txt
python -m pytest services/pipeline -q
```

## Architecture decisions
- [ADR-0001: Deploy to us-west-2](docs/adr/0001-region-selection.md)
- [ADR-0002: Terraform state and CI/CD authentication](docs/adr/0002-terraform-state-and-ci-auth.md)
- [ADR-0004: Direct service integrations vs. Lambda wrappers](docs/adr/0004-direct-integrations-vs-lambda.md)
- [ADR-0005: Rule-based validation gate](docs/adr/0005-rule-based-validation.md)
- [ADR-0006: Idempotency strategy](docs/adr/0006-idempotency.md)
