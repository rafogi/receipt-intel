# ADR-0005: Rule-based validation gate (not an LLM)

## Status
Accepted

## Context
Success criterion for v1: unreadable or failed receipts are never dropped or
saved with a guessed total. Something must decide, for every receipt, whether
the extracted data can be saved as `processed` or needs a human.

Phase 1 showed the kinds of mistakes models make on real receipts: a
two-digit year read as 2016 instead of 2026, a missing subtotal returned as
`0` rather than omitted, and an address line taken as the store name.

## Decision
A small Python function (the Validate Lambda) applies fixed rules. Any
failure routes the receipt to the review queue with the reasons attached:

| Rule | Reason code |
|---|---|
| Textract couldn't read the document | `unreadable:<code>` |
| No total, or total ≤ 0 | `total_missing` |
| Textract's confidence in the printed total < 80% | `total_low_confidence` |
| Extracted total differs from Textract's total by > $0.01 | `total_mismatch` |
| Subtotal + GST + PST ≠ total (± $0.02), when a subtotal is printed | `math_mismatch` |
| No store | `store_missing` |
| No date | `date_missing` |
| Date in the future, or more than 2 years old | `date_implausible` |
| Category not in the allowed list | `category_missing` |
| Same date and total (and a similar store name) as a receipt already saved | `possible_duplicate` |

Receipts with no printed subtotal skip the math check instead of failing it
(the fallback noted in Phase 1).

The duplicate rule was added after the first real use: the same receipt was
photographed twice, 38 seconds apart. Each photo is a different file, so the
idempotency check (ADR-0006) correctly processed both; only a content check
can tell they are the same purchase. Store names are compared loosely
(similarity ≥ 0.8) because OCR misreads them ("T&1" for "T&T"). Saving the
receipt in the app confirms it is not a duplicate; deleting it removes it.
This rule needs a read of the receipts table (the `byDate` index), so the
Validate Lambda has `dynamodb:Query` on that index and nothing else.

Manual edits (`PATCH /receipts/{id}`) run the same field rules
(`field_checks`), minus the Textract cross-checks and the duplicate check.

## Alternatives considered
- **Ask an LLM whether its own reading was a guess:** weak (a model that
  guessed tends to be confident), slow, and adds a model call to every
  receipt.
- **Trust the extraction model with no gate:** violates the success criterion.

## Consequences
- Validation is deterministic, unit-tested (`services/pipeline/tests`), and
  free to run.
- Thresholds are explicit and can be tuned against the evaluation set.
- Some correct receipts will be flagged (for example, low-confidence totals
  that happen to be right). That cost is accepted: a false flag costs the
  user a tap; a wrong saved total corrupts their budget.
- The cross-check against Textract's total only has teeth once a separate
  LLM extraction exists; with the Phase 2 placeholder both come from Textract.
