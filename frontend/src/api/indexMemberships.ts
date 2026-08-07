import { apiClient } from "./client";
import type { IndexConstituent } from "../types";

export async function fetchIndexNames(): Promise<string[]> {
  const { data } = await apiClient.get<string[]>("/index-memberships/index-names");
  return data;
}

export async function fetchSnapshots(indexName: string): Promise<string[]> {
  const { data } = await apiClient.get<string[]>("/index-memberships/snapshots", {
    params: { index_name: indexName },
  });
  return data;
}

export async function fetchConstituents(indexName: string, asOfDate: string): Promise<IndexConstituent[]> {
  const { data } = await apiClient.get<IndexConstituent[]>("/index-memberships/constituents", {
    params: { index_name: indexName, as_of_date: asOfDate },
  });
  return data;
}
