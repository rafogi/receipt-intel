import type { EditableFields, Receipt } from "./api";

export const MONEY_FIELDS = ["subtotal", "gst", "pst", "total"] as const;

export type Form = Record<"store" | "date" | "category" | (typeof MONEY_FIELDS)[number], string>;

export function toForm(r: Receipt): Form {
  const money = (v?: number) => (v === undefined ? "" : v.toFixed(2));
  return {
    store: r.store ?? "",
    date: r.date ?? "",
    category: r.category ?? "",
    subtotal: money(r.subtotal),
    gst: money(r.gst),
    pst: money(r.pst),
    total: money(r.total),
  };
}

/** Every field is sent: an emptied box clears the stored value (null). */
export function fromForm(form: Form): EditableFields {
  const value = (text: string) => text.trim() || null;
  return {
    store: value(form.store),
    date: value(form.date),
    category: value(form.category),
    subtotal: value(form.subtotal),
    gst: value(form.gst),
    pst: value(form.pst),
    total: value(form.total),
  };
}
