import type { Status } from "./api";
import type { Problem } from "./imageCheck";

// Must match CATEGORIES in services/pipeline/src/receipt_schema.py.
export const CATEGORIES = [
  "groceries",
  "dining",
  "gas",
  "household",
  "pharmacy",
  "kids",
  "cycling",
  "golf",
  "utilities",
  "other",
] as const;

export const STATUS_LABELS: Record<Status, string> = {
  processing: "Reading…",
  needs_review: "Needs your input",
  needs_manual_entry: "Enter by hand",
  processed: "Done",
};

const REASONS: Record<string, string> = {
  category_missing: "Pick a category",
  total_missing: "The total wasn't found",
  total_low_confidence: "The total was hard to read; check it",
  total_mismatch: "Two readings of the total disagree; check it",
  math_mismatch: "Subtotal plus taxes doesn't add up to the total",
  store_missing: "The store name wasn't found",
  date_missing: "The date wasn't found",
  date_implausible: "The date looks wrong",
  unsupported_file_type: "This file type can't be read yet",
};

const FIELD_NAMES: Record<string, string> = {
  store: "Store",
  date: "Date",
  subtotal: "Subtotal",
  gst: "GST",
  pst: "PST",
  total: "Total",
  category: "Category",
};

export function reasonLabel(reason: string): string {
  if (reason in REASONS) return REASONS[reason];
  if (reason.startsWith("unreadable:")) return "The photo couldn't be read; retake it or enter it by hand";
  if (reason.endsWith("_invalid")) {
    const field = reason.slice(0, -"_invalid".length);
    return `${FIELD_NAMES[field] ?? field} isn't valid`;
  }
  return reason.replaceAll("_", " ");
}

export const PROBLEM_LABELS: Record<Problem, string> = {
  blurry: "The photo looks blurry",
  dark: "The photo is quite dark",
  small: "The photo is small; move closer",
};

export function formatMoney(value?: number): string {
  return value === undefined ? "—" : `$${value.toFixed(2)}`;
}

export function currentMonth(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}
