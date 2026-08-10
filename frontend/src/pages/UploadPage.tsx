import { useState } from "react";
import { uploadFile } from "../api/data";
import { bulkUploadMonthlyFundamentals } from "../api/monthlyFundamentals";
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
        <p style={{ color: "#666", fontSize: 13 }}>필수 컬럼: {selected.columns}</p>
      </div>

      <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      <button onClick={handleUpload} disabled={!file || uploading} style={{ marginLeft: 8 }}>
        {uploading ? "업로드 중..." : "업로드"}
      </button>

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {result && (
        <div style={{ marginTop: 16, border: "1px solid #ccc", padding: 12 }}>
          <p>
            상태: <strong>{result.status}</strong> / 성공 {result.row_count}건 / 오류{" "}
            {result.error_count}건
          </p>
          {result.errors.length > 0 && (
            <ul>
              {result.errors.map((e, idx) => (
                <li key={idx} style={{ color: "crimson" }}>
                  {e}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <hr style={{ margin: "32px 0" }} />

      <h2>월간 펀더멘털 일괄 업로드 (WISEfn 벌크)</h2>
      <p style={{ color: "#666", fontSize: 13 }}>
        유동비율/EBITDA(TTM)/EBITDA(Fwd.12M)/EV·EBITDA(Fwd.12M)가 한 파일에 여러 시트로 담긴 WISEfn/DataGuide
        벌크 양식(예: monthly_data_response_*.xlsx)을 그대로 업로드합니다. 시트명 접두어로 항목을 자동 인식하며,
        항목별로 없는 시트는 건너뜁니다.
      </p>

      <input
        type="file"
        accept=".xlsx,.xls"
        onChange={(e) => setBulkFile(e.target.files?.[0] ?? null)}
      />
      <button onClick={handleBulkUpload} disabled={!bulkFile || bulkUploading} style={{ marginLeft: 8 }}>
        {bulkUploading ? "업로드 중..." : "업로드"}
      </button>

      {bulkError && <p style={{ color: "crimson" }}>{bulkError}</p>}

      {bulkResult && (
        <div style={{ marginTop: 16, border: "1px solid #ccc", padding: 12 }}>
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
                <li key={idx} style={{ color: "crimson" }}>
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
