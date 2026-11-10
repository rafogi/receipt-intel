import { describe, expect, it } from "vitest";
import { barSegments, budgetName, budgetState, monthLabel, shiftMonth, unbudgeted, validLimit } from "./spendingView";

describe("shiftMonth", () => {
  it("crosses year boundaries both ways", () => {
    expect(shiftMonth("2026-10", 1)).toBe("2026-11");
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftMonth("2026-03", -14)).toBe("2025-01");
  });
});

describe("monthLabel", () => {
  it("names the month and year", () => {
    expect(monthLabel("2026-10")).toMatch(/2026/);
  });
});

describe("barSegments", () => {
  it("weights by amount and drops empty categories", () => {
    const segments = barSegments([
      { category: "groceries", amount: 75, count: 2 },
      { category: "dining", amount: 25, count: 1 },
      { category: "gas", amount: 0, count: 0 },
    ]);
    expect(segments.map((s) => [s.category, s.width])).toEqual([["groceries", 75], ["dining", 25]]);
  });

  it("is empty with no spending", () => {
    expect(barSegments([])).toEqual([]);
  });
});

describe("budgetState", () => {
  it("uses the alert thresholds", () => {
    expect(budgetState(84)).toBe("ok");
    expect(budgetState(85)).toBe("near");
    expect(budgetState(99)).toBe("near");
    expect(budgetState(100)).toBe("over");
    expect(budgetState(250)).toBe("over");
  });
});

describe("unbudgeted", () => {
  it("lists categories without a budget, overall first", () => {
    const list = unbudgeted([{ category: "groceries", limit: 25, spent: 0, percent: 0 }]);
    expect(list[0]).toBe("total");
    expect(list).not.toContain("groceries");
    expect(list).toContain("dining");
  });
});

describe("validLimit", () => {
  it("accepts amounts the API accepts and rejects the rest", () => {
    expect(validLimit("600")).toBe(true);
    expect(validLimit("$1,200.50")).toBe(true);
    expect(validLimit("")).toBe(false);
    expect(validLimit("0")).toBe(false);
    expect(validLimit("-5")).toBe(false);
    expect(validLimit("abc")).toBe(false);
  });
});

describe("budgetName", () => {
  it("names the overall budget", () => {
    expect(budgetName("total")).toBe("All spending");
    expect(budgetName("groceries")).toBe("Groceries");
  });
});
