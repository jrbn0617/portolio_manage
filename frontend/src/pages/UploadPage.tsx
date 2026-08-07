import { useState } from "react";
import { uploadFile } from "../api/data";
import type { UploadDataType, UploadResult } from "../types";

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

export default function UploadPage() {
  const [dataType, setDataType] = useState<UploadDataType>("prices");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

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
    </div>
  );
}
