import { apiClient } from "./client";
import type { BatchRun, BatchSchedule } from "../types";

export async function fetchBatchSchedule(): Promise<BatchSchedule[]> {
  const { data } = await apiClient.get<BatchSchedule[]>("/batches/schedule");
  return data;
}

export async function fetchBatchRuns(limit = 50): Promise<BatchRun[]> {
  const { data } = await apiClient.get<BatchRun[]>("/batches/runs", { params: { limit } });
  return data;
}

export async function triggerBatch(jobName: string): Promise<void> {
  await apiClient.post(`/batches/${jobName}/run`);
}
