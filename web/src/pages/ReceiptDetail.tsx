import { type FormEvent, useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { ApiError, type Receipt } from "../api";
import { useApi } from "../App";
import { CATEGORIES, STATUS_LABELS, formatMoney, reasonLabel } from "../labels";
import { type Form, MONEY_FIELDS, fromForm, toForm } from "../receiptForm";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 90_000;

export function ReceiptDetail() {
  const api = useApi();
  const { id = "" } = useParams();
  const justUploaded = Boolean((useLocation().state as { justUploaded?: boolean } | null)?.justUploaded);

  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [waiting, setWaiting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Form | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveReasons, setSaveReasons] = useState<string[]>([]);

  // Poll while the photo is still on its way through the pipeline.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const started = Date.now();
    const load = async () => {
      try {
        const r = await api.getReceipt(id);
        if (cancelled) return;
        const pending = !r || r.status === "processing";
        if (r) setReceipt(r);
        if (pending && (justUploaded || r) && Date.now() - started < POLL_LIMIT_MS) {
          timer = window.setTimeout(load, POLL_MS);
        } else {
          setWaiting(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError((err as Error).message);
          setWaiting(false);
        }
      }
    };
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [api, id, justUploaded]);

  useEffect(() => {
    if (receipt && receipt.status !== "processed" && receipt.status !== "processing") setEditing(true);
  }, [receipt?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (receipt && editing && !form) setForm(toForm(receipt));
  }, [receipt, editing, form]);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    setSaving(true);
    setSaveReasons([]);
    setError(null);
    try {
      const saved = await api.updateReceipt(id, fromForm(form));
      setReceipt((current) => ({ ...saved, photoUrl: current?.photoUrl }));
      setEditing(false);
      setForm(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) setSaveReasons(err.reasons);
      else setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (error && !receipt) return <main className="page error">{error}</main>;
  if (!receipt) {
    return (
      <main className="page muted">
        {waiting ? "Uploading and reading the receipt…" : "Receipt not found."} <Link to="/">Back</Link>
      </main>
    );
  }

  const set = (field: keyof Form) => (e: { target: { value: string } }) =>
    setForm((f) => (f ? { ...f, [field]: e.target.value } : f));

  return (
    <main className="page">
      <Link to="/" className="back">
        ← Receipts
      </Link>
      <h1>{receipt.store ?? "Receipt"}</h1>
      <p>
        <span className={`badge ${receipt.status}`}>{STATUS_LABELS[receipt.status] ?? receipt.status}</span>
        {receipt.source === "manual" && <span className="muted"> · edited by you</span>}
      </p>
      {receipt.status === "processing" && <p className="muted">Reading the receipt… this page updates by itself.</p>}

      {(receipt.reasons?.length ?? 0) > 0 && receipt.status !== "processed" && (
        <div className="warning">
          <ul>
            {receipt.reasons!.map((r) => (
              <li key={r}>{reasonLabel(r)}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <p className="error">{error}</p>}

      {editing && form ? (
        <form className="edit" onSubmit={save}>
          <label>
            Store
            <input value={form.store} onChange={set("store")} autoComplete="off" />
          </label>
          <label>
            Date
            <input type="date" value={form.date} onChange={set("date")} />
          </label>
          <label>
            Category
            <select value={form.category} onChange={set("category")}>
              <option value="">Choose…</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          {MONEY_FIELDS.map((f) => (
            <label key={f}>
              {f === "gst" || f === "pst" ? f.toUpperCase() : f[0].toUpperCase() + f.slice(1)}
              <input inputMode="decimal" value={form[f]} onChange={set(f)} placeholder={f === "total" ? "Required" : "If printed"} />
            </label>
          ))}
          {saveReasons.length > 0 && (
            <ul className="error">
              {saveReasons.map((r) => (
                <li key={r}>{reasonLabel(r)}</li>
              ))}
            </ul>
          )}
          <button className="primary" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          {receipt.status === "processed" && (
            <button type="button" className="secondary" onClick={() => setEditing(false)}>
              Cancel
            </button>
          )}
        </form>
      ) : (
        <dl className="fields">
          <dt>Date</dt>
          <dd>{receipt.date ?? "—"}</dd>
          <dt>Category</dt>
          <dd>{receipt.category ?? "—"}</dd>
          <dt>Subtotal</dt>
          <dd>{formatMoney(receipt.subtotal)}</dd>
          <dt>GST</dt>
          <dd>{formatMoney(receipt.gst)}</dd>
          <dt>PST</dt>
          <dd>{formatMoney(receipt.pst)}</dd>
          <dt>Total</dt>
          <dd className="amount">{formatMoney(receipt.total)}</dd>
        </dl>
      )}
      {!editing && receipt.status !== "processing" && (
        <button className="secondary" onClick={() => setEditing(true)}>
          Edit
        </button>
      )}

      {receipt.photoUrl && <img src={receipt.photoUrl} alt="Receipt photo" className="photo" />}
    </main>
  );
}
