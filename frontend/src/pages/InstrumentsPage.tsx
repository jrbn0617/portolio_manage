import { useEffect, useMemo, useState } from "react";
import { createInstrument, deleteInstrument, fetchInstruments } from "../api/instruments";
import type { AssetType, Instrument, InstrumentInput } from "../types";

const ASSET_TYPES: AssetType[] = ["stock", "etf", "fund", "index"];

const emptyForm: InstrumentInput = { ticker: "", name: "", asset_type: "stock", market: "" };

const ALL = "전체";

/** assetType 을 주면 그 유형으로 고정한다 (주식/ETF 메뉴에서 재사용).
 *  주지 않으면 종전처럼 전체를 보여주고 드롭다운으로 고를 수 있다. */
export default function InstrumentsPage({ assetType }: { assetType?: AssetType } = {}) {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [form, setForm] = useState<InstrumentInput>(emptyForm);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sectorFilter, setSectorFilter] = useState(ALL);
  const [industryFilter, setIndustryFilter] = useState(ALL);
  const [tickerFilter, setTickerFilter] = useState("");
  const [assetTypeFilter, setAssetTypeFilter] = useState<string>(assetType ?? ALL);

  async function load() {
    setLoading(true);
    try {
      setInstruments(await fetchInstruments());
    } finally {
      setLoading(false);
    }
  }

  const assetTypeCounts = useMemo(() => {
    const c = new Map<string, number>();
    for (const i of instruments) c.set(i.asset_type, (c.get(i.asset_type) ?? 0) + 1);
    return c;
  }, [instruments]);

  const byAssetType = useMemo(
    () => (assetTypeFilter === ALL ? instruments : instruments.filter((i) => i.asset_type === assetTypeFilter)),
    [instruments, assetTypeFilter]
  );

  const sectors = useMemo(
    () => [ALL, ...Array.from(new Set(byAssetType.map((i) => i.sector).filter((s): s is string => !!s))).sort()],
    [byAssetType]
  );

  const industries = useMemo(() => {
    const pool = sectorFilter === ALL ? byAssetType : byAssetType.filter((i) => i.sector === sectorFilter);
    return [ALL, ...Array.from(new Set(pool.map((i) => i.industry).filter((s): s is string => !!s))).sort()];
  }, [byAssetType, sectorFilter]);

  const filtered = useMemo(() => {
    return byAssetType.filter((i) => {
      if (sectorFilter !== ALL && i.sector !== sectorFilter) return false;
      if (industryFilter !== ALL && i.industry !== industryFilter) return false;
      if (tickerFilter) {
        const q = tickerFilter.trim().toLowerCase();
        if (!i.ticker.toLowerCase().includes(q) && !i.name.toLowerCase().includes(q)) return false;
      }
      return true;
    });
  }, [byAssetType, sectorFilter, industryFilter, tickerFilter]);

  // 섹터를 바꾸면 더 이상 유효하지 않을 수 있는 산업 필터를 초기화한다.
  useEffect(() => {
    if (sectorFilter !== ALL && !industries.includes(industryFilter)) {
      setIndustryFilter(ALL);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectorFilter]);

  // 유형을 바꾸면 이전 유형에서 고른 섹터·산업이 남아 결과가 0건이 되므로 함께 되돌린다.
  useEffect(() => {
    setSectorFilter(ALL);
    setIndustryFilter(ALL);
  }, [assetTypeFilter]);

  // 상위 메뉴(주식/ETF)가 바뀌면 고정 유형도 따라간다.
  useEffect(() => {
    if (assetType) setAssetTypeFilter(assetType);
  }, [assetType]);

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
        {!assetType && (
          <select value={assetTypeFilter} onChange={(e) => setAssetTypeFilter(e.target.value)}>
            <option value={ALL}>유형 전체 ({instruments.length})</option>
            {ASSET_TYPES.filter((t) => assetTypeCounts.has(t)).map((t) => (
              <option key={t} value={t}>
                {t} ({assetTypeCounts.get(t)})
              </option>
            ))}
          </select>
        )}
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
