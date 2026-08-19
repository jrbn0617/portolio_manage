import { apiClient } from "./client";
import type { DataSource } from "../types";

export async function fetchDataSources(): Promise<DataSource[]> {
  const { data } = await apiClient.get<DataSource[]>("/data-sources");
  return data;
}
