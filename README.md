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

## API and sign-in (Phase 3)

Cognito user pool (admin-created accounts only, optional TOTP MFA, hosted
sign-in pages) and an HTTP API with a JWT authorizer. Terraform:
`infra/modules/receipt_api`; code: `services/pipeline/src/api.py`.

| Route | Does |
|---|---|
| `POST /uploads` | Presigned POST for a new photo (JPEG/PNG, ≤ 10 MB) |
| `GET /receipts` | Newest first; `?month=YYYY-MM` or `?status=needs_attention` (also `needs_review`, `needs_manual_entry`, `processing`, `processed`); `?limit=`, `?nextToken=` |
| `GET /receipts/{id}` | One receipt plus a 10-minute photo URL |
| `PATCH /receipts/{id}` | Manual fix of `store`, `date`, `subtotal`, `gst`, `pst`, `total`, `category`; saved as `source=manual` (422 lists what's still missing). Saving also confirms a `possible_duplicate` isn't one |
| `DELETE /receipts/{id}` | Removes the receipt, its photo, and its raw Textract output (S3 versioning keeps the files 30 days) |

### Create your account
Self sign-up is off. Create users with the CLI (Cognito emails a temporary password):
```powershell
cd infra/envs/dev
aws cognito-idp admin-create-user --user-pool-id (terraform output -raw user_pool_id) `
  --username you@example.com --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
```

### Call the API from the CLI (dev only)
`dev` allows `ADMIN_USER_PASSWORD_AUTH`, which needs IAM admin credentials, so
you can get a token without the web app:
```powershell
$pool = terraform output -raw user_pool_id; $client = terraform output -raw web_client_id; $api = terraform output -raw api_url
$token = aws cognito-idp admin-initiate-auth --user-pool-id $pool --client-id $client `
  --auth-flow ADMIN_USER_PASSWORD_AUTH --auth-parameters USERNAME=you@example.com,PASSWORD='...' `
  --query AuthenticationResult.AccessToken --output text
curl.exe -H "Authorization: Bearer $token" "${api}receipts?status=needs_attention"
```

## Phone web app (Phase 3b)

React + Vite in `web/`, hosted on a private S3 bucket behind CloudFront
(`infra/modules/web_hosting`). The main-branch workflow applies Terraform,
then builds the app and syncs it to the bucket. Terraform writes
`config.json` (API URL, Cognito ids), so the same build works in any environment.

Open the URL from `terraform output -raw web_url` on your phone, sign in, and
use **Share → Add to Home Screen** to install it.

- **Add receipt:** take a photo; the app shrinks it to a JPEG and warns if it
  looks blurry, dark, or small (you can still upload), then shows the receipt
  as it's read.
- **Needs your input:** receipts the validation step flagged; fix the fields
  and save (the same rules as the pipeline decide whether it's complete).
- **By month:** everything with a purchase date in that month.

Security: sign-in uses the authorization code flow with PKCE (no client
secret); CloudFront adds a strict Content-Security-Policy (scripts only from
the app's own origin), HSTS, and frame blocking.

### Run it locally
```powershell
cd infra/envs/dev
$cfg = @{ apiUrl = (terraform output -raw api_url); issuer = (terraform output -raw issuer);
          clientId = (terraform output -raw web_client_id); loginDomain = (terraform output -raw login_domain) }
$cfg | ConvertTo-Json | Set-Content ..\..\..\web\public\config.json   # gitignored
cd ..\..\..\web
npm install
npm run dev        # http://localhost:5173 (registered with Cognito and CORS)
npm test
```

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
