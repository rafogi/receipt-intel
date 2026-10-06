# receipt-intel: project plan

Event-driven document intelligence on AWS. Receipt photos, PDFs, and forwarded
email receipts become categorized spending data, with a phone web app, budget
tracking, and AI agents for exception handling and spending questions.

**Goal:** a portfolio project for cloud AI architect roles. The design
decisions, evaluation results, and cost analysis matter as much as the code.

**Last updated:** 2026-10-06

---

## Status at a glance

| Phase | Focus | Status |
|---|---|---|
| 0 | Foundations: account, Identity Center, Terraform state, CI/CD | ✅ Done |
| 1 | Test set and extraction evaluation | 🟡 In progress (Bedrock blocked by account verification) |
| 2 | Core pipeline | 🟡 In progress (extraction step waits on Phase 1) |
| 3 | Upload API, Cognito, phone web app | ⬜ |
| 4 | Online receipts: email and PDF | ⬜ |
| 5 | Event consumers, dashboard, budgets | ⬜ |
| 6 | Observability, failure handling, cost | ⬜ |
| 7 | Portfolio packaging (v1 write-up) | ⬜ |
| 8 | Strands agents: review agent and spending Q&A agent | ⬜ |
| 9 | Raspberry Pi edge gateway with local LLM | ⬜ |

**Success criteria (v1):**
- A receipt uploaded from the phone appears in the app within ~30 seconds.
- Key fields (store, date, total, category) correct on ≥ 90% of the test set.
- Unreadable or failed receipts are never dropped or saved with a guessed total.
- Everything deploys from Terraform through CI. Monthly cost under $5.

---

## Architecture

### Ingestion (every receipt)
1. **Producers:** phone web app (via API Gateway + Cognito presigned URLs),
   forwarded email (SES inbound), Raspberry Pi (device role). All write to one
   **S3** bucket.
2. **EventBridge** rule on S3 uploads starts a **Step Functions** workflow:
   - **Claim** (DynamoDB conditional write on object key + ETag; duplicates stop here)
   - **File type** choice: photos → Textract; text PDFs and emails → text parser
   - **Analyze** (small Lambda): Textract AnalyzeExpense, raw response saved to
     S3, compact fields returned. A Lambda, not a direct integration, because
     responses can exceed the 256 KB Step Functions payload limit (ADR-0004)
   - **Bedrock (Nova)** extraction and categorization with a JSON schema
     (direct service integration; a Textract-only placeholder until ADR-0003)
   - **Validate** (small Lambda, rule-based, not an LLM)
   - **Route:** valid → DynamoDB; failed validation → SQS review queue
   - Publish `ReceiptProcessed` event
3. **Consumers:** SNS notifications, budget alerts, weekly summary.

### Read path (app data)
Web app → API Gateway (Cognito) → read Lambda → DynamoDB. The app never
touches DynamoDB directly. User ID always comes from the login token.

| Endpoint | Powers |
|---|---|
| `GET /receipts?month=` | Receipts list |
| `GET /receipts/{id}` | Detail view + presigned URL for the photo |
| `GET /receipts?status=needs_review` | Review / "Needs your input" screen |
| `GET /summary?month=` | Dashboard by category |
| `PATCH /receipts/{id}` | Manual edits and entries |
| `GET/PUT /budgets` | Budget settings |

### Ask path (spending and budget chat)
Web app "Ask" tab → API Gateway → **Q&A agent** (Strands on AgentCore,
streaming) → Bedrock + tools against DynamoDB.

### Agents run on compute, think with Bedrock
- **Lambda / AgentCore** runs the agent code, the loop, and the tools.
- **Bedrock** is the model the code calls. It never runs inside Lambda.
- Fixed workflows (extraction) use Step Functions; open-ended reasoning
  (agents) uses Strands.

### Delivery (built in Phase 0)
GitHub Actions → OIDC IAM roles (plan role on PRs, apply role on `main` via the
`dev` environment) → Terraform, with state in S3 (versioned, encrypted,
native locking). Trust policies pinned to immutable GitHub owner/repo IDs.

---

## Failure handling

Two kinds of failure, two paths. Both end with the user able to enter the
receipt manually, so nothing is lost or guessed.

| | Receipt is bad (blurry, total unreadable) | System broke (timeout, throttling, bug) |
|---|---|---|
| Caught by | Validate step | Step Functions retries fail |
| Goes to | SQS review queue (reason attached) | Dead-letter queue (engineer alerted) |
| First response | Review agent re-reads (Phase 8) | Auto-redrive hourly for up to 24h |
| If still unresolved | Status `needs_review` → user asked | Status `needs_manual_entry` → user asked (batched notification) |

**Defenses against guessed totals:**
1. Pre-upload check on phone/Pi: blurry, dark, too small → retake prompt.
2. `unreadable_reason` in the schema; `total` optional. The model can say
   "can't read" instead of inventing a number.
3. Textract confidence threshold on the total (~80%).
4. Textract total vs. LLM total must agree.
5. Math check: subtotal + GST + PST = total, where printed. Needs a fallback
   for receipts with no subtotal line (seen in Phase 1).

Manual entries are saved with `source: manual`, are never overwritten by a
later automatic retry, and become extra ground-truth labels for evaluation.

The validation gate stays rule-based on purpose: an LLM judging whether its
own reading was a guess is weak, slow, and costly on every receipt.

---

## Phases

### Phase 0: Foundations ✅
- [x] AWS account, root MFA, budget alerts ($5/$10), Cost Anomaly Detection
- [x] IAM Identity Center in us-west-2, admin user with MFA, SSO CLI profile
- [x] Bootstrap stack: Terraform state bucket + GitHub OIDC plan/apply roles
- [x] Bootstrap state migrated into S3
- [x] GitHub repo, variables, `dev` environment limited to `main`
- [x] Hello Lambda deployed by GitHub Actions
- [x] ADR-0001 (region), ADR-0002 (state and CI auth)

**Lessons:** enabling AWS Organizations moves an account off the Free Plan;
GitHub's OIDC subject can include immutable IDs (found via CloudTrail);
PowerShell needs `"-backend-config=backend.hcl"` quoted.

### Phase 1: Test set and evaluation 🟡
Compare extraction approaches on real receipts before building the pipeline.

| Approach | How |
|---|---|
| `textract_only` | Textract fields, no LLM (baseline) |
| `textract_nova_micro` | Textract → Nova Micro normalizes and categorizes |
| `nova_lite_vision` | Nova Lite reads the photo directly |
| *(optional)* Claude | Via Bedrock once unblocked, or the Anthropic API as a comparison |

- [x] Evaluation tooling (`eval/`): labels CSV, Textract cache, report
- [x] First Textract-only results (2 receipts: store, date, total correct; no subtotal/GST found)
- [ ] Bedrock unblocked (support case open; account verification hold)
- [ ] 20–30 labeled receipts, including 2–3 deliberately unreadable ones
- [ ] Add `unreadable_reason`, Textract-vs-LLM cross-check, "guessed vs. admitted" metric
- [ ] Full run; ADR-0003 (extraction approach) with accuracy, latency, cost

**Done when:** results table and ADR-0003 choose the approach for Phase 2.

### Phase 2: Core pipeline 🟡
- [x] S3 bucket, EventBridge rule, Step Functions workflow (Terraform, `infra/modules/receipt_pipeline`)
- [x] File-type choice; Analyze Lambda (Textract, raw response to S3); DynamoDB, SQS, EventBridge as direct integrations
- [x] Analyze and Validate Lambdas; shared package `services/pipeline/src` (also used by `eval/`)
- [x] DynamoDB table: `userId` / `receiptId`, local indexes `byDate` (`date#receiptId`) and `byStatus`
- [x] SQS review queue; DLQs; retries with backoff; idempotency (object key + ETag)
- [x] Catch blocks set `needs_manual_entry` status after retries fail
- [x] Unit tests in CI; ADR-0004, ADR-0005, ADR-0006
- [ ] Deployed and smoke-tested with a real upload
- [ ] Extract step: replace the Textract-only placeholder once ADR-0003 is decided

**Done when:** a receipt in S3 produces a DynamoDB row; a bad one lands in the
review queue; a forced failure lands in the DLQ. Can be built while Bedrock is
blocked (Bedrock failures exercise the DLQ path).

### Phase 3: Upload API and phone web app
- Cognito, API Gateway, presigned upload URLs
- React (Vite) PWA on S3 + CloudFront: camera capture, pre-upload blur check
- Receipts list, detail view, "Needs your input" screen, manual entry form
- Read endpoints and `PATCH /receipts/{id}`; status polling
- Least-privilege IAM, S3 public access blocked, encryption

**Done when:** sign in on the phone, upload, and see the receipt appear; fix
a flagged one by hand.

### Phase 4: Online receipts
- SES inbound email (domain needed, sender allowlist) and PDF upload
- PDF text extraction (skip OCR for digital PDFs); scanned PDFs → Textract async
- Duplicate handling by order number; currency, shipping, refund fields
- Prompt-injection safeguards: receipt and email text treated as data only

**Done when:** a forwarded order email appears once, with correct total and currency.

### Phase 5: Consumers, dashboard, budgets
- SNS notifications; weekly Bedrock summary (EventBridge Scheduler)
- Dashboard: spending by category and month
- Budgets table; budget progress in the dashboard; budget alerts (e.g. 85% used)
- Optional: DynamoDB Streams to maintain running totals

**Done when:** dashboard shows real data and a budget alert fires.

### Phase 6: Observability, failure handling, cost
- CloudWatch dashboard (processing time, failures, queue depths), X-Ray
- Alarms on DLQ not empty and error rates
- Scheduled DLQ redrive (hourly, up to 24h) and batched user notification
- Log retention 14 days, cost allocation tags, real cost per 1,000 receipts
- Narrow the CI apply role (from AdministratorAccess)

**Done when:** you can show what it costs and how you'd know it broke.

### Phase 7: Portfolio packaging (v1)
- README: architecture diagrams (ingestion, app read/ask, agents close-up)
  with official AWS icons; ADRs; evaluation report; cost analysis
- Azure / GCP mapping section
- Demo video (3–5 min) and LinkedIn post

**Done when:** someone new understands the design and trade-offs in five minutes.

### Phase 8: Strands agents
**Review agent** (Lambda, triggered by the SQS review queue)
- Tools: re-read photo (S3, stronger vision model), past receipts from the same store (DynamoDB)
- Fixes only when confident; otherwise flags for the user
- Guardrails: limited fields, before/after values logged, reversible

**Spending Q&A agent** (AgentCore, streaming, "Ask" tab)
- Tools: `get_spending`, `list_receipts`, `compare_periods`, `get_budgets`,
  `set_budget` (only write tool, confirms first)
- User ID set by the backend from the token, never by the agent
- Math done in tools, not by the model

- Evaluate agent accuracy; OpenTelemetry tracing; ADR on workflows vs. agents

**Done when:** a failed receipt is fixed or clearly explained, and a spending
or budget question gets a correct answer.

### Phase 9: Raspberry Pi edge gateway
- Capture (camera or watched folder), blur check, local OCR
- Local LLM (Ollama, ~3B model) verifies "is this a readable receipt?"
- Redacts card numbers before upload; local queue for offline retries
- Device auth (IAM Roles Anywhere vs. IoT Core); Pi metrics to CloudWatch
- Cloud cross-checks the Pi's local total against Textract's

**Done when:** a Pi-scanned receipt is checked, redacted, uploaded, and cross-validated.

---

## Architecture decision records

| ADR | Topic | Status |
|---|---|---|
| 0001 | Region: us-west-2 | Accepted |
| 0002 | Terraform state and CI/CD auth (OIDC, ID-pinned trust) | Accepted |
| 0003 | Extraction approach (Textract+LLM vs. vision vs. Textract only) | Phase 1 |
| 0004 | Direct service integrations vs. Lambda wrappers | Accepted |
| 0005 | Rule-based validation gate (not an LLM) | Accepted |
| 0006 | Idempotency strategy | Accepted |
| 0007 | SQS buffer vs. inline agent call | Phase 8 |
| 0008 | Workflows for ingestion, agents for exceptions and questions | Phase 8 |
| 0009 | Pi authentication and edge redaction | Phase 9 |
| 0010 | Where Terraform runs (GitHub runners vs. CodeBuild) for prod | Later |

---

## Cost

| | Estimate (USD) |
|---|---|
| Development (~13 weeks) | ~$12–30, covered by sign-up credits |
| Running (30 receipts/month) | ~$1–3/month |
| Domain for email receipts (optional) | ~$15/year |

Main drivers: Textract (~1¢/page, cached during evaluation), Bedrock (fractions
of a cent per receipt). Avoid: NAT Gateways, OpenSearch Serverless, provisioned
throughput, customer-managed KMS keys.

---

## Open items and risks

- **Bedrock blocked** ("Too many tokens per day" / account verification).
  Support case open. Not blocking Phase 2 infrastructure work.
- Math check needs a fallback for receipts without a subtotal line.
- Collect 20–30 receipts plus 5–10 online PDFs (for Phase 4).
- Later: `prod` environment with tag-based promotion and required reviewers.
