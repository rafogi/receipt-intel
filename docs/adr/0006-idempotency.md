# ADR-0006: Idempotency strategy

## Status
Accepted

## Context
EventBridge delivers S3 events at least once, so the workflow can start
twice for the same upload. Processing twice costs a second Textract call and
risks double-counting a receipt. Users may also re-upload a corrected photo
under the same receipt id, or enter a receipt by hand while it is processing.

## Decision
The receipt item in DynamoDB is its own idempotency record. The first workflow
step ("Claim") is a conditional `PutItem` keyed by `userId` + `receiptId`
that records the object's ETag:

```
attribute_not_exists(receiptId)
OR (etag <> :etag AND (attribute_not_exists(source) OR source <> "manual"))
```

| Situation | Result |
|---|---|
| First event for an upload | Item created, processing continues |
| Duplicate event (same ETag) | Condition fails, execution ends as `AlreadyProcessed` |
| New photo under the same key (new ETag) | Item replaced, receipt reprocessed |
| User already entered the receipt by hand | Condition fails, manual entry kept |

The later writes (Save, Record failure) carry the condition
`source <> "manual"`, so a manual entry made mid-processing is never
overwritten by the automatic result.

## Alternatives considered
- **Step Functions execution name as the idempotency key:** the EventBridge
  target can't set the execution name, so it would need an extra Lambda.
- **A separate idempotency table (e.g. Powertools pattern):** another table
  and TTL to manage, for no benefit over a condition on the item itself.
- **No idempotency (accept duplicates):** duplicate Textract charges and
  duplicate events for budget alerts.

## Consequences
- No extra table or Lambda; the guarantee lives in one DynamoDB condition.
- Re-processing a receipt on purpose (for example, the hourly DLQ redrive in
  Phase 6) needs to bypass the same-ETag check; to be designed with the redrive.
