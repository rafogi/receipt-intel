"""Phase 1 evaluation: compare receipt-extraction approaches against ground truth.

  A  textract_nova_micro : Textract AnalyzeExpense -> Nova Micro (text) normalizes + categorizes
  B  nova_lite_vision    : Nova Lite reads the receipt image directly

Textract responses are cached in data/cache/textract/, so re-runs only pay
for the (much cheaper) LLM calls. Results go to data/results/<timestamp>/.

--provider openai sends the same prompts and tool schema to the OpenAI API
instead of Bedrock (key from the OPENAI_API_KEY environment variable). Useful
as a comparison, or while Bedrock is unavailable.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from common import (
    CACHE,
    IMAGES,
    LABELS_CSV,
    RESULTS,
    normalize_store,
    parse_date,
    parse_money,
    prepare_image,
)
from receipt_schema import (
    CATEGORIES,
    MONEY_FIELDS,
    SYSTEM_PROMPT,
    TOOL_SPEC,
    textract_user_prompt,
    vision_user_prompt,
)

REGION = os.environ.get("AWS_REGION", "us-west-2")

# Cross-region inference profiles ("us." prefix) work in us-west-2 without
# per-region model availability surprises. Override with --micro-model / --lite-model.
DEFAULT_MICRO = "us.amazon.nova-micro-v1:0"
DEFAULT_LITE = "us.amazon.nova-lite-v1:0"
# One OpenAI model handles both approaches (it reads text and images).
DEFAULT_OPENAI = "gpt-5-nano"

# USD list prices; confirm against the AWS / OpenAI pricing pages before quoting results.
PRICES = {
    "textract_expense_per_page": 0.01,
    "us.amazon.nova-micro-v1:0": {"in": 0.035, "out": 0.14},  # per 1M tokens
    "us.amazon.nova-lite-v1:0": {"in": 0.06, "out": 0.24},
    "amazon.nova-micro-v1:0": {"in": 0.035, "out": 0.14},  # in-region ids, separate quotas
    "amazon.nova-lite-v1:0": {"in": 0.06, "out": 0.24},
    "gpt-5-nano": {"in": 0.05, "out": 0.40},  # output includes reasoning tokens
    "gpt-5-mini": {"in": 0.25, "out": 2.00},
    "gpt-5.4-nano": {"in": 0.20, "out": 1.25},
    "gpt-5.4-mini": {"in": 0.75, "out": 4.50},
}

SCORED_FIELDS = ["store", "date", "subtotal", "gst", "pst", "total", "category"]
KEY_FIELDS = ["store", "date", "total", "category"]
HEADINGS = {"store": "Store", "date": "Date", "subtotal": "Subtotal", "gst": "GST", "pst": "PST", "total": "Total", "category": "Category"}
MONEY_TOLERANCE = 0.01
MATH_TOLERANCE = 0.02


# ---------------------------------------------------------------- AWS calls


def make_clients():
    cfg = Config(
        region_name=REGION,
        retries={"max_attempts": 6, "mode": "adaptive"},
        read_timeout=120,
    )
    return boto3.client("textract", config=cfg), boto3.client("bedrock-runtime", config=cfg)


def textract_expense(textract, receipt_id: str, image: bytes, refresh: bool) -> dict:
    path = CACHE / "textract" / f"{receipt_id}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    start = time.perf_counter()
    resp = textract.analyze_expense(Document={"Bytes": image})
    latency = time.perf_counter() - start
    resp.pop("ResponseMetadata", None)
    record = {
        "latency_s": latency,
        "pages": resp.get("DocumentMetadata", {}).get("Pages", 1),
        "response": resp,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def textract_to_text(resp: dict, max_items: int = 60) -> str:
    """Flatten AnalyzeExpense output into compact lines an LLM can read."""
    lines: list[str] = []
    for doc in resp.get("ExpenseDocuments", []):
        for f in doc.get("SummaryFields", []):
            ftype = f.get("Type", {}).get("Text", "OTHER")
            label = (f.get("LabelDetection") or {}).get("Text", "")
            value = (f.get("ValueDetection") or {}).get("Text", "")
            if value:
                lines.append(f"{ftype}" + (f" [{label}]" if label else "") + f": {value}")
        items = 0
        for group in doc.get("LineItemGroups", []):
            for item in group.get("LineItems", []):
                fields = {
                    x.get("Type", {}).get("Text"): (x.get("ValueDetection") or {}).get("Text", "")
                    for x in item.get("LineItemExpenseFields", [])
                }
                row = fields.get("EXPENSE_ROW") or " ".join(
                    v for k, v in fields.items() if k in ("ITEM", "QUANTITY", "PRICE") and v
                )
                if row and items < max_items:
                    lines.append(f"LINE_ITEM: {row}")
                    items += 1
    return "\n".join(lines)


def call_llm(bedrock, model_id: str, content: list[dict]) -> dict:
    """Converse with a forced tool call; returns parsed fields + usage + latency."""
    request = {
        "modelId": model_id,
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": content}],
        "toolConfig": {"tools": [TOOL_SPEC], "toolChoice": {"tool": {"name": "record_receipt"}}},
        "inferenceConfig": {"maxTokens": 800, "temperature": 0},
    }
    start = time.perf_counter()
    try:
        resp = bedrock.converse(**request)
    except ClientError as err:
        # Some models only accept "any"/"auto" tool choice; fall back once.
        if err.response.get("Error", {}).get("Code") != "ValidationException":
            raise
        request["toolConfig"]["toolChoice"] = {"any": {}}
        resp = bedrock.converse(**request)
    latency = time.perf_counter() - start

    data = None
    text_parts = []
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            data = block["toolUse"]["input"]
            break
        text_parts.append(block.get("text", ""))
    if data is None:
        data = extract_json("".join(text_parts))
    usage = resp.get("usage", {})
    return {
        "fields": data or {},
        "latency_s": latency,
        "input_tokens": usage.get("inputTokens", 0),
        "output_tokens": usage.get("outputTokens", 0),
    }


def call_openai(client, model_id: str, content: list[dict], reasoning_effort: str | None) -> dict:
    """Same request as call_llm, sent to OpenAI Chat Completions with a forced function call."""
    parts = []
    for block in content:  # Bedrock Converse blocks -> OpenAI content parts
        if "text" in block:
            parts.append({"type": "text", "text": block["text"]})
        elif "image" in block:
            b64 = base64.b64encode(block["image"]["source"]["bytes"]).decode("ascii")
            url = f"data:image/{block['image']['format']};base64,{b64}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
    spec = TOOL_SPEC["toolSpec"]
    request = {
        "model": model_id,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": parts}],
        "tools": [{
            "type": "function",
            "function": {"name": spec["name"], "description": spec["description"], "parameters": spec["inputSchema"]["json"]},
        }],
        "tool_choice": {"type": "function", "function": {"name": spec["name"]}},
        # GPT-5 models count hidden reasoning against this limit, so leave room.
        "max_completion_tokens": 4000,
    }
    if reasoning_effort:
        request["reasoning_effort"] = reasoning_effort
    start = time.perf_counter()
    resp = client.chat.completions.create(**request)
    latency = time.perf_counter() - start

    message = resp.choices[0].message
    if message.tool_calls:
        data = json.loads(message.tool_calls[0].function.arguments)
    else:
        data = extract_json(message.content or "")
    return {
        "fields": data or {},
        "latency_s": latency,
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
    }


def extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def llm_cost(model_id: str, tokens_in: int, tokens_out: int) -> float:
    price = PRICES.get(model_id)
    if not price:
        return float("nan")
    return tokens_in / 1e6 * price["in"] + tokens_out / 1e6 * price["out"]


# ---------------------------------------------------------------- approaches


def run_textract_nova_micro(ctx, receipt_id: str, image: bytes) -> dict:
    record = textract_expense(ctx["textract"], receipt_id, image, ctx["refresh_textract"])
    text = textract_to_text(record["response"])
    llm = ctx["llm"](ctx["micro_model"], [{"text": textract_user_prompt(text)}])
    return {
        "fields": llm["fields"],
        "latency_s": record["latency_s"] + llm["latency_s"],
        "cost_usd": record["pages"] * PRICES["textract_expense_per_page"]
        + llm_cost(ctx["micro_model"], llm["input_tokens"], llm["output_tokens"]),
        "tokens": [llm["input_tokens"], llm["output_tokens"]],
    }


def run_nova_lite_vision(ctx, receipt_id: str, image: bytes) -> dict:
    content = [
        {"image": {"format": "jpeg", "source": {"bytes": image}}},
        {"text": vision_user_prompt()},
    ]
    llm = ctx["llm"](ctx["lite_model"], content)
    return {
        "fields": llm["fields"],
        "latency_s": llm["latency_s"],
        "cost_usd": llm_cost(ctx["lite_model"], llm["input_tokens"], llm["output_tokens"]),
        "tokens": [llm["input_tokens"], llm["output_tokens"]],
    }


def best_field(fields: list[dict], ftype: str) -> tuple[str | None, str]:
    """Highest-confidence value Textract assigned to this field type, plus its printed label."""
    candidates = [
        f for f in fields
        if f.get("Type", {}).get("Text") == ftype and (f.get("ValueDetection") or {}).get("Text")
    ]
    if not candidates:
        return None, ""
    best = max(candidates, key=lambda f: (f.get("ValueDetection") or {}).get("Confidence", 0))
    return best["ValueDetection"]["Text"], (best.get("LabelDetection") or {}).get("Text", "")


def money_from_text(text: str | None) -> float | None:
    match = re.search(r"-?\d[\d,]*\.\d{2}", text or "")
    return round(float(match.group(0).replace(",", "")), 2) if match else None


def run_textract_only(ctx, receipt_id: str, image: bytes) -> dict:
    """Baseline: Textract's own fields, no LLM. It cannot choose a category."""
    record = textract_expense(ctx["textract"], receipt_id, image, ctx["refresh_textract"])
    fields = [f for d in record["response"].get("ExpenseDocuments", []) for f in d.get("SummaryFields", [])]
    store, _ = best_field(fields, "VENDOR_NAME")
    date, _ = best_field(fields, "INVOICE_RECEIPT_DATE")
    out = {
        "store": store.split("\n")[0] if store else None,
        "date": date,
        "subtotal": money_from_text(best_field(fields, "SUBTOTAL")[0]),
        "total": money_from_text(best_field(fields, "TOTAL")[0]),
    }
    for f in fields:  # TAX lines: labelled PST go to pst, anything else to gst
        if f.get("Type", {}).get("Text") != "TAX":
            continue
        label = ((f.get("LabelDetection") or {}).get("Text") or "").upper()
        key = "pst" if "PST" in label else "gst"
        if out.get(key) is None:
            out[key] = money_from_text((f.get("ValueDetection") or {}).get("Text"))
    return {
        "fields": out,
        "latency_s": record["latency_s"],
        "cost_usd": record["pages"] * PRICES["textract_expense_per_page"],
        "tokens": [0, 0],
    }


APPROACHES = {
    "textract_only": run_textract_only,
    "textract_nova_micro": run_textract_nova_micro,
    "nova_lite_vision": run_nova_lite_vision,
}


# ---------------------------------------------------------------- labels + scoring


def load_labels() -> list[dict]:
    if not LABELS_CSV.exists():
        sys.exit(f"Missing {LABELS_CSV}. Run init_labels.py first.")
    raw = LABELS_CSV.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")  # Excel's default "CSV (Comma delimited)" on Windows
    labels, skipped = [], []
    for row in csv.DictReader(text.splitlines()):
        if not (row.get("total") or "").strip():
            skipped.append(row.get("id"))
            continue
        category = (row.get("category") or "").strip().lower() or None
        if category and category not in CATEGORIES:
            sys.exit(f"{row['id']}: category {category!r} not in {CATEGORIES}")
        labels.append(
            {
                "id": row["id"].strip(),
                "image": row["image"].strip(),
                "store": (row.get("store") or "").strip() or None,
                "date": parse_date(row.get("date")),
                **{f: parse_money(row.get(f)) for f in MONEY_FIELDS},
                "category": category,
                "difficulty": (row.get("difficulty") or "").strip(),
            }
        )
    if skipped:
        print(f"Skipping {len(skipped)} unlabeled receipt(s) (no total): {', '.join(skipped)}")
    return labels


def normalize_prediction(fields: dict) -> dict:
    out = {"store": fields.get("store"), "category": (fields.get("category") or "").lower() or None}
    try:
        out["date"] = parse_date(fields.get("date"))
    except ValueError:
        out["date"] = str(fields.get("date"))  # keep it so it's scored (as wrong)
    for f in MONEY_FIELDS:
        try:
            out[f] = parse_money(fields.get(f))
        except (TypeError, ValueError):
            out[f] = None
    return out


def field_correct(name: str, truth, pred) -> bool:
    if name == "store":
        t, p = normalize_store(truth), normalize_store(pred)
        if not p:
            return False
        return t == p or (len(t) >= 4 and (t in p or p in t)) or SequenceMatcher(None, t, p).ratio() >= 0.85
    if name in MONEY_FIELDS:
        return pred is not None and abs(truth - pred) <= MONEY_TOLERANCE
    return truth == pred


def math_check_passes(pred: dict) -> bool:
    """The Phase 2 validation rule: printed parts must add up to the printed total."""
    if pred.get("subtotal") is None or pred.get("total") is None:
        return False
    parts = pred["subtotal"] + (pred.get("gst") or 0) + (pred.get("pst") or 0)
    return abs(parts - pred["total"]) <= MATH_TOLERANCE


def score(label: dict, pred: dict) -> dict:
    """Per-field result; fields blank in the label are not scored."""
    return {f: field_correct(f, label[f], pred.get(f)) for f in SCORED_FIELDS if label[f] is not None}


# ---------------------------------------------------------------- reporting


def num(value: float, spec: str, prefix: str = "", suffix: str = "") -> str:
    return "—" if value != value else f"{prefix}{value:{spec}}{suffix}"  # value != value is True for nan


def pct(num: int, den: int) -> str:
    return f"{100 * num / den:.0f}% ({num}/{den})" if den else "n/a"


def summarize(approach: str, results: list[dict]) -> dict:
    ok = [r for r in results if "error" not in r]
    per_field = {}
    for f in SCORED_FIELDS:
        scored = [r["score"][f] for r in ok if f in r["score"]]
        per_field[f] = (sum(scored), len(scored))
    all_key = [all(r["score"].get(f, True) for f in KEY_FIELDS) for r in ok]
    total_wrong = [r for r in ok if r["score"].get("total") is False]
    total_right = [r for r in ok if r["score"].get("total") is True]
    latencies = sorted(r["latency_s"] for r in ok)
    costs = [r["cost_usd"] for r in ok]
    return {
        "approach": approach,
        "n": len(results),
        "errors": len(results) - len(ok),
        "per_field": per_field,
        "all_key": (sum(all_key), len(all_key)),
        "caught": (sum(not r["math_ok"] for r in total_wrong), len(total_wrong)),
        "false_flags": (sum(not r["math_ok"] for r in total_right), len(total_right)),
        "p50": statistics.median(latencies) if latencies else float("nan"),
        "p95": latencies[max(0, round(0.95 * len(latencies)) - 1)] if latencies else float("nan"),
        "cost": statistics.mean(costs) if costs else float("nan"),
    }


def write_report(out_dir, summaries: list[dict], all_results: dict, models: dict) -> str:
    lines = [
        f"# Receipt extraction evaluation — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Provider `{models['provider']}` · Textract region `{REGION}` · "
        f"text model (approach A) `{models['micro']}` · vision model (approach B) `{models['lite']}`",
        "",
        "## Accuracy",
        "",
        "| Approach | " + " | ".join(HEADINGS[f] for f in SCORED_FIELDS) + " | **All key fields** |",
        "|---|" + "---|" * (len(SCORED_FIELDS) + 1),
    ]
    for s in summaries:
        cells = [pct(*s["per_field"][f]) for f in SCORED_FIELDS]
        lines.append(f"| {s['approach']} | " + " | ".join(cells) + f" | **{pct(*s['all_key'])}** |")
    lines += [
        "",
        "Key fields = store, date, total, category (fields left blank in labels.csv are not scored).",
        "",
        "## Latency, cost, and validation",
        "",
        "| Approach | p50 latency | p95 latency | Cost / receipt | Cost / 1,000 | Wrong totals caught by math check | Correct receipts falsely flagged | Errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['approach']} | {num(s['p50'], '.1f', suffix='s')} | {num(s['p95'], '.1f', suffix='s')} "
            f"| {num(s['cost'], '.5f', prefix='$')} | {num(s['cost'] * 1000, '.2f', prefix='$')} "
            f"| {pct(*s['caught'])} | {pct(*s['false_flags'])} | {s['errors']} |"
        )
    lines += [
        "",
        "Approach A latency includes the original (uncached) Textract call time. "
        "Cost uses list prices in `run_eval.py`; check them against current AWS / OpenAI pricing.",
        "",
        "## Misses",
        "",
        "| Approach | Receipt | Field | Expected | Got |",
        "|---|---|---|---|---|",
    ]
    for approach, results in all_results.items():
        for r in results:
            if "error" in r:
                lines.append(f"| {approach} | {r['id']} | *error* | | {r['error'][:80]} |")
                continue
            for f, correct in r["score"].items():
                if not correct:
                    lines.append(f"| {approach} | {r['id']} | {f} | {r['truth'][f]} | {r['pred'].get(f)} |")
    report = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return report


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approach", choices=list(APPROACHES), action="append", help="run only these (repeatable)")
    parser.add_argument("--limit", type=int, help="only the first N labeled receipts")
    parser.add_argument("--ids", nargs="+", help="only these receipt ids")
    parser.add_argument("--refresh-textract", action="store_true", help="ignore the Textract cache (costs money)")
    parser.add_argument("--provider", choices=["bedrock", "openai"], default="bedrock")
    parser.add_argument("--micro-model", help=f"text model (default {DEFAULT_MICRO}, or {DEFAULT_OPENAI} with openai)")
    parser.add_argument("--lite-model", help=f"vision model (default {DEFAULT_LITE}, or {DEFAULT_OPENAI} with openai)")
    parser.add_argument(
        "--reasoning-effort", default="minimal",
        help="OpenAI only: reasoning effort for GPT-5 models ('none' to omit the parameter)",
    )
    args = parser.parse_args()
    if args.provider == "openai":
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).with_name(".env"))  # real environment variables win
        args.micro_model = args.micro_model or DEFAULT_OPENAI
        args.lite_model = args.lite_model or DEFAULT_OPENAI
        if not os.environ.get("OPENAI_API_KEY"):
            sys.exit("No OPENAI_API_KEY. Copy eval\\.env.example to eval\\.env and add your key.")
    else:
        args.micro_model = args.micro_model or DEFAULT_MICRO
        args.lite_model = args.lite_model or DEFAULT_LITE

    labels = load_labels()
    if args.ids:
        labels = [l for l in labels if l["id"] in set(args.ids)]
    if args.limit:
        labels = labels[: args.limit]
    if not labels:
        print("No labeled receipts to evaluate.")
        return 1

    try:
        who = boto3.client("sts", region_name=REGION).get_caller_identity()
        print(f"AWS account {who['Account']} as {who['Arn'].rsplit('/', 1)[-1]} ({REGION})")
    except (BotoCoreError, ClientError) as err:
        sys.exit(
            f"Not signed in to AWS ({type(err).__name__}). In PowerShell run:\n"
            '  $env:AWS_PROFILE = "receipt-intel-admin"\n'
            "  aws sso login"
        )

    textract, bedrock = make_clients()
    if args.provider == "openai":
        from openai import OpenAI  # only needed for this provider

        openai_client = OpenAI(max_retries=4)
        effort = None if args.reasoning_effort == "none" else args.reasoning_effort
        llm = lambda model, content: call_openai(openai_client, model, content, effort)
    else:
        llm = lambda model, content: call_llm(bedrock, model, content)
    ctx = {
        "textract": textract,
        "llm": llm,
        "micro_model": args.micro_model,
        "lite_model": args.lite_model,
        "refresh_textract": args.refresh_textract,
    }
    approaches = args.approach or list(APPROACHES)
    out_dir = RESULTS / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results: dict[str, list[dict]] = {a: [] for a in approaches}
    with (out_dir / "predictions.jsonl").open("w", encoding="utf-8") as pred_file:
        for i, label in enumerate(labels, 1):
            image = prepare_image(IMAGES / label["image"])
            for approach in approaches:
                try:
                    run = APPROACHES[approach](ctx, label["id"], image)
                    pred = normalize_prediction(run["fields"])
                    result = {
                        "id": label["id"],
                        "truth": label,
                        "pred": pred,
                        "raw": run["fields"],
                        "score": score(label, pred),
                        "math_ok": math_check_passes(pred),
                        "latency_s": run["latency_s"],
                        "cost_usd": run["cost_usd"],
                        "tokens": run["tokens"],
                    }
                    status = "all key fields OK" if all(result["score"].get(f, True) for f in KEY_FIELDS) else "miss"
                except Exception as err:  # keep going; errors are reported
                    result = {"id": label["id"], "error": f"{type(err).__name__}: {err}"}
                    status = result["error"][:70]
                all_results[approach].append(result)
                pred_file.write(json.dumps({"approach": approach, **result}, default=str) + "\n")
                print(f"[{i}/{len(labels)}] {label['id']:<12} {approach:<20} {status}")

    summaries = [summarize(a, r) for a, r in all_results.items()]
    models = {"provider": args.provider, "micro": args.micro_model, "lite": args.lite_model}
    report = write_report(out_dir, summaries, all_results, models)
    print("\n" + report.split("## Misses")[0])
    print(f"Full report: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
