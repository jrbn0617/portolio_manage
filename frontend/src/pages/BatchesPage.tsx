import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { fetchBatchRuns, fetchBatchSchedule, triggerBatch } from "../api/batches";
import type { BatchRun, BatchSchedule } from "../types";

const STATUS_LABEL: Record<string, string> = { running: "실행 중", success: "성공", failed: "실패" };
const STATUS_COLOR: Record<string, string> = { running: "#1976d2", success: "#2e7d32", failed: "#c62828" };

function formatDuration(startedAt: string, finishedAt: string | null): string {
  const start = new Date(startedAt).getTime();
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}초`;
  return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
}

export default function BatchesPage() {
  const [schedules, setSchedules] = useState<BatchSchedule[]>([]);
  const [runs, setRuns] = useState<BatchRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [triggeringJob, setTriggeringJob] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadRuns = useCallback(async () => {
    setRuns(await fetchBatchRuns());
  }, []);

  async function loadAll() {
    setLoading(true);
    try {
      const [s, r] = await Promise.all([fetchBatchSchedule(), fetchBatchRuns()]);
      setSchedules(s);
      setRuns(r);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 실행 중인 배치가 있으면 완료될 때까지 짧은 주기로 폴링한다.
  useEffect(() => {
    const hasRunning = runs.some((r) => r.status === "running");
    if (hasRunning && !pollRef.current) {
      pollRef.current = setInterval(loadRuns, 2000);
    } else if (!hasRunning && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [runs, loadRuns]);

  async function handleTrigger(jobName: string) {
    setError(null);
    setTriggeringJob(jobName);
    try {
      await triggerBatch(jobName);
      await loadRuns();
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "실행 요청에 실패했습니다.");
    } finally {
      setTriggeringJob(null);
    }
  }

  function isJobRunning(jobName: string): boolean {
    return runs.some((r) => r.job_name === jobName && r.status === "running");
  }

  return (
    <div>
      <h2>배치 관리</h2>

      <section style={{ marginBottom: 24 }}>
        <h3>스케줄</h3>
        {schedules.map((s) => {
          const running = isJobRunning(s.job_name);
          return (
            <div
              key={s.job_name}
              style={{ border: "1px solid #ddd", borderRadius: 6, padding: 12, marginBottom: 8 }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>{s.job_name}</strong>
                <button onClick={() => handleTrigger(s.job_name)} disabled={triggeringJob !== null || running}>
                  {running ? "실행 중..." : triggeringJob === s.job_name ? "요청 중..." : "지금 실행"}
                </button>
              </div>
              <p style={{ color: "#555", margin: "6px 0" }}>{s.description}</p>
              <p style={{ color: "#888", margin: 0, fontSize: 13 }}>
                cron: <code>{s.cron}</code> ({s.timezone})
              </p>
            </div>
          );
        })}
        {error && <p style={{ color: "crimson" }}>{error}</p>}
      </section>

      <section>
        <h3>실행 이력</h3>
        {loading ? (
          <p>불러오는 중...</p>
        ) : (
          <table border={1} cellPadding={6} style={{ borderCollapse: "collapse", width: "100%" }}>
            <thead>
              <tr>
                <th>id</th>
                <th>job</th>
                <th>trigger</th>
                <th>상태</th>
                <th>시작</th>
                <th>소요</th>
                <th>요약</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <Fragment key={r.id}>
                  <tr
                    style={{ cursor: "pointer" }}
                    onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}
                  >
                    <td>{r.id}</td>
                    <td>{r.job_name}</td>
                    <td>{r.trigger === "cron" ? "자동(cron)" : "수동"}</td>
                    <td style={{ color: STATUS_COLOR[r.status], fontWeight: 600 }}>
                      {STATUS_LABEL[r.status] ?? r.status}
                    </td>
                    <td>{new Date(r.started_at).toLocaleString("ko-KR")}</td>
                    <td>{formatDuration(r.started_at, r.finished_at)}</td>
                    <td>{r.error ? r.error.split("\n")[0] : r.summary ?? "-"}</td>
                  </tr>
                  {expandedId === r.id && (
                    <tr>
                      <td colSpan={7} style={{ background: "#fafafa" }}>
                        {r.error && (
                          <div style={{ marginBottom: 8 }}>
                            <strong style={{ color: "#c62828" }}>에러</strong>
                            <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{r.error}</pre>
                          </div>
                        )}
                        <strong>로그</strong>
                        <pre
                          style={{
                            whiteSpace: "pre-wrap",
                            fontSize: 12,
                            maxHeight: 400,
                            overflowY: "auto",
                            background: "#1e1e1e",
                            color: "#ddd",
                            padding: 8,
                            borderRadius: 4,
                          }}
                        >
                          {r.log || "(로그 없음)"}
                        </pre>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {runs.length === 0 && (
                <tr>
                  <td colSpan={7} style={{ textAlign: "center", color: "#888" }}>
                    실행 이력이 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
