import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Spending as SpendingData } from "../api";
import { useApi } from "../App";
import { currentMonth, formatMoney } from "../labels";
import {
  barSegments,
  budgetName,
  budgetState,
  monthLabel,
  shiftMonth,
  unbudgeted,
  validLimit,
} from "../spendingView";

/** Month totals, category breakdown and budgets, from GET /spending. */
export function Spending({ month, onMonthChange }: { month: string; onMonthChange: (month: string) => void }) {
  const api = useApi();
  const [data, setData] = useState<SpendingData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    return api
      .getSpending(month)
      .then(setData)
      .catch((err: Error) => setError(err.message));
  }, [api, month]);

  useEffect(() => {
    setData(null);
    void load();
  }, [load]);

  const segments = data ? barSegments(data.byCategory) : [];

  return (
    <section className="spending" aria-label="Spending">
      <div className="month-nav">
        <button className="icon" onClick={() => onMonthChange(shiftMonth(month, -1))} aria-label="Previous month">
          ‹
        </button>
        <h2>{monthLabel(month)}</h2>
        <button
          className="icon"
          onClick={() => onMonthChange(shiftMonth(month, 1))}
          disabled={month >= currentMonth()}
          aria-label="Next month"
        >
          ›
        </button>
      </div>

      {error && (
        <p className="error">
          Couldn't load spending: {error}{" "}
          <button className="link" onClick={() => void load()}>
            Retry
          </button>
        </p>
      )}
      {!data && !error && <p className="muted">Loading…</p>}

      {data && (
        <>
          <p className="kicker">
            Processed · {data.processedCount} receipt{data.processedCount === 1 ? "" : "s"}
          </p>
          <p className="hero">{formatMoney(data.total)}</p>
          <p className="muted">
            GST {formatMoney(data.gst)} · PST {formatMoney(data.pst)}
          </p>

          {data.pendingCount > 0 && (
            <Link to="/?view=attention" className="notice">
              {data.pendingCount} receipt{data.pendingCount === 1 ? "" : "s"} still need
              {data.pendingCount === 1 ? "s" : ""} your input ›
            </Link>
          )}

          {segments.length > 0 ? (
            <>
              <div className="bar" role="img" aria-label="Spending by category">
                {segments.map((s, i) => (
                  <span key={s.category} style={{ width: `${s.width}%`, background: swatch(i) }} />
                ))}
              </div>
              <ul className="legend">
                {segments.map((s, i) => (
                  <li key={s.category}>
                    <i style={{ background: swatch(i) }} />
                    <span>{budgetName(s.category)}</span>
                    <span className="amount">{formatMoney(s.amount)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted">No processed receipts this month.</p>
          )}

          <Budgets data={data} onChanged={load} />
        </>
      )}
    </section>
  );
}

/** Green ramp, darkest for the biggest category. */
function swatch(index: number): string {
  return `hsl(165 ${Math.max(20, 56 - index * 4)}% ${Math.min(80, 24 + index * 6)}%)`;
}

function Budgets({ data, onChanged }: { data: SpendingData; onChanged: () => Promise<void> }) {
  const api = useApi();
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(category: string, limit: string) {
    setError(null);
    try {
      await api.putBudget(category, limit);
      setEditing(null);
      setAdding(false);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function remove(category: string) {
    setError(null);
    try {
      await api.deleteBudget(category);
      setEditing(null);
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const available = unbudgeted(data.budgets);

  return (
    <div className="budgets">
      <h3>Budgets</h3>
      {data.budgets.length === 0 && !adding && (
        <p className="muted">No budgets yet. You'll get an email at 85% and 100% of each one.</p>
      )}

      <ul className="budget-list">
        {data.budgets.map((b) => (
          <li key={b.category} className={`budget ${budgetState(b.percent)}`}>
            <div className="budget-head">
              <strong>{budgetName(b.category)}</strong>
              <span className="amount">
                {formatMoney(b.spent)} of {formatMoney(b.limit)}
              </span>
            </div>
            <div
              className="meter"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.min(b.percent, 100)}
              aria-label={`${budgetName(b.category)} budget used`}
            >
              <span style={{ width: `${Math.min(b.percent, 100)}%` }} />
            </div>
            <div className="budget-foot">
              <span className="muted">
                {b.percent}% used
                {b.percent >= 100 ? " · over budget" : b.percent >= 85 ? " · nearly there" : ""}
              </span>
              {editing !== b.category && (
                <button className="link" onClick={() => setEditing(b.category)}>
                  Edit
                </button>
              )}
            </div>
            {editing === b.category && (
              <LimitForm
                initial={String(b.limit)}
                submitLabel="Save"
                onSubmit={(limit) => save(b.category, limit)}
                onCancel={() => setEditing(null)}
                onRemove={() => remove(b.category)}
              />
            )}
          </li>
        ))}
      </ul>

      {error && <p className="error">{error}</p>}

      {adding ? (
        <AddBudget categories={available} onSubmit={save} onCancel={() => setAdding(false)} />
      ) : (
        available.length > 0 && (
          <button className="secondary" onClick={() => setAdding(true)}>
            Add a budget
          </button>
        )
      )}
    </div>
  );
}

function AddBudget({
  categories,
  onSubmit,
  onCancel,
}: {
  categories: string[];
  onSubmit: (category: string, limit: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [category, setCategory] = useState(categories[0]);
  return (
    <div className="stack budget-form">
      <label>
        Budget for
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((c) => (
            <option key={c} value={c}>
              {budgetName(c)}
            </option>
          ))}
        </select>
      </label>
      <LimitForm initial="" submitLabel="Add budget" onSubmit={(limit) => onSubmit(category, limit)} onCancel={onCancel} />
    </div>
  );
}

function LimitForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
  onRemove,
}: {
  initial: string;
  submitLabel: string;
  onSubmit: (limit: string) => Promise<void>;
  onCancel: () => void;
  onRemove?: () => Promise<void>;
}) {
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="stack budget-form"
      onSubmit={(e) => {
        e.preventDefault();
        void run(() => onSubmit(text));
      }}
    >
      <label>
        Monthly limit
        <input
          inputMode="decimal"
          placeholder="e.g. 600"
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-invalid={text !== "" && !validLimit(text)}
        />
      </label>
      <button className="primary" type="submit" disabled={busy || !validLimit(text)}>
        {submitLabel}
      </button>
      <button type="button" onClick={onCancel} disabled={busy}>
        Cancel
      </button>
      {onRemove && (
        <button type="button" className="danger" onClick={() => void run(onRemove)} disabled={busy}>
          Remove budget
        </button>
      )}
    </form>
  );
}
