# ADR-0001: Deploy to us-west-2 (Oregon)

## Status
Accepted

## Context
The pipeline needs Textract (AnalyzeExpense), Bedrock Nova models,
Step Functions, and SES inbound email. The primary user is in
Richmond, BC. Candidate regions: us-west-2, ca-central-1, ca-west-1.

## Decision
Use us-west-2 for all project resources.

## Rationale
- All required services, including Nova models, are available in-region.
- Physically closer to the user than ca-central-1.
- Processing is asynchronous, so latency differences are negligible.
- Cost differences between candidate regions are negligible at this scale.
- ca-west-1 lacks several required services.

## Consequences
- Receipt data is stored and processed in the United States.
- For a customer with Canadian data residency requirements, the design
  would move to ca-central-1, use in-region or Canada-only inference
  profiles, and replace services not available there.
