# ADR-0004: Direct service integrations vs. Lambda wrappers

## Status
Accepted

## Context
The ingestion workflow (Step Functions) calls Textract, an LLM, DynamoDB, SQS,
and EventBridge for every receipt. Each call can be a direct (optimized)
service integration from Step Functions, or a Lambda function that makes the
call. Lambda wrappers add code to maintain, cold starts, and a second place
for retries and permissions to live.

Measured during Phase 1: a Textract `AnalyzeExpense` response for one short
grocery receipt was 286 KB, mostly the raw `Blocks` array. Step Functions
limits state input and output to 256 KB, so a direct Textract integration
would fail with `States.DataLimitExceeded` on ordinary receipts.

## Decision
Use direct integrations by default, and a Lambda only where it earns its place:

| Step | Implementation | Why |
|---|---|---|
| Claim, Save, Record failure | DynamoDB direct integration | Simple writes; conditions handle idempotency |
| Review queue, DLQ | SQS direct integration | |
| Publish `ReceiptProcessed` | EventBridge direct integration | |
| Analyze | **Lambda** | Calls Textract, saves the raw response to S3, returns only compact fields (payload limit) |
| Validate | **Lambda** | Rules are easier to unit-test in Python than in JSONata (ADR-0005) |
| Extract (LLM) | Bedrock direct integration (planned) | The prompt input is the compact OCR text, well under the limit |

Workflow logic (key parsing, routing, building DynamoDB requests) uses
JSONata, so no Lambda is needed just to reshape data.

## Alternatives considered
- **Direct Textract integration with output filtering:** relies on the full
  service response being accepted before filtering trims it; with responses
  already above the limit, not worth betting every long receipt on.
- **Textract async API with output to S3:** avoids the limit but adds a
  start/poll (or SNS callback) pattern for a call that takes about 2 seconds.
  Worth revisiting for multi-page PDFs in Phase 4.
- **One Lambda for the whole pipeline:** simplest to write, but loses
  per-step retries, visual execution history, and the failure routing that
  Step Functions provides.

## Consequences
- Two small Lambdas instead of none; both share one package
  (`services/pipeline/src`) with the evaluation tooling.
- The full Textract response is kept in S3 (`textract/`), useful for
  reprocessing and audits without paying for Textract again.
- Step Functions payloads stay small and contain no raw OCR geometry.
