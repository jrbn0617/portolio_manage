import { useEffect, useMemo, useState } from "react";
import { createInstrument, deleteInstrument, fetchInstruments } from "../api/instruments";
import type { AssetType, Instrument, InstrumentInput } from "../types";

const ASSET_TYPES: AssetType[] = ["stock", "etf", "fund", "index"];

const emptyForm: InstrumentInput = { ticker: "", name: "", asset_type: "stock", market: "" };

const ALL = "전체";

export default function InstrumentsPage() {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [form, setForm] = useState<InstrumentInput>(emptyForm);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sectorFilter, setSectorFilter] = useState(ALL);
  const [industryFilter, setIndustryFilter] = useState(ALL);
  const [tickerFilter, setTickerFilter] = useState("");

  async function load() {
    setLoading(true);
    try {
      setInstruments(await fetchInstruments());
    } finally {
      setLoading(false);
    }
  }

  const sectors = useMemo(
    () => [ALL, ...Array.from(new Set(instruments.map((i) => i.sector).filter((s): s is string => !!s))).sort()],
    [instruments]
  );

  const industries = useMemo(() => {
    const pool = sectorFilter === ALL ? instruments : instruments.filter((i) => i.sector === sectorFilter);
    return [ALL, ...Array.from(new Set(pool.map((i) => i.industry).filter((s): s is string => !!s))).sort()];
  }, [instruments, sectorFilter]);

  const filtered = useMemo(() => {
    return instruments.filter((i) => {
      if (sectorFilter !== ALL && i.sector !== sectorFilter) return false;
      if (industryFilter !== ALL && i.industry !== industryFilter) return false;
      if (tickerFilter) {
        const q = tickerFilter.trim().toLowerCase();
        if (!i.ticker.toLowerCase().includes(q) && !i.name.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [instruments, sectorFilter, industryFilter, tickerFilter]);

  // 섹터를 바꾸면 더 이상 유효하지 않을 수 있는 산업 필터를 초기화한다.
  useEffect(() => {
    if (sectorFilter !== ALL && !industries.includes(industryFilter)) {
      setIndustryFilter(ALL);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectorFilter]);

  useEffect(() => {
    load();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await createInstrument({ ...form, market: form.market || null });
      setForm(emptyForm);
      await load();
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "등록에 실패했습니다.");
    }
  }

  async function handleDelete(id: number) {
    await deleteInstrument(id);
    await load();
  }

  return (
    <div>
      <h2>종목 마스터 관리</h2>

      <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <input
          placeholder="ticker"
          value={form.ticker}
          onChange={(e) => setForm({ ...form, ticker: e.target.value })}
          required
        />
        <input
          placeholder="name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          required
        />
        <select
          value={form.asset_type}
          onChange={(e) => setForm({ ...form, asset_type: e.target.value as AssetType })}
        >
          {ASSET_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          placeholder="market (예: KOSPI)"
          value={form.market ?? ""}
          onChange={(e) => setForm({ ...form, market: e.target.value })}
        />
        <button type="submit">추가</button>
      </form>

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
        <input
          placeholder="ticker/name 검색"
          value={tickerFilter}
          onChange={(e) => setTickerFilter(e.target.value)}
        />
        <select value={sectorFilter} onChange={(e) => setSectorFilter(e.target.value)}>
          {sectors.map((s) => (
            <option key={s} value={s}>
              {s === ALL ? "섹터 전체" : s}
            </option>
          ))}
        </select>
        <select value={industryFilter} onChange={(e) => setIndustryFilter(e.target.value)}>
          {industries.map((s) => (
            <option key={s} value={s}>
              {s === ALL ? "산업 전체" : s}
            </option>
          ))}
        </select>
        <span style={{ color: "#666" }}>
          {filtered.length} / {instruments.length}개
        </span>
      </div>

      {loading ? (
        <p>불러오는 중...</p>
      ) : (
        <table
          border={1}
          cellPadding={6}
          style={{ borderCollapse: "collapse", width: "100%", tableLayout: "fixed" }}
        >
          <colgroup>
            <col style={{ width: 90 }} />
            <col style={{ width: 220 }} />
            <col style={{ width: 90 }} />
            <col style={{ width: 130 }} />
            <col style={{ width: 130 }} />
            <col style={{ width: 150 }} />
            <col style={{ width: 60 }} />
          </colgroup>
          <thead>
            <tr>
              <th>ticker</th>
              <th>name</th>
              <th>asset_type</th>
              <th>market</th>
              <th>섹터</th>
              <th>산업</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((i) => (
              <tr key={i.id}>
                <td style={{ whiteSpace: "nowrap" }}>{i.ticker}</td>
                <td>
                  <div style={{ overflowX: "auto", whiteSpace: "nowrap" }}>{i.name}</div>
                </td>
                <td style={{ whiteSpace: "nowrap" }}>{i.asset_type}</td>
                <td style={{ whiteSpace: "nowrap" }}>{i.market}</td>
                <td style={{ whiteSpace: "nowrap" }}>{i.sector}</td>
                <td style={{ whiteSpace: "nowrap" }}>{i.industry}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button onClick={() => handleDelete(i.id)}>삭제</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
