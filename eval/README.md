# Phase 1: extraction evaluation

Compares two ways of reading receipts, on real receipts, before the pipeline is built:

| Approach | How it works |
|---|---|
| **A** `textract_nova_micro` | Textract `AnalyzeExpense` reads the receipt; Nova Micro normalizes fields and picks a category |
| **B** `nova_lite_vision` | Nova Lite reads the photo directly, no OCR step |

Both use the same prompt and output schema (`services/pipeline/src/receipt_schema.py`),
shared with the Phase 2 pipeline, as is the Textract parsing. The report measures per-field accuracy, latency, cost per receipt,
and how well the "subtotal + taxes = total" check catches wrong totals.

Everything under `eval/data/` is gitignored, because receipts contain personal information.

## Setup (Windows PowerShell)

```powershell
cd C:\Users\User\Documents\projects\awsbedrock\eval
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:AWS_PROFILE = "receipt-intel-admin"
aws sso login
```

## 1. Add receipt photos

Put 20–30 photos in `eval\data\images\`. Any filename works; the filename (without
extension) becomes the receipt id, so names like `r001.jpg`, `r002.jpg` keep things tidy.
JPG, PNG, and iPhone HEIC are all fine.

Aim for variety: groceries, restaurants, gas, pharmacy, a long receipt, and a few
faded or crumpled ones. Lay them flat in even light.

## 2. Record the correct answers

```powershell
.venv\Scripts\python init_labels.py
```

This creates `data\labels.csv` with one row per photo (re-run it after adding photos;
your answers are kept). Fill it in from the **paper receipt**, not from any model output:

| Column | What to enter |
|---|---|
| `store` | Merchant name as printed, e.g. `Save-On-Foods` |
| `date` | `YYYY-MM-DD`. Blank if not printed |
| `subtotal`, `gst`, `pst` | As printed. Blank if not printed |
| `total` | Final amount paid. **Required** (rows without it are skipped) |
| `category` | One of: groceries, dining, gas, household, pharmacy, kids, cycling, golf, utilities, other |
| `difficulty` | Optional: `easy`, `faded`, `crumpled`, `long`, … |
| `notes` | Optional |

If you use Excel: format the `date` column as **Text** before typing, and save as
**CSV UTF-8**. (Dates Excel rewrites as `9/21/2026` are still accepted.)

## 3. Run

Smoke test on two receipts first:

```powershell
.venv\Scripts\python run_eval.py --limit 2
```

Then the full set:

```powershell
.venv\Scripts\python run_eval.py
```

The summary prints to the terminal; the full report (including every miss) is saved
to `data\results\<timestamp>\report.md`, with raw outputs in `predictions.jsonl`.

Useful options: `--approach nova_lite_vision` (repeatable), `--ids r003 r007`,
`--refresh-textract` (ignore the cache), `--micro-model` / `--lite-model` (try other models).

### Running on OpenAI instead of Bedrock

Same prompts, schema, and scoring; only the model call changes. Textract still runs
on AWS (cached after the first run).

1. Copy `.env.example` to `.env` in this folder and set `OPENAI_API_KEY=sk-...`.
   `.env` is gitignored.
2. Run:

```powershell
.venv\Scripts\python run_eval.py --provider openai
```

The default model is `gpt-5-nano` for both approaches. Try others with
`--micro-model gpt-5-mini --lite-model gpt-5-mini`. `--reasoning-effort` defaults to
`minimal` to keep hidden reasoning tokens (billed as output) low.

Receipt text and photos are sent to OpenAI, outside AWS. The approach names still
say "nova"; the report header shows the provider and models actually used.

## Cost

Textract runs once per receipt and is cached in `data\cache\textract\`:
about **$0.30 for 30 receipts**. The Nova calls cost well under a cent per full run,
so re-running while tuning the prompt is effectively free.

## Troubleshooting

- **`AccessDeniedException` from Bedrock:** open the Bedrock console in us-west-2 and
  confirm Nova Micro and Nova Lite work in the playground.
- **`ValidationException` about on-demand throughput:** use an inference profile id
  (the defaults `us.amazon.nova-…` already are).
- **`ExpiredToken`:** run `aws sso login` again.

## After the run

Record the decision in `docs/adr/0003-extraction-approach.md`: the results table,
which approach Phase 2 uses, and why (accuracy vs. cost vs. latency).
