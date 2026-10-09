import { describe, expect, it } from "vitest";
import { currentMonth, formatMoney, reasonLabel } from "./labels";
import { fromForm, toForm } from "./receiptForm";

describe("reasonLabel", () => {
  it("explains pipeline and API reason codes", () => {
    expect(reasonLabel("category_missing")).toBe("Pick a category");
    expect(reasonLabel("unreadable:BadDocumentException")).toMatch(/couldn't be read/);
    expect(reasonLabel("total_invalid")).toBe("Total isn't valid");
    expect(reasonLabel("possible_duplicate")).toMatch(/already added/);
    expect(reasonLabel("something_new")).toBe("something new");
  });
});

describe("formatting", () => {
  it("formats money and months", () => {
    expect(formatMoney(8.5)).toBe("$8.50");
    expect(formatMoney(undefined)).toBe("—");
    expect(currentMonth(new Date(2026, 0, 15))).toBe("2026-01");
  });
});

describe("receipt form", () => {
  it("round-trips a receipt and clears emptied fields", () => {
    const form = toForm({ receiptId: "r1", status: "needs_review", store: "T&T", total: 8.5, subtotal: 8.4 });
    expect(form.total).toBe("8.50");
    const body = fromForm({ ...form, subtotal: "  ", category: "groceries" });
    expect(body).toEqual({
      store: "T&T",
      date: null,
      category: "groceries",
      subtotal: null, // emptied: clears the stored 8.40
      gst: null,
      pst: null,
      total: "8.50",
    });
  });
});
