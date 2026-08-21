import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchDividendAdjustedPrices, fetchDividends, fetchMacroIndicator, fetchPrices } from "../api/data";
import type { Dividend, DividendAdjustedPrice, MacroIndicator, Price } from "../types";

function formatNumber(value: number | null): string {
  return value === null ? "" : value.toLocaleString();
}

interface PriceRow extends Price {
  adj_close: number | null;
}

function downloadCsv(filename: string, header: string[], rows: (string | number | null)[][]) {
  const escape = (v: string | number | null) => {
    const s = v === null || v === undefined ? "" : String(v);
    return s.includes(",") || s.includes('"') ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [header.map(escape).join(","), ...rows.map((r) => r.map(escape).join(","))];
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function DataViewPage() {
  const [ticker, setTicker] = useState("");
  const [period, setPeriod] = useState<"D" | "M">("D");
  const [prices, setPrices] = useState<Price[]>([]);
  const [adjPrices, setAdjPrices] = useState<DividendAdjustedPrice[]>([]);
  const [dividends, setDividends] = useState<Dividend[]>([]);

  const [indicatorName, setIndicatorName] = useState("USDKRW");
  const [macro, setMacro] = useState<MacroIndicator[]>([]);

  async function loadInstrumentData() {
    if (!ticker) return;
    const [p, adj, d] = await Promise.all([
      fetchPrices(ticker, period),
      fetchDividendAdjustedPrices(ticker, period),
      fetchDividends(ticker),
    ]);
    setPrices(p);
    setAdjPrices(adj);
    setDividends(d);
  }

  async function loadMacro() {
    if (!indicatorName) return;
    setMacro(await fetchMacroIndicator(indicatorName));
  }

  const priceRows: PriceRow[] = useMemo(() => {
    const adjByDate = new Map(adjPrices.map((a) => [a.date, a.adj_close]));
    return prices.map((p) => ({ ...p, adj_close: adjByDate.get(p.date) ?? null }));
  }, [prices, adjPrices]);

  function handleDownloadPrices() {
    downloadCsv(
      `${ticker}_${period}_prices.csv`,
      ["date", "open", "high", "low", "close", "adj_close", "volume"],
      priceRows.map((p) => [p.date, p.open, p.high, p.low, p.close, p.adj_close, p.volume])
    );
  }

  function handleDownloadDividends() {
    downloadCsv(
      `${ticker}_dividends.csv`,
      ["ex_date", "pay_date", "amount"],
      dividends.map((d) => [d.ex_date, d.pay_date, d.amount])
    );
  }

  return (
    <div>
      <h2>데이터 조회</h2>

      <section style={{ marginBottom: 32 }}>
        <h3>종목 가격 / 배당</h3>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <input placeholder="ticker" value={ticker} onChange={(e) => setTicker(e.target.value)} />
          <select value={period} onChange={(e) => setPeriod(e.target.value as "D" | "M")}>
            <option value="D">일봉</option>
            <option value="M">월봉</option>
          </select>
          <button onClick={loadInstrumentData}>조회</button>
        </div>

        {priceRows.length > 0 && (
          <>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={priceRows} margin={{ left: 10, right: 10 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis
                  yAxisId="left"
                  scale="log"
                  domain={["auto", "auto"]}
                  width={90}
                  tickFormatter={(v) => v.toLocaleString()}
                />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  scale="log"
                  domain={["auto", "auto"]}
                  width={70}
                  tickFormatter={(v) => v.toLocaleString()}
                />
                <Tooltip />
                <Line yAxisId="left" type="monotone" dataKey="close" name="종가" stroke="var(--c-accent-2)" dot={false} />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="high"
                  name="고가"
                  stroke="var(--c-ok-2)"
                  dot={false}
                  strokeDasharray="3 3"
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="low"
                  name="저가"
                  stroke="var(--c-bad-2)"
                  dot={false}
                  strokeDasharray="3 3"
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="adj_close"
                  name="수정종가 지수(최초일=100)"
                  stroke="var(--c-chart-2)"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>

            <ResponsiveContainer width="100%" height={100}>
              <BarChart data={priceRows} margin={{ left: 10 }}>
                <XAxis dataKey="date" hide />
                <YAxis width={90} tickFormatter={(v) => v.toLocaleString()} />
                <Tooltip />
                <Bar dataKey="volume" name="거래량" fill="var(--c-muted)" />
              </BarChart>
            </ResponsiveContainer>

            <div style={{ marginTop: 12 }}>
              <button onClick={handleDownloadPrices}>CSV 다운로드 (가격)</button>
            </div>

            <div style={{ maxHeight: 320, overflowY: "auto", marginTop: 8 }}>
              <table border={1} cellPadding={6} style={{ borderCollapse: "collapse", width: "100%" }}>
                <thead>
                  <tr style={{ position: "sticky", top: 0, background: "var(--c-card)" }}>
                    <th>date</th>
                    <th>open</th>
                    <th>high</th>
                    <th>low</th>
                    <th>close</th>
                    <th>수정종가 지수(최초일=100)</th>
                    <th>volume</th>
                  </tr>
                </thead>
                <tbody>
                  {priceRows.map((p) => (
                    <tr key={p.id}>
                      <td>{p.date}</td>
                      <td>{formatNumber(p.open)}</td>
                      <td>{formatNumber(p.high)}</td>
                      <td>{formatNumber(p.low)}</td>
                      <td>{formatNumber(p.close)}</td>
                      <td>{formatNumber(p.adj_close)}</td>
                      <td>{formatNumber(p.volume)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {dividends.length > 0 && (
          <>
            <div style={{ marginTop: 16 }}>
              <button onClick={handleDownloadDividends}>CSV 다운로드 (배당)</button>
            </div>
            <table border={1} cellPadding={6} style={{ borderCollapse: "collapse", marginTop: 8 }}>
              <thead>
                <tr>
                  <th>ex_date</th>
                  <th>pay_date</th>
                  <th>amount</th>
                </tr>
              </thead>
              <tbody>
                {dividends.map((d) => (
                  <tr key={d.id}>
                    <td>{d.ex_date}</td>
                    <td>{d.pay_date}</td>
                    <td>{d.amount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section>
        <h3>거시지표</h3>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <input value={indicatorName} onChange={(e) => setIndicatorName(e.target.value)} />
          <button onClick={loadMacro}>조회</button>
        </div>

        {macro.length > 0 && (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={macro}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" />
              <YAxis domain={["auto", "auto"]} />
              <Tooltip />
              <Line type="monotone" dataKey="value" stroke="var(--c-ok-2)" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </section>
    </div>
  );
}
