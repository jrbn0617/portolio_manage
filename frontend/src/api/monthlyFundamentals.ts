import { apiClient } from "./client";
import type {
  MonthlyFundamental,
  MonthlyFundamentalBulkUploadResult,
  MonthlyFundamentalInput,
} from "../types";

export async function fetchMonthlyFundamentals(params: {
  ticker?: string;
  metric?: string;
}): Promise<MonthlyFundamental[]> {
  const { data } = await apiClient.get<MonthlyFundamental[]>("/monthly-fundamentals", { params });
  return data;
}

export async function fetchMonthlyFundamentalMetrics(): Promise<string[]> {
  const { data } = await apiClient.get<string[]>("/monthly-fundamentals/metrics");
  return data;
}

export async function upsertMonthlyFundamental(input: MonthlyFundamentalInput): Promise<MonthlyFundamental> {
  const { data } = await apiClient.post<MonthlyFundamental>("/monthly-fundamentals", input);
  return data;
}

export async function deleteMonthlyFundamental(id: number): Promise<void> {
  await apiClient.delete(`/monthly-fundamentals/${id}`);
}

export async function bulkUploadMonthlyFundamentals(file: File): Promise<MonthlyFundamentalBulkUploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await apiClient.post<MonthlyFundamentalBulkUploadResult>(
    "/monthly-fundamentals/bulk-upload",
    formData,
    { headers: { "Content-Type": "multipart/form-data" } }
  );
  return data;
}

/** DataGuide 요청 양식(xlsx)을 내려받는다. month는 'YYYY-MM'(미지정 시 당월). */
export async function downloadMonthlyFundamentalTemplate(
  month?: string,
  lookback?: number
): Promise<void> {
  const res = await apiClient.get("/monthly-fundamentals/template", {
    params: { month: month || undefined, lookback },
    responseType: "blob",
  });
  // 서버가 Content-Disposition으로 파일명을 준다 (CORS에서 노출 설정됨).
  const disposition = res.headers["content-disposition"] as string | undefined;
  const matched = disposition?.match(/filename="?([^";]+)"?/);
  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = matched?.[1] ?? "monthly_data_request.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
