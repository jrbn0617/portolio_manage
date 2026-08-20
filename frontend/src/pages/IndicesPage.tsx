import { useEffect, useState } from "react";
import {
  createBbgIndex, deleteBbgIndex, fetchBbgIndices, updateBbgIndex,
} from "../api/bbgIndices";
import { triggerBatch } from "../api/batches";
import type { BbgIndex } from "../types";

// 요청 방식은 티커별로 다르다 — 배당포인트가 사후 정정되는 지수만 전 구간을 다시 받는다.
const MODE_LABEL: Record<string, string> = { daily: "당일치", full: "전 구간" };
const MODE_DESC: Record<string, string> = {
  daily: "마지막 적재일 다음날부터 오늘까지. 빠진 날이 있으면 다음 실행이 자동으로 메운다",
  full: "매번 전 구간을 다시 받아 통째로 교체. 배당포인트가 사후 정정되는 지수용",
};

const cell: React.CSSProperties = { padding: "7px 9px", verticalAlign: "top" };
const th: React.CSSProperties = { ...cell, textAlign: "left", fontWeight: 600, color: "#475569" };

function daysAgo(iso: string | null): number | null {
  if (!iso) return null;
  const d = new Date(iso + "T00:00:00");
  return Math.round((Date.now() - d.getTime()) / 86400000);
}

export default function IndicesPage() {
  const [rows, setRows] = useState<BbgIndex[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  // 편집 중인 셀 — 저장 전까지 서버 값과 분리해 둔다
  const [edit, setEdit] = useState<{ ticker: string; field: "name" | "note" } | null>(null);
  const [draft, setDraft] = useState("");

  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ bbg_ticker: "", ticker: "", name: "" });

  async function load() {
    setLoading(true);
    try {
      setRows(await fetchBbgIndices());
    } catch {
      setError("목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function patch(ticker: string, patchBody: Parameters<typeof updateBbgIndex>[1]) {
    setSaving(ticker);
    setError(null);
    try {
      const updated = await updateBbgIndex(ticker, patchBody);
      setRows((prev) => prev.map((r) => (r.ticker === ticker ? updated : r)));
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "저장에 실패했습니다.");
    } finally {
      setSaving(null);
    }
  }

  function startEdit(r: BbgIndex, field: "name" | "note") {
    setEdit({ ticker: r.ticker, field });
    setDraft((field === "name" ? r.name : r.note) ?? "");
  }

  async function commitEdit() {
    if (!edit) return;
    const row = rows.find((r) => r.ticker === edit.ticker);
    const current = (edit.field === "name" ? row?.name : row?.note) ?? "";
    const next = edit.field === "note" ? draft.trim() : draft.trim() || current;
    setEdit(null);
    if (next !== current) await patch(edit.ticker, { [edit.field]: next || null } as never);
  }

  async function add() {
    setError(null);
    try {
      await createBbgIndex({ ...form, refresh_mode: "daily" });
      setForm({ bbg_ticker: "", ticker: "", name: "" });
      setAdding(false);
      await load();
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "등록에 실패했습니다.");
    }
  }

  async function remove(r: BbgIndex) {
    if (!confirm(`${r.ticker} 를 수집 대상에서 빼시겠습니까?\n`
      + `이미 받아 둔 ${r.rows.toLocaleString()}행은 지우지 않습니다.`)) return;
    await deleteBbgIndex(r.ticker);
    await load();
  }

  async function runBatch() {
    setRunning(true);
    setError(null);
    try {
      await triggerBatch("benchmark_indices_bbg");
      setTimeout(load, 4000);
    } catch (err: any) {
      setError(err.response?.data?.detail ?? "실행 요청에 실패했습니다.");
    } finally {
      setRunning(false);
    }
  }

  const enabled = rows.filter((r) => r.enabled);
  const stale = enabled.filter((r) => {
    const d = daysAgo(r.last_dt);
    return d === null || d > 5;
  });

  return (
    <div>
      <h2>지수관리</h2>

      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13, color: "#374151",
                    background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 6,
                    padding: "10px 14px", marginBottom: 14, alignItems: "center" }}>
        <span>수집 대상 <b>{enabled.length}</b> / {rows.length}</span>
        <span>당일치 <b>{enabled.filter((r) => r.refresh_mode === "daily").length}</b></span>
        <span>전 구간 <b>{enabled.filter((r) => r.refresh_mode === "full").length}</b></span>
        {stale.length > 0 && (
          <span style={{ color: "#b45309" }}>최근 5일 내 데이터 없음 <b>{stale.length}</b></span>
        )}
        <span style={{ marginLeft: "auto", color: "#94a3b8" }}>
          블룸버그 터미널 SSH · 평일 18:30
        </span>
        <button onClick={runBatch} disabled={running}>
          {running ? "요청 중..." : "지금 수집"}
        </button>
      </div>

      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {loading ? <p>불러오는 중...</p> : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5,
                        border: "1px solid #e2e8f0" }}>
          <thead style={{ background: "#f8fafc" }}>
            <tr>
              <th style={{ ...th, width: 34 }} title="수집 대상에서 빼려면 체크 해제">수집</th>
              <th style={th}>블룸버그 티커</th>
              <th style={th}>설명 <span style={{ fontWeight: 400, color: "#94a3b8" }}>(클릭해 수정)</span></th>
              <th style={th}>요청 방식</th>
              <th style={{ ...th, textAlign: "right" }}>최근값</th>
              <th style={th}>적재 구간</th>
              <th style={{ ...th, width: 30 }}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const d = daysAgo(r.last_dt);
              const isStale = r.enabled && (d === null || d > 5);
              return (
                <tr key={r.ticker} style={{ borderTop: "1px solid #f1f5f9",
                                            opacity: r.enabled ? 1 : 0.5 }}>
                  <td style={{ ...cell, textAlign: "center" }}>
                    <input type="checkbox" checked={r.enabled}
                           disabled={saving === r.ticker}
                           onChange={(e) => patch(r.ticker, { enabled: e.target.checked })} />
                  </td>
                  <td style={cell}>
                    <div style={{ fontFamily: "ui-monospace, monospace" }}>{r.bbg_ticker}</div>
                    <div style={{ color: "#94a3b8", fontSize: 11, marginTop: 2 }}>
                      → {r.ticker}
                      {r.compute_tr && <span style={{ color: "#0f766e" }}> · TR 직접계산</span>}
                    </div>
                  </td>
                  <td style={{ ...cell, minWidth: 260 }}>
                    {edit?.ticker === r.ticker && edit.field === "name" ? (
                      <input autoFocus value={draft} style={{ width: "100%" }}
                             onChange={(e) => setDraft(e.target.value)}
                             onBlur={commitEdit}
                             onKeyDown={(e) => {
                               if (e.key === "Enter") commitEdit();
                               if (e.key === "Escape") setEdit(null);
                             }} />
                    ) : (
                      <div onClick={() => startEdit(r, "name")}
                           style={{ cursor: "text", fontWeight: 500 }}>{r.name}</div>
                    )}
                    {edit?.ticker === r.ticker && edit.field === "note" ? (
                      <input autoFocus value={draft} placeholder="메모"
                             style={{ width: "100%", marginTop: 3 }}
                             onChange={(e) => setDraft(e.target.value)}
                             onBlur={commitEdit}
                             onKeyDown={(e) => {
                               if (e.key === "Enter") commitEdit();
                               if (e.key === "Escape") setEdit(null);
                             }} />
                    ) : (
                      <div onClick={() => startEdit(r, "note")}
                           style={{ cursor: "text", fontSize: 11, marginTop: 2,
                                    color: r.note ? "#64748b" : "#cbd5e1" }}>
                        {r.note || "메모 추가"}
                      </div>
                    )}
                  </td>
                  <td style={cell}>
                    <select value={r.refresh_mode} disabled={r.compute_tr || saving === r.ticker}
                            title={r.compute_tr
                              ? "총수익을 직접 계산하는 지수는 전 구간으로만 받을 수 있습니다"
                              : MODE_DESC[r.refresh_mode]}
                            onChange={(e) => patch(r.ticker,
                              { refresh_mode: e.target.value as "daily" | "full" })}>
                      <option value="daily">{MODE_LABEL.daily}</option>
                      <option value="full">{MODE_LABEL.full}</option>
                    </select>
                  </td>
                  <td style={{ ...cell, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {r.last_value === null ? <span style={{ color: "#cbd5e1" }}>-</span>
                      : r.last_value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                  </td>
                  <td style={cell}>
                    {r.rows === 0 ? (
                      <span style={{ color: "#b45309" }}>없음</span>
                    ) : (
                      <>
                        <div style={{ color: isStale ? "#b45309" : "#334155" }}>
                          {r.first_dt} ~ <b>{r.last_dt}</b>
                          {d !== null && d > 5 && <> ({d}일 전)</>}
                        </div>
                        <div style={{ color: "#94a3b8", fontSize: 11 }}>
                          {r.rows.toLocaleString()}행
                        </div>
                      </>
                    )}
                  </td>
                  <td style={{ ...cell, textAlign: "center" }}>
                    <button onClick={() => remove(r)} title="수집 대상에서 제거 (가격은 유지)"
                            style={{ border: "none", background: "none", cursor: "pointer",
                                     color: "#cbd5e1" }}>✕</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div style={{ marginTop: 14 }}>
        {adding ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input placeholder="블룸버그 티커 (예: SPX Index)" style={{ width: 210 }}
                   value={form.bbg_ticker}
                   onChange={(e) => {
                     const v = e.target.value;
                     // 우리 쪽 코드는 접미어를 뗀 것을 기본값으로 — 직접 고칠 수 있다
                     setForm((f) => ({
                       ...f, bbg_ticker: v,
                       ticker: f.ticker || v.replace(/\s+(Index|Curncy|Equity|Comdty)$/i, ""),
                     }));
                   }} />
            <input placeholder="종목코드" style={{ width: 110 }} value={form.ticker}
                   onChange={(e) => setForm((f) => ({ ...f, ticker: e.target.value }))} />
            <input placeholder="설명" style={{ width: 300 }} value={form.name}
                   onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
            <button onClick={add}
                    disabled={!form.bbg_ticker.trim() || !form.ticker.trim() || !form.name.trim()}>
              등록
            </button>
            <button onClick={() => setAdding(false)}>취소</button>
            <span style={{ color: "#94a3b8", fontSize: 12 }}>
              당일치 · PX_LAST 로 등록됩니다
            </span>
          </div>
        ) : (
          <button onClick={() => setAdding(true)}>+ 지수 추가</button>
        )}
      </div>

      <p style={{ color: "#64748b", fontSize: 12, marginTop: 16, lineHeight: 1.7 }}>
        <b>당일치</b> — {MODE_DESC.daily}<br />
        <b>전 구간</b> — {MODE_DESC.full}<br />
        블룸버그 시계열은 구간을 넓혀도 요청 1회라, 당일치라고 해서 하루만 좁혀 부르지 않고
        빠진 날까지 함께 받아 온다. 가격은 <code>instruments(asset_type=index)</code> +
        <code> prices</code> 에 들어가 데이터 조회·백테스트에서 그대로 쓰인다.
      </p>
    </div>
  );
}
