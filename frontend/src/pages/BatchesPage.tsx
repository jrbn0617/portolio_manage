import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { fetchBatchRuns, fetchBatchSchedule, triggerBatch } from "../api/batches";
import type { BatchRun, BatchSchedule } from "../types";
import { SOURCE_COLOR, STATUS_COLOR, STATUS_LABEL } from "../lib/sources";

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

  // /batches/runs 는 started_at 내림차순이라 첫 매치가 최신 실행이다.
  function lastRunOf(jobName: string): BatchRun | undefined {
    return runs.find((r) => r.job_name === jobName);
  }

  return (
    <div>
      <h2>배치 관리</h2>

      <section style={{ marginBottom: 24 }}>
        <h3>스케줄</h3>
        <table border={1} cellPadding={6} style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>배치</th>
              <th style={{ textAlign: "left" }}>출처</th>
              <th style={{ textAlign: "left" }}>스케줄</th>
              <th style={{ textAlign: "left" }}>최근 실행</th>
              <th style={{ width: 96 }}></th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((s) => {
              const running = isJobRunning(s.job_name);
              const last = lastRunOf(s.job_name);
              return (
                <tr key={s.job_name}>
                  <td>
                    <strong>{s.job_name}</strong>
                    <p style={{ color: "var(--c-ink-4)", margin: "4px 0 0", fontSize: 12.5, lineHeight: 1.5 }}>
                      {s.description}
                    </p>
                  </td>
                  <td style={{ color: SOURCE_COLOR[s.source] ?? "var(--c-ink-3)", whiteSpace: "nowrap" }}>
                    {s.source}
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {s.schedule}
                    {s.cron && (
                      <>
                        <br />
                        <code style={{ color: "var(--c-muted)", fontSize: 11 }}>{s.cron}</code>
                      </>
                    )}
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {last ? (
                      <>
                        <span style={{ color: STATUS_COLOR[last.status], fontWeight: 600 }}>
                          {STATUS_LABEL[last.status] ?? last.status}
                        </span>
                        <br />
                        <span style={{ color: "var(--c-muted)", fontSize: 11 }}>
                          {new Date(last.started_at).toLocaleString("ko-KR")}
                        </span>
                      </>
                    ) : (
                      <span style={{ color: "var(--c-muted)" }}>이력 없음</span>
                    )}
                  </td>
                  <td style={{ textAlign: "center" }}>
                    {/* 수동 업로드로 들어오는 데이터는 돌릴 배치가 없다 — 버튼도 두지 않는다. */}
                    {s.runnable ? (
                      <button
                        onClick={() => handleTrigger(s.job_name)}
                        disabled={triggeringJob !== null || running}
                      >
                        {running ? "실행 중..." : triggeringJob === s.job_name ? "요청 중..." : "지금 실행"}
                      </button>
                    ) : (
                      <span style={{ color: "var(--c-muted)", fontSize: 12 }}>화면에서 업로드</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {error && <p style={{ color: "var(--c-bad-2)" }}>{error}</p>}
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
                      <td colSpan={7} style={{ background: "var(--c-card-2)" }}>
                        {r.error && (
                          <div style={{ marginBottom: 8 }}>
                            <strong style={{ color: "var(--c-bad-2)" }}>에러</strong>
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
                  <td colSpan={7} style={{ textAlign: "center", color: "var(--c-muted)" }}>
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
