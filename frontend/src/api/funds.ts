import { apiClient } from "./client";
import type { Fund, FundDetail, FundKind, FundNavPoint, FundSettlement, FundStats } from "../types";

export async function fetchFundStats(): Promise<FundStats> {
  const { data } = await apiClient.get<FundStats>("/funds/stats");
  return data;
}

export async function fetchFundCategories(): Promise<string[]> {
  const { data } = await apiClient.get<string[]>("/funds/categories");
  return data;
}

export async function fetchFundCompanies(): Promise<string[]> {
  const { data } = await apiClient.get<string[]>("/funds/companies");
  return data;
}

export async function fetchFunds(params: {
  q?: string;
  category?: string;
  manage_company?: string;
  pension?: string;
  kind?: FundKind;
  limit?: number;
}): Promise<Fund[]> {
  const { data } = await apiClient.get<Fund[]>("/funds", { params });
  return data;
}

export async function fetchFundDetail(fundCode: string): Promise<FundDetail> {
  const { data } = await apiClient.get<FundDetail>(`/funds/${fundCode}`);
  return data;
}

export async function fetchFundNavs(fundCode: string, start?: string): Promise<FundNavPoint[]> {
  const { data } = await apiClient.get<FundNavPoint[]>(`/funds/${fundCode}/navs`, {
    params: { start },
  });
  return data;
}

export async function fetchFundSettlements(fundCode: string): Promise<FundSettlement[]> {
  const { data } = await apiClient.get<FundSettlement[]>(`/funds/${fundCode}/settlements`);
  return data;
}
