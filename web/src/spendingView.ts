import type { BudgetProgress, CategorySpend } from "./api";
import { CATEGORIES } from "./labels";

/** Budget key for all categories together (OVERALL in services/pipeline/src/spending.py). */
export const OVERALL = "total";

export function budgetName(category: string): string {
  return category === OVERALL ? "All spending" : category.charAt(0).toUpperCase() + category.slice(1);
}

/** "2026-10" moved by `delta` months. */
export function shiftMonth(month: string, delta: number): string {
  const [year, mon] = month.split("-").map(Number);
  const index = year * 12 + (mon - 1) + delta;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

export function monthLabel(month: string): string {
  const [year, mon] = month.split("-").map(Number);
  return new Date(year, mon - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

/** Widths for the category bar, in percent, largest first; zero amounts are dropped. */
export function barSegments(byCategory: CategorySpend[]): { category: string; amount: number; width: number }[] {
  const rows = byCategory.filter((c) => c.amount > 0);
  const total = rows.reduce((sum, c) => sum + c.amount, 0);
  return rows.map((c) => ({ category: c.category, amount: c.amount, width: (c.amount / total) * 100 }));
}

export type BudgetState = "ok" | "near" | "over";

/** Matches the alert thresholds: 85% and 100%. */
export function budgetState(percent: number): BudgetState {
  return percent >= 100 ? "over" : percent >= 85 ? "near" : "ok";
}

/** Categories with no budget yet, overall first. */
export function unbudgeted(budgets: BudgetProgress[]): string[] {
  const taken = new Set(budgets.map((b) => b.category));
  return [OVERALL, ...CATEGORIES].filter((c) => !taken.has(c));
}

/** True for "600", "600.5", "$1,200.00"; the API enforces the real limits. */
export function validLimit(text: string): boolean {
  const n = Number(text.trim().replace(/^\$/, "").replaceAll(",", ""));
  return text.trim() !== "" && Number.isFinite(n) && n > 0;
}
