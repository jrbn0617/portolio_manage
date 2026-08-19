import { useEffect, useState } from "react";
import { fetchDataSources } from "../api/dataSources";
import type { DataSource } from "../types";
import { SOURCE_COLOR } from "../lib/sources";

/** 상태 배지 — 지연이 먼저다. 수집 예정은 경고가 아니라 안내다. */
function statusOf(d: DataSource): { text: string; color: string; bg: string } | null {
  if (d.stale) return { text: d.stale_reason ?? "지연", color: "#b91c1c", bg: "#fee2e2" };
  if (d.pending) return { text: d.stale_reason ?? "수집 예정", color: "#92400e", bg: "#fef3c7" };
  // 휴장일 실행은 정상이다 — 새 데이터가 없는 게 맞으므로 경고로 칠하지 않는다.
  if (d.last_run?.status === "holiday") return { text: "직전 휴장일", color: "#57534e", bg: "#f5f5f4" };
  return null;
}

function fmt(iso: string | null) {
  return iso ? iso.slice(2).replace(/-/g, ".") : "—";
}

export default function DataSourcePanel() {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDataSources()
      .then(setSources)
      .catch(() => setError("입수 경로를 불러오지 못했습니다."));
  }, []);

  const stale = sources.filter((s) => s.stale);
  // 접힌 상태에서도 어디서 들어오는지는 보여야 한다 — 그게 이 패널의 목적이다.
  const origins = Array.from(new Set(sources.map((s) => s.source)));

  return (
    <section className="ds-panel">
      <button className="ds-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="ds-caret">{open ? "▾" : "▸"}</span>
        <b>데이터 입수 경로</b>
        {error ? (
          <span className="ds-err">{error}</span>
        ) : (
          <>
            <span className="ds-origins">
              {origins.map((o) => (
                <span key={o} style={{ color: SOURCE_COLOR[o] ?? "#374151" }}>
                  {o}
                </span>
              ))}
            </span>
            {stale.length > 0 && (
              <span className="ds-badge">지연 {stale.length}건</span>
            )}
          </>
        )}
      </button>

      {open && sources.length > 0 && (
        <div className="ds-body">
          <table className="ds-table">
            <thead>
              <tr>
                <th>데이터</th>
                <th>출처</th>
                <th>수집</th>
                <th>최신</th>
                <th>최근 실행</th>
                <th>상태</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((d) => {
                const st = statusOf(d);
                return (
                  <tr key={d.key} className={d.stale ? "ds-row-stale" : undefined}>
                    <td>
                      {d.label}
                      {d.note && <i className="ds-note" title={d.note}>ⓘ</i>}
                    </td>
                    <td style={{ color: SOURCE_COLOR[d.source] ?? "#374151", whiteSpace: "nowrap" }}>
                      {d.source}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>{d.schedule}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {fmt(d.last_date)}
                      <i className="ds-dim"> {d.date_label}</i>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {d.last_run ? (
                        <>
                          {fmt(d.last_run.started_at.slice(0, 10))}
                          <i
                            className={d.last_run.status === "failed" ? "ds-fail" : "ds-dim"}
                            title={d.last_run.error ?? undefined}
                          >
                            {" "}
                            {d.last_run.status}
                          </i>
                        </>
                      ) : (
                        <i className="ds-dim">이력 없음</i>
                      )}
                    </td>
                    <td>
                      {st && (
                        <span className="ds-tag" style={{ color: st.color, background: st.bg }}>
                          {st.text}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="ds-foot">
            과거 시계열 백필은 DataGuide 요청 양식으로만 받는다 — pykrx는 신규 데이터 자동수집 전용.
            ⓘ 표시에 마우스를 올리면 주의사항이 보인다.
          </p>
        </div>
      )}
    </section>
  );
}
