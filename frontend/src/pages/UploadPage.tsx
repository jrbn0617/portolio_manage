import { useState } from "react";
import { uploadFile } from "../api/data";
import {
  bulkUploadMonthlyFundamentals,
  downloadMonthlyFundamentalTemplate,
} from "../api/monthlyFundamentals";
import type { MonthlyFundamentalBulkUploadResult, UploadDataType, UploadResult } from "../types";

const DATA_TYPES: { value: UploadDataType; label: string; columns: string }[] = [
  {
    value: "prices",
    label: "가격 (OHLCV)",
    columns:
      "ticker(종목코드), date(일자), period(D/M), open(시가), high(고가), low(저가), close(종가), volume(거래량) — name(종목명)/market(시장구분)은 선택, 미등록 종목은 자동 등록됨",
  },
  {
    value: "dividends",
    label: "배당",
    columns:
      "ticker(종목코드), ex_date(배정기준일), pay_date(현금배당 지급일), amount(주당배당금) — name(종목명)/market(시장구분)은 선택, 미등록 종목은 자동 등록됨",
  },
  {
    value: "macro",
    label: "거시지표 (환율/금리 등)",
    columns: "indicator_name(지표명), date(일자), value(값)",
  },
  {
    value: "index_memberships",
    label: "지수/시장 편입 (KOSPI/KOSDAQ, KOSPI200/KOSDAQ150 등)",
    columns:
      "ticker(종목코드), index_name(지수명/구분: KOSPI, KOSDAQ, KOSPI200, KOSDAQ150 등), as_of_date(기준일) — name(종목명)은 선택, 미등록 종목은 자동 등록됨",
  },
];

const METRIC_LABELS: Record<string, string> = {
  free_float_ratio: "유동비율",
  ebitda_ttm: "EBITDA(TTM)",
  ebitda_fwd_12m: "EBITDA(Fwd.12M)",
  ev_ebitda_fwd_12m: "EV/EBITDA(Fwd.12M)",
};

export default function UploadPage() {
  const [dataType, setDataType] = useState<UploadDataType>("prices");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const [bulkFile, setBulkFile] = useState<File | null>(null);
  const [bulkResult, setBulkResult] = useState<MonthlyFundamentalBulkUploadResult | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [bulkUploading, setBulkUploading] = useState(false);
  const [templateMonth, setTemplateMonth] = useState("");
  const [templateBusy, setTemplateBusy] = useState(false);

  const selected = DATA_TYPES.find((d) => d.value === dataType)!;

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    setError(null);
    setResult(null);
    try {
      const res = await uploadFile(dataType, file);
      setResult(res);
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "업로드에 실패했습니다.");
    } finally {
      setUploading(false);
    }
  }

  async function handleDownloadTemplate() {
    setTemplateBusy(true);
    setBulkError(null);
    try {
      await downloadMonthlyFundamentalTemplate(templateMonth);
    } catch {
      // 실패 응답도 blob으로 오므로 detail을 꺼내려면 별도 파싱이 필요하다 — 여기선 간단히.
      setBulkError("양식 생성에 실패했습니다. 기간에 해당하는 거래일이 DB에 있는지 확인하세요.");
    } finally {
      setTemplateBusy(false);
    }
  }

  async function handleBulkUpload() {
    if (!bulkFile) return;
    setBulkUploading(true);
    setBulkError(null);
    setBulkResult(null);
    try {
      const res = await bulkUploadMonthlyFundamentals(bulkFile);
      setBulkResult(res);
    } catch (err: any) {
      setBulkError(err.response?.data?.detail ?? "업로드에 실패했습니다.");
    } finally {
      setBulkUploading(false);
    }
  }

  return (
    <div>
      <h2>데이터 업로드</h2>

      <div style={{ marginBottom: 12 }}>
        <label>
          데이터 종류:{" "}
          <select value={dataType} onChange={(e) => setDataType(e.target.value as UploadDataType)}>
            {DATA_TYPES.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <p style={{ color: "var(--c-ink-4)", fontSize: 13 }}>필수 컬럼: {selected.columns}</p>
      </div>

      <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      <button onClick={handleUpload} disabled={!file || uploading} style={{ marginLeft: 8 }}>
        {uploading ? "업로드 중..." : "업로드"}
      </button>

      {error && <p style={{ color: "var(--c-bad-2)" }}>{error}</p>}

      {result && (
        <div style={{ marginTop: 16, border: "1px solid var(--c-rule-3)", padding: 12 }}>
          <p>
            상태: <strong>{result.status}</strong> / 성공 {result.row_count}건 / 오류{" "}
            {result.error_count}건
          </p>
          {result.errors.length > 0 && (
            <ul>
              {result.errors.map((e, idx) => (
                <li key={idx} style={{ color: "var(--c-bad-2)" }}>
                  {e}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <hr style={{ margin: "32px 0" }} />

      <h2>월간 펀더멘털 일괄 업로드 (WISEfn 벌크)</h2>
      <p style={{ color: "var(--c-ink-4)", fontSize: 13 }}>
        유동비율/EBITDA(TTM)/EBITDA(Fwd.12M)/EV·EBITDA(Fwd.12M)가 한 파일에 여러 시트로 담긴 WISEfn/DataGuide
        벌크 양식(예: monthly_data_response_*.xlsx)을 그대로 업로드합니다. 시트명 접두어로 항목을 자동 인식하며,
        항목별로 없는 시트는 건너뜁니다.
      </p>

      <div
        style={{
          border: "1px solid var(--c-rule)",
          borderRadius: 6,
          padding: 12,
          margin: "12px 0 16px",
          background: "var(--c-card-2)",
        }}
      >
        <strong style={{ fontSize: 13 }}>1. 요청 양식 받기</strong>
        <p style={{ color: "var(--c-ink-4)", fontSize: 12.5, margin: "6px 0 10px", lineHeight: 1.6 }}>
          현재 상장 보통주 전체와 월말 거래일이 채워진 빈 양식을 내려받아 DataGuide에 요청하고,
          값이 채워져 돌아온 파일을 아래에 그대로 올리면 됩니다. 시트 구성이 업로드 파서와 맞춰져 있습니다.
        </p>
        <label style={{ fontSize: 12.5, color: "var(--c-ink-3)" }}>
          기준월{" "}
          <input
            type="month"
            value={templateMonth}
            onChange={(e) => setTemplateMonth(e.target.value)}
            style={{ marginRight: 8 }}
          />
        </label>
        <button onClick={handleDownloadTemplate} disabled={templateBusy}>
          {templateBusy ? "생성 중..." : "양식 다운로드"}
        </button>
        <span style={{ color: "var(--c-muted)", fontSize: 11.5, marginLeft: 8 }}>
          미지정 시 당월 기준 최근 6개월
        </span>
      </div>

      <strong style={{ fontSize: 13 }}>2. 응답 파일 업로드</strong>
      <div style={{ marginTop: 8 }}>
        <input
          type="file"
          accept=".xlsx,.xls"
          onChange={(e) => setBulkFile(e.target.files?.[0] ?? null)}
        />
        <button onClick={handleBulkUpload} disabled={!bulkFile || bulkUploading} style={{ marginLeft: 8 }}>
          {bulkUploading ? "업로드 중..." : "업로드"}
        </button>
      </div>

      {bulkError && <p style={{ color: "var(--c-bad-2)" }}>{bulkError}</p>}

      {bulkResult && (
        <div style={{ marginTop: 16, border: "1px solid var(--c-rule-3)", padding: 12 }}>
          <p>
            상태: <strong>{bulkResult.status}</strong> / 총 {bulkResult.total_rows}행 적재
          </p>
          {bulkResult.metrics.length > 0 && (
            <table border={1} cellPadding={6} style={{ borderCollapse: "collapse", marginTop: 8 }}>
              <thead>
                <tr>
                  <th>항목</th>
                  <th>시트 수</th>
                  <th>적재 행수</th>
                  <th>미등록 티커</th>
                </tr>
              </thead>
              <tbody>
                {bulkResult.metrics.map((m) => (
                  <tr key={m.metric}>
                    <td>{METRIC_LABELS[m.metric] ?? m.metric}</td>
                    <td style={{ textAlign: "right" }}>{m.sheets}</td>
                    <td style={{ textAlign: "right" }}>{m.rows.toLocaleString()}</td>
                    <td style={{ textAlign: "right" }}>{m.unknown_tickers}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {bulkResult.errors.length > 0 && (
            <ul>
              {bulkResult.errors.map((e, idx) => (
                <li key={idx} style={{ color: "var(--c-bad-2)" }}>
                  {e}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
