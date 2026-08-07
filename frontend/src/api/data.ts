import { apiClient } from "./client";
import type {
  Dividend,
  DividendAdjustedPrice,
  MacroIndicator,
  Price,
  UploadDataType,
  UploadResult,
} from "../types";

export async function fetchPrices(ticker: string, period: "D" | "M" = "D"): Promise<Price[]> {
  const { data } = await apiClient.get<Price[]>("/prices", { params: { ticker, period } });
  return data;
}

export async function fetchDividendAdjustedPrices(
  ticker: string,
  period: "D" | "M" = "D"
): Promise<DividendAdjustedPrice[]> {
  const { data } = await apiClient.get<DividendAdjustedPrice[]>("/dividend-adjusted-prices", {
    params: { ticker, period },
  });
  return data;
}

export async function fetchDividends(ticker: string): Promise<Dividend[]> {
  const { data } = await apiClient.get<Dividend[]>("/dividends", { params: { ticker } });
  return data;
}

export async function fetchMacroIndicator(indicatorName: string): Promise<MacroIndicator[]> {
  const { data } = await apiClient.get<MacroIndicator[]>("/macro", {
    params: { indicator_name: indicatorName },
  });
  return data;
}

export async function uploadFile(dataType: UploadDataType, file: File): Promise<UploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await apiClient.post<UploadResult>(`/uploads/${dataType}`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}
