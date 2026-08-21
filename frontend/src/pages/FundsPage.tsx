import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  fetchFundCategories, fetchFundCompanies, fetchFundDetail, fetchFundNavs,
  fetchFundSettlements, fetchFundStats, fetchFunds,
} from "../api/funds";
import type {
  Fund, FundDetail, FundKind, FundNavPoint, FundSettlement, FundStats,
} from "../types";

const ALL = "전체";
// 연금 성격은 배제 대상이 아니라 **선택 축**이다 — 연금·퇴직연금 포트폴리오의 유니버스가 된다.
const PENSION_OPTIONS = [ALL, "연금전체", "퇴직연금", "개인연금", "일반"];
const PENSION_COLOR: Record<string, string> = { 퇴직연금: "var(--c-src-dg)", 개인연금: "var(--c-accent)" };

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

const num = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined ? "-" : v.toLocaleString(undefined, { maximumFractionDigits: d });

export default function FundsPage() {
  const [stats, setStats] = useState<FundStats | null>(null);
  const [categories, setCategories] = useState<string[]>([]);
  const [companies, setCompanies] = useState<string[]>([]);

  const [q, setQ] = useState("");
  const [category, setCategory] = useState(ALL);
  const [company, setCompany] = useState(ALL);
  const [pension, setPension] = useState(ALL);
  const [kind, setKind] = useState<FundKind>("manage");
  const [funds, setFunds] = useState<Fund[]>([]);
  const [loading, setLoading] = useState(false);

  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<FundDetail | null>(null);
  const [navs, setNavs] = useState<FundNavPoint[]>([]);
  const [settlements, setSettlements] = useState<FundSettlement[]>([]);
  const [years, setYears] = useState("5");
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    fetchFundStats().then(setStats).catch(() => {});
    fetchFundCategories().then(setCategories).catch(() => {});
    fetchFundCompanies().then(setCompanies).catch(() => {});
  }, []);

  async function search() {
    setLoading(true);
    try {
      setFunds(await fetchFunds({
        q: q.trim() || undefined,
        category: category === ALL ? undefined : category,
        manage_company: company === ALL ? undefined : company,
        pension: pension === ALL ? undefined : pension,
        kind,
        limit: 300,
      }));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { search(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ },
            [kind, pension]);

  async function select(code: string) {
    setSelected(code);
    setDetailLoading(true);
    try {
      const since = years === ALL ? undefined : (() => {
        const d = new Date();
        d.setFullYear(d.getFullYear() - Number(years));
        return d.toISOString().slice(0, 10);
      })();
      const [d, n, s] = await Promise.all([
        fetchFundDetail(code), fetchFundNavs(code, since), fetchFundSettlements(code),
      ]);
      setDetail(d); setNavs(n); setSettlements(s);
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => { if (selected) select(selected); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [years]);

  // 수정기준가는 절대값이 기준가와 자릿수가 달라(100 기준 리베이스) 같은 축에 못 둔다.
  // 첫날을 100으로 맞춰 서로 비교되게 지수화한다.
  const chart = useMemo(() => {
    const first = navs.find((p) => p.nav != null && p.adj_nav != null);
    if (!first) return [];
    return navs.map((p) => ({
      base_dt: p.base_dt,
      nav: p.nav,
      navIdx: p.nav != null && first.nav ? (p.nav / first.nav) * 100 : null,
      adjIdx: p.adj_nav != null && first.adj_nav ? (p.adj_nav / first.adj_nav) * 100 : null,
    }));
  }, [navs]);

  return (
    <div>
      <h2>펀드</h2>

      {stats && (
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13, color: "var(--c-ink-3)",
                      background: "var(--c-card-2)", border: "1px solid var(--c-rule)", borderRadius: 6,
                      padding: "10px 14px", marginBottom: 16 }}>
          <span>운용펀드 <b>{stats.manage_funds.toLocaleString()}</b></span>
          <span>클래스 <b>{stats.class_funds.toLocaleString()}</b></span>
          <span>미매핑 <b>{stats.unmapped.toLocaleString()}</b></span>
          <span>기준가 <b>{stats.nav_rows.toLocaleString()}</b>행</span>
          <span style={{ color: "var(--c-src-manual)" }}>{stats.nav_from} ~ <b>{stats.nav_to}</b></span>
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
        <input placeholder="펀드명 / 펀드코드" value={q} style={{ width: 220 }}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && search()} />
        <select value={kind} onChange={(e) => setKind(e.target.value as FundKind)}>
          <option value="manage">운용펀드</option>
          <option value="class">클래스</option>
          <option value="all">전체</option>
        </select>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value={ALL}>유형 전체</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={pension} onChange={(e) => setPension(e.target.value)}>
          {PENSION_OPTIONS.map((p) => (
            <option key={p} value={p}>{p === ALL ? "연금 전체구분" : p}</option>
          ))}
        </select>
        <select value={company} onChange={(e) => setCompany(e.target.value)}>
          <option value={ALL}>운용사 전체</option>
          {companies.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <button onClick={search}>조회</button>
        <span style={{ color: "var(--c-ink-4)" }}>{funds.length}건</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 380px) 1fr", gap: 16,
                    alignItems: "start" }}>
        <div style={{ maxHeight: 620, overflowY: "auto", border: "1px solid var(--c-rule)", borderRadius: 6 }}>
          {loading ? <p style={{ padding: 12 }}>불러오는 중...</p> : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <tbody>
                {funds.map((f) => (
                  <tr key={f.fund_code}
                      onClick={() => select(f.fund_code)}
                      style={{ cursor: "pointer", borderBottom: "1px solid var(--c-rule-2)",
                               background: selected === f.fund_code ? "var(--c-accent-bg)" : undefined }}>
                    <td style={{ padding: "7px 10px" }}>
                      <div style={{ fontWeight: selected === f.fund_code ? 700 : 400 }}>{f.name}</div>
                      <div style={{ color: "var(--c-muted)", fontSize: 11, marginTop: 2 }}>
                        {f.fund_code}
                        {f.class_str && <> · <span style={{ color: "var(--c-src-seibro)" }}>{f.class_str}</span></>}
                        {f.category && <> · {f.category}</>}
                        {f.pension_type && (
                          <> · <span style={{ color: PENSION_COLOR[f.pension_type] }}>
                            {f.pension_type}</span></>
                        )}
                        {f.special && <> · <span style={{ color: "var(--c-warn-2)" }}>특수</span></>}
                      </div>
                    </td>
                  </tr>
                ))}
                {funds.length === 0 && (
                  <tr><td style={{ padding: 12, color: "var(--c-muted)" }}>결과가 없습니다.</td></tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        <div>
          {!detail ? (
            <p style={{ color: "var(--c-muted)" }}>왼쪽에서 펀드를 선택하세요.</p>
          ) : (
            <>
              <h3 style={{ margin: "0 0 4px" }}>{detail.name}</h3>
              <p style={{ color: "var(--c-src-manual)", fontSize: 12.5, margin: "0 0 12px" }}>
                {detail.fund_code} · {detail.manage_company ?? "-"} · {detail.category ?? "-"}
                {detail.incept_dt && <> · 설정 {detail.incept_dt}</>}
                {!detail.is_manage_fund && detail.master_fund_code && (
                  <> · 운용펀드 <code>{detail.master_fund_code}</code></>
                )}
              </p>

              <div style={{ display: "flex", gap: 8, marginBottom: 10, alignItems: "center" }}>
                <select value={years} onChange={(e) => setYears(e.target.value)}>
                  <option value="1">최근 1년</option>
                  <option value="3">최근 3년</option>
                  <option value="5">최근 5년</option>
                  <option value="10">최근 10년</option>
                  <option value={ALL}>전체</option>
                </select>
                <span style={{ color: "var(--c-src-manual)", fontSize: 12.5 }}>
                  {detail.nav_from} ~ {detail.nav_to} · {detail.nav_count.toLocaleString()}행
                  · 결산 {detail.settlement_count}건
                </span>
                <button style={{ marginLeft: "auto" }} disabled={navs.length === 0}
                        onClick={() => downloadCsv(
                          `fund_${detail.fund_code}_nav.csv`,
                          ["base_dt", "nav", "adj_nav", "adj_factor", "aum"],
                          navs.map((p) => [p.base_dt, p.nav, p.adj_nav, p.adj_factor, p.aum]))}>
                  CSV
                </button>
              </div>

              {detailLoading ? <p>불러오는 중...</p> : chart.length > 0 && (
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={chart} margin={{ left: 10, right: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="base_dt" minTickGap={40} />
                    <YAxis scale="log" domain={["auto", "auto"]} width={70}
                           tickFormatter={(v) => Number(v).toLocaleString()} />
                    <Tooltip formatter={(v: number) => num(v)} />
                    <Legend />
                    <Line type="monotone" dataKey="navIdx" name="기준가 지수" stroke="var(--c-accent-2)" dot={false} />
                    <Line type="monotone" dataKey="adjIdx" name="수정기준가 지수(결산반영)"
                          stroke="var(--c-chart-2)" dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              )}
              <p style={{ color: "var(--c-muted)", fontSize: 11.5, margin: "4px 0 16px" }}>
                두 계열 모두 조회 구간 첫날을 100으로 맞춘 지수다. 벌어지는 폭이 결산·분배로
                빠져나간 몫이며, 성과 비교에는 수정기준가를 쓴다.
              </p>

              {detail.classes.length > 0 && (
                <>
                  <h4 style={{ margin: "0 0 6px" }}>종류형 클래스 {detail.classes.length}개</h4>
                  <div style={{ maxHeight: 220, overflowY: "auto", marginBottom: 16 }}>
                    <table border={1} cellPadding={5}
                           style={{ borderCollapse: "collapse", width: "100%", fontSize: 12.5 }}>
                      <thead><tr>
                        <th style={{ textAlign: "left" }}>클래스</th>
                        <th style={{ textAlign: "left" }}>펀드명</th>
                        <th>최근 기준가</th><th>기준일</th>
                      </tr></thead>
                      <tbody>
                        {detail.classes.map((c) => (
                          <tr key={c.fund_code}
                              style={{ background: c.fund_code === detail.fund_code ? "var(--c-accent-bg)" : undefined }}>
                            <td style={{ color: "var(--c-src-seibro)", fontWeight: 700 }}>{c.class_str ?? "-"}</td>
                            <td>
                              <span style={{ cursor: "pointer", textDecoration: "underline" }}
                                    onClick={() => select(c.fund_code)}>{c.name}</span>
                              {c.pension_type && (
                                <span style={{ color: PENSION_COLOR[c.pension_type] }}>
                                  {" · "}{c.pension_type}</span>
                              )}
                              {c.special && <span style={{ color: "var(--c-warn-2)" }}> · 특수</span>}
                            </td>
                            <td style={{ textAlign: "right" }}>{num(c.last_nav)}</td>
                            <td style={{ textAlign: "center", color: "var(--c-src-manual)" }}>{c.last_dt ?? "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {settlements.length > 0 && (
                <>
                  <h4 style={{ margin: "0 0 6px" }}>결산 이력 {settlements.length}건</h4>
                  <div style={{ maxHeight: 220, overflowY: "auto" }}>
                    <table border={1} cellPadding={5}
                           style={{ borderCollapse: "collapse", width: "100%", fontSize: 12.5 }}>
                      <thead><tr>
                        <th>회계기말</th><th>구분</th><th>결산전</th><th>결산후</th>
                        <th>수정계수</th><th>기준가 하락일</th>
                      </tr></thead>
                      <tbody>
                        {settlements.map((s, i) => (
                          <tr key={i}>
                            <td style={{ textAlign: "center" }}>{s.period_end_value}</td>
                            <td style={{ textAlign: "center" }}>{s.settlement_type}</td>
                            <td style={{ textAlign: "right" }}>{num(s.nav)}</td>
                            <td style={{ textAlign: "right" }}>{num(s.post_settlement_nav)}</td>
                            <td style={{ textAlign: "right" }}>
                              {s.nav && s.post_settlement_nav
                                ? num(s.nav / s.post_settlement_nav, 6) : "-"}
                            </td>
                            <td style={{ textAlign: "center", color: "var(--c-src-manual)" }}>{s.ex_dividend_dt ?? "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
