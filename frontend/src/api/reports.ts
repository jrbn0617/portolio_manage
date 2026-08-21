import { apiClient } from "./client";
import type { Report } from "../types";

export async function fetchReports(): Promise<Report[]> {
  const { data } = await apiClient.get<Report[]>("/reports");
  return data;
}

/** 리포트를 여는 절대 URL. 앱이 아니라 백엔드가 서빙하므로 baseURL 을 붙여 새 탭으로 연다. */
export function reportUrl(key: string): string {
  return `${apiClient.defaults.baseURL ?? ""}/reports/${key}`;
}
