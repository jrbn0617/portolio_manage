import { apiClient } from "./client";
import type { MonthlyFundamental, MonthlyFundamentalInput } from "../types";

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
