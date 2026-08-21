import { useEffect, useState } from "react";
import { fetchReports, reportUrl } from "../api/reports";
import type { Report } from "../types";

/** 개발 단계. **문서가 원천이고 여기는 요약이다** — 상태가 바뀌면 문서를 먼저 고친다.
 *  근거 문서 경로를 같이 적어 두는 이유는, 화면 문구만 보고 판단하지 않게 하려는 것이다. */
type Stage = "채택" | "폐기 권고" | "이식 완료";

interface Algo {
  key: string;
  name: string;
  summary: string;
  stage: Stage;
  stageNote: string;
  doc: string;
  reports: string[]; // reports API 의 key
}

const ALGOS: Algo[] = [
  {
    key: "algorithm1",
    name: "알고리즘 #1",
    summary: "수익성 대비 밸류에이션 선별 + 중기 가격 흐름 순위화. 월 1회 리밸런싱, 손실 제한·레짐 필터.",
    stage: "채택",
    stageNote: "실험 1~19 및 표본 외 검증을 거쳐 파라미터 확정. 개발 종료.",
    doc: "docs/algorithms/algorithm1-overview.md",
    reports: ["mtd", "monthly", "walkforward", "proposal"],
  },
  {
    key: "algorithm2",
    name: "알고리즘 #2",
    summary: "기관 수급 필터 + 모멘텀. 인샘플 위험조정 성과는 우수했으나 표본 외에서 무너졌다.",
    stage: "폐기 권고",
    stageNote: "표본 외(2015-08~2019-12) CAGR −10.82% · 샤프 −0.690. 실자금 근거로 쓸 수 없다.",
    doc: "docs/algorithms/algorithm2-overview.md",
    reports: [],
  },
  {
    key: "allocation1",
    name: "배분 #1 — 경기 사이클 스위치",
    summary: "세 개의 on/off 스위치로 8국면을 만들고 국면별 비중표를 꺼내 쓰는 전술적 자산배분.",
    stage: "이식 완료",
    stageNote: "신호·비중 생성까지 이식. 원본 대비 107회 리밸런싱 중 106회 일치.",
    doc: "docs/allocation/allocation1-overview.md",
    reports: ["cycle_switch"],
  },
];

const STAGE_CLASS: Record<Stage, string> = {
  "채택": "ok",
  "폐기 권고": "bad",
  "이식 완료": "warn",
};

function fmtWhen(iso: string | null) {
  if (!iso) return "미생성";
  const d = new Date(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return sameDay ? `오늘 ${hm}` : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${hm}`;
}

function ReportButton({ r }: { r: Report }) {
  if (!r.exists) {
    return (
      <span className="rep-btn off" title={`아직 만들어지지 않았다. 생성: ${r.command}`}>
        <b>{r.label}</b>
        <em>미생성 — {r.command}</em>
      </span>
    );
  }
  return (
    <a className="rep-btn" href={reportUrl(r.key)} target="_blank" rel="noreferrer">
      <b>{r.label}</b>
      <em>{r.description}</em>
      <i>{fmtWhen(r.generated_at)} 생성 ↗</i>
    </a>
  );
}

export default function AlgoDashboardPage() {
  const [reports, setReports] = useState<Report[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchReports()
      .then(setReports)
      .catch(() => setError("리포트 목록을 불러오지 못했습니다. 백엔드가 떠 있는지 확인하세요."));
  }, []);

  const byKey = new Map(reports.map((r) => [r.key, r]));

  return (
    <div className="dash">
      <div className="dash-head">
        <h2>알고리즘</h2>
        <p>
          개발 단계와 성과 보고를 한자리에서 본다. 리포트는 분석 스크립트가 만들어 둔 파일을
          그대로 여는 것이라, 생성 시각을 같이 적어 둔다.
        </p>
      </div>

      {error && <p className="dash-err">{error}</p>}

      {ALGOS.map((a) => (
        <section key={a.key} className="algo">
          <header className="algo-head">
            <h3>{a.name}</h3>
            <span className={`pill ${STAGE_CLASS[a.stage]}`}>{a.stage}</span>
            <code>{a.doc}</code>
          </header>
          <p className="algo-sum">{a.summary}</p>
          <p className="algo-note">{a.stageNote}</p>

          {a.reports.length > 0 ? (
            <div className="rep-grid">
              {a.reports.map((k) => {
                const r = byKey.get(k);
                return r ? <ReportButton key={k} r={r} /> : null;
              })}
            </div>
          ) : (
            <p className="algo-none">보고 자료 없음 — 개발 문서만 있다.</p>
          )}
        </section>
      ))}
    </div>
  );
}
