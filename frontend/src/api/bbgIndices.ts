import { apiClient } from "./client";
import type { BbgIndex, BbgIndexCreate, BbgIndexUpdate } from "../types";

export async function fetchBbgIndices(): Promise<BbgIndex[]> {
  const { data } = await apiClient.get<BbgIndex[]>("/bbg-indices");
  return data;
}

export async function updateBbgIndex(ticker: string, patch: BbgIndexUpdate): Promise<BbgIndex> {
  const { data } = await apiClient.patch<BbgIndex>(`/bbg-indices/${ticker}`, patch);
  return data;
}

export async function createBbgIndex(payload: BbgIndexCreate): Promise<BbgIndex> {
  const { data } = await apiClient.post<BbgIndex>("/bbg-indices", payload);
  return data;
}

export async function deleteBbgIndex(ticker: string): Promise<void> {
  await apiClient.delete(`/bbg-indices/${ticker}`);
}
