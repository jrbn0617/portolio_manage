import { useEffect, useMemo, useState } from "react";
import {
  deleteMonthlyFundamental,
  fetchMonthlyFundamentalMetrics,
  fetchMonthlyFundamentals,
  upsertMonthlyFundamental,
} from "../api/monthlyFundamentals";
import type { MonthlyFundamental } from "../types";

const METRIC_LABELS: Record<string, string> = {
  free_float_ratio: "유동비율(%)",
  shares_outstanding_monthly: "상장주식수",
};

const CUSTOM_METRIC = "__custom__";

const emptyForm = { ticker: "", date: "", metric: "free_float_ratio", customMetric: "", value: "" };

export default function MonthlyFundamentalsPage() {
  const [metrics, setMetrics] = useState<string[]>([]);
  const [entries, setEntries] = useState<MonthlyFundamental[]>([]);
  const [form, setForm] = useState(emptyForm);
  const [filterTicker, setFilterTicker] = useState("");
  const [filterMetric, setFilterMetric] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const knownMetrics = useMemo(() => {
    const merged = new Set([...Object.keys(METRIC_LABELS), ...metrics]);
    return Array.from(merged);
  }, [metrics]);

  async function loadMetrics() {
    setMetrics(await fetchMonthlyFundamentalMetrics());
  }

  async function loadEntries() {
    setLoading(true);
    try {
      const params: { ticker?: string; metric?: string } = {};
      if (filterTicker) params.ticker = filterTicker;
      if (filterMetric) params.metric = filterMetric;
      setEntries(await fetchMonthlyFundamentals(params));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadMetrics();
    loadEntries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadEntries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterTicker, filterMetric]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const metric = form.metric === CUSTOM_METRIC ? form.customMetric.trim() : form.metric;
    if (!metric) {
      setError("항목명을 입력해주세요.");
      return;
    }
    try {
      await upsertMonthlyFundamental({
        ticker: form.ticker.trim(),
        date: form.date,
        metric,
        value: Number(form.value),
      });
      setForm({ ...emptyForm, metric: form.metric });
      await Promise.all([loadMetrics(), loadEntries()]);
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "저장에 실패했습니다.");
    }
  }

  async function handleDelete(id: number) {
    await deleteMonthlyFundamental(id);
    await loadEntries();
  }

  return (
    <div>
      <h2>월간 펀더멘털 수동 입력</h2>
      <p style={{ color: "var(--c-ink-4)", fontSize: 13 }}>
        유동비율·상장주식수처럼 월 단위로만 갱신되는 항목을 종목별로 직접 입력합니다. 새 항목명을 입력하면
        자동으로 새로운 지표로 추가됩니다.
      </p>

      <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <input
          placeholder="ticker"
          value={form.ticker}
          onChange={(e) => setForm({ ...form, ticker: e.target.value })}
          required
        />
        <input
          type="date"
          value={form.date}
          onChange={(e) => setForm({ ...form, date: e.target.value })}
          required
        />
        <select value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })}>
          {knownMetrics.map((m) => (
            <option key={m} value={m}>
              {METRIC_LABELS[m] ?? m}
            </option>
          ))}
          <option value={CUSTOM_METRIC}>+ 새 항목 직접 입력...</option>
        </select>
        {form.metric === CUSTOM_METRIC && (
          <input
            placeholder="새 항목명 (예: dividend_yield_monthly)"
            value={form.customMetric}
            onChange={(e) => setForm({ ...form, customMetric: e.target.value })}
            required
          />
        )}
        <input
          type="number"
          step="any"
          placeholder="값"
          value={form.value}
          onChange={(e) => setForm({ ...form, value: e.target.value })}
          required
        />
        <button type="submit">저장</button>
      </form>

      {error && <p style={{ color: "var(--c-bad-2)" }}>{error}</p>}

      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
        <input
          placeholder="ticker 필터"
          value={filterTicker}
          onChange={(e) => setFilterTicker(e.target.value)}
        />
        <select value={filterMetric} onChange={(e) => setFilterMetric(e.target.value)}>
          <option value="">항목 전체</option>
          {knownMetrics.map((m) => (
            <option key={m} value={m}>
              {METRIC_LABELS[m] ?? m}
            </option>
          ))}
        </select>
        <span style={{ color: "var(--c-ink-4)" }}>최근 500건</span>
      </div>

      {loading ? (
        <p>불러오는 중...</p>
      ) : (
        <table border={1} cellPadding={6} style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              <th>ticker</th>
              <th>name</th>
              <th>날짜</th>
              <th>항목</th>
              <th style={{ textAlign: "right" }}>값</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td style={{ whiteSpace: "nowrap" }}>{e.ticker}</td>
                <td>{e.name}</td>
                <td style={{ whiteSpace: "nowrap" }}>{e.date}</td>
                <td>{METRIC_LABELS[e.metric] ?? e.metric}</td>
                <td style={{ textAlign: "right" }}>{e.value.toLocaleString()}</td>
                <td>
                  <button onClick={() => handleDelete(e.id)}>삭제</button>
                </td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", color: "var(--c-muted)" }}>
                  데이터가 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
