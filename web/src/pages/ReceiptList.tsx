import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { Receipt } from "../api";
import { useApi } from "../App";
import { STATUS_LABELS, currentMonth, formatMoney } from "../labels";

type View = "attention" | "month";

export function ReceiptList() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const view: View = params.get("view") === "month" ? "month" : "attention";
  const month = params.get("month") ?? currentMonth();

  const [receipts, setReceipts] = useState<Receipt[] | null>(null);
  const [nextToken, setNextToken] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);

  const query = view === "month" ? { month } : { status: "needs_attention" };

  useEffect(() => {
    let cancelled = false;
    setReceipts(null);
    setError(null);
    api
      .listReceipts(query)
      .then((page) => {
        if (cancelled) return;
        setReceipts(page.receipts);
        setNextToken(page.nextToken);
      })
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, view, month]);

  async function loadMore() {
    if (!nextToken) return;
    const page = await api.listReceipts({ ...query, nextToken });
    setReceipts((current) => [...(current ?? []), ...page.receipts]);
    setNextToken(page.nextToken);
  }

  const total = (receipts ?? []).reduce((sum, r) => sum + (r.status === "processed" ? (r.total ?? 0) : 0), 0);

  return (
    <main className="page">
      <nav className="tabs" aria-label="Receipt views">
        <button className={view === "attention" ? "active" : ""} onClick={() => setParams({ view: "attention" })}>
          Needs your input
        </button>
        <button className={view === "month" ? "active" : ""} onClick={() => setParams({ view: "month", month })}>
          By month
        </button>
      </nav>

      {view === "month" && (
        <div className="month-bar">
          <input
            type="month"
            value={month}
            onChange={(e) => e.target.value && setParams({ view: "month", month: e.target.value })}
            aria-label="Month"
          />
          {receipts && <span className="muted">Processed: {formatMoney(total)}</span>}
        </div>
      )}

      {error && <p className="error">{error}</p>}
      {!receipts && !error && <p className="muted">Loading…</p>}
      {receipts?.length === 0 && (
        <p className="muted">{view === "attention" ? "Nothing needs your input." : "No receipts this month."}</p>
      )}

      <ul className="receipts">
        {receipts?.map((r) => (
          <li key={r.receiptId}>
            <Link to={`/receipts/${r.receiptId}`} className="receipt-row">
              <span className="store">{r.store ?? "Unknown store"}</span>
              <span className="amount">{formatMoney(r.total)}</span>
              <span className="meta">
                {r.date ?? "No date"} · {r.category ?? "No category"}
              </span>
              <span className={`badge ${r.status}`}>{STATUS_LABELS[r.status] ?? r.status}</span>
            </Link>
          </li>
        ))}
      </ul>
      {nextToken && (
        <button className="secondary" onClick={loadMore}>
          Load more
        </button>
      )}

      <Link to="/upload" className="fab" aria-label="Add a receipt">
        + Add receipt
      </Link>
    </main>
  );
}
