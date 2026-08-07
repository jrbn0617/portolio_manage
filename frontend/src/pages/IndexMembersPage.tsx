import { useEffect, useMemo, useState } from "react";
import { fetchConstituents, fetchIndexNames, fetchSnapshots } from "../api/indexMemberships";
import type { IndexConstituent } from "../types";

const INDEX_ORDER = ["KOSPI200", "KOSDAQ150", "KOSPI", "KOSDAQ"];

function formatMarketCap(value: number | null): string {
  if (value === null) return "-";
  const eok = value / 100_000_000;
  return `${Math.round(eok).toLocaleString()}억`;
}

export default function IndexMembersPage() {
  const [indexNames, setIndexNames] = useState<string[]>([]);
  const [indexName, setIndexName] = useState<string>("");
  const [snapshots, setSnapshots] = useState<string[]>([]);
  const [asOfDate, setAsOfDate] = useState<string>("");
  const [constituents, setConstituents] = useState<IndexConstituent[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchIndexNames().then((names) => {
      const sorted = [...names].sort((a, b) => {
        const ia = INDEX_ORDER.indexOf(a);
        const ib = INDEX_ORDER.indexOf(b);
        return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
      });
      setIndexNames(sorted);
      if (sorted.length > 0) setIndexName(sorted[0]);
    });
  }, []);

  useEffect(() => {
    if (!indexName) return;
    fetchSnapshots(indexName).then((dates) => {
      setSnapshots(dates);
      setAsOfDate(dates[0] ?? "");
    });
  }, [indexName]);

  useEffect(() => {
    if (!indexName || !asOfDate) return;
    setLoading(true);
    setError(null);
    fetchConstituents(indexName, asOfDate)
      .then(setConstituents)
      .catch((err) => setError(err.response?.data?.detail ?? "조회에 실패했습니다."))
      .finally(() => setLoading(false));
  }, [indexName, asOfDate]);

  const filtered = useMemo(() => {
    if (!search.trim()) return constituents;
    const q = search.trim().toLowerCase();
    return constituents.filter((c) => c.ticker.toLowerCase().includes(q) || c.name.toLowerCase().includes(q));
  }, [constituents, search]);

  return (
    <div>
      <h2>지수 구성종목</h2>

      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
        <select value={indexName} onChange={(e) => setIndexName(e.target.value)}>
          {indexNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <select value={asOfDate} onChange={(e) => setAsOfDate(e.target.value)}>
          {snapshots.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <input
          placeholder="ticker/name 검색"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span style={{ color: "#666" }}>
          {filtered.length} / {constituents.length}종목
        </span>
      </div>

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {loading ? (
        <p>불러오는 중...</p>
      ) : (
        <table border={1} cellPadding={6} style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              <th>ticker</th>
              <th>name</th>
              <th>market</th>
              <th>KRX 업종</th>
              <th style={{ textAlign: "right" }}>시가총액</th>
              <th style={{ textAlign: "right" }}>종가</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((c) => (
              <tr key={c.instrument_id}>
                <td style={{ whiteSpace: "nowrap" }}>{c.ticker}</td>
                <td>{c.name}</td>
                <td style={{ whiteSpace: "nowrap" }}>{c.market}</td>
                <td style={{ whiteSpace: "nowrap" }}>{c.krx_sector}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>{formatMarketCap(c.market_cap)}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {c.close !== null ? c.close.toLocaleString() : "-"}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", color: "#888" }}>
                  구성종목이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
