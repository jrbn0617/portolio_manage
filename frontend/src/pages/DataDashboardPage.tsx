import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchDataSources } from "../api/dataSources";
import { fetchBatchRuns } from "../api/batches";
import type { BatchRun, DataSource } from "../types";
import { SOURCE_COLOR } from "../lib/sources";

/** 자산군 바로가기 — 종목 수는 화면을 열자마자 나와야 해서 요약 API 없이 링크만 둔다. */
const SECTIONS = [
  { to: "/data/stocks", label: "주식", note: "종목 마스터 · 데이터 조회 · 지수 구성종목 · 월간 펀더멘털" },
  { to: "/data/etf", label: "ETF", note: "종목 마스터 · 데이터 조회" },
  { to: "/data/funds", label: "펀드", note: "운용펀드 · 클래스 · 기준가" },
  { to: "/data/indices", label: "지수", note: "블룸버그 수신 티커 관리" },
];

function fmtDate(iso: string | null) {
  return iso ? iso.slice(0, 10) : "—";
}

function fmtWhen(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes(),
  ).padStart(2, "0")}`;
}

export default function DataDashboardPage() {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [runs, setRuns] = useState<BatchRun[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchDataSources(), fetchBatchRuns(12)])
      .then(([s, r]) => {
        setSources(s);
        setRuns(r);
      })
      .catch(() => setError("현황을 불러오지 못했습니다. 백엔드가 떠 있는지 확인하세요."));
  }, []);

  const stale = sources.filter((s) => s.stale);
  const pending = sources.filter((s) => s.pending && !s.stale);
  const failed = runs.filter((r) => r.status === "failed");

  return (
    <div className="dash">
      <div className="dash-head">
        <h2>데이터 현황</h2>
        <p>
          입수 경로 {sources.length}종 · 최근 배치 {runs.length}건.
          자세한 내역은 <Link to="/data/batches">배치 관리</Link>에 있다.
        </p>
      </div>

      {error && <p className="dash-err">{error}</p>}

      <div className="dash-kpis">
        <article className={stale.length ? "dash-kpi bad" : "dash-kpi good"}>
          <span>지연된 데이터</span>
          <b>{stale.length}건</b>
          <em>{stale.length ? stale.map((s) => s.label).join(" · ") : "모두 최신"}</em>
        </article>
        <article className="dash-kpi">
          <span>수집 예정</span>
          <b>{pending.length}건</b>
          <em>{pending.length ? pending.map((s) => s.label).join(" · ") : "없음"}</em>
        </article>
        <article className={failed.length ? "dash-kpi bad" : "dash-kpi good"}>
          <span>최근 배치 실패</span>
          <b>{failed.length}건</b>
          <em>
            {failed.length
              ? Array.from(new Set(failed.map((r) => r.job_name))).join(" · ")
              : "최근 12건 모두 성공"}
          </em>
        </article>
      </div>

      <h3 className="dash-h3">자산군</h3>
      <div className="dash-cards">
        {SECTIONS.map((s) => (
          <Link key={s.to} to={s.to} className="dash-card">
            <b>{s.label}</b>
            <span>{s.note}</span>
          </Link>
        ))}
      </div>

      <h3 className="dash-h3">데이터별 최신 수신일</h3>
      <div className="scroll">
        <table className="dash-table">
          <thead>
            <tr>
              <th>데이터</th>
              <th>출처</th>
              <th>수집</th>
              <th>최신</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.key}>
                <td>{s.label}</td>
                <td style={{ color: SOURCE_COLOR[s.source] ?? "#374151" }}>{s.source}</td>
                <td className="muted">{s.schedule}</td>
                <td className="num">{fmtDate(s.last_date)}</td>
                <td>
                  {s.stale ? (
                    <span className="pill bad">{s.stale_reason ?? "지연"}</span>
                  ) : s.pending ? (
                    <span className="pill warn">{s.stale_reason ?? "수집 예정"}</span>
                  ) : (
                    <span className="pill ok">정상</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="dash-h3">최근 배치 실행</h3>
      <div className="scroll">
        <table className="dash-table">
          <thead>
            <tr>
              <th>배치</th>
              <th>시작</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td>{r.job_name}</td>
                <td className="num muted">{fmtWhen(r.started_at)}</td>
                <td>
                  <span
                    className={
                      r.status === "failed" ? "pill bad" : r.status === "success" ? "pill ok" : "pill"
                    }
                  >
                    {r.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
