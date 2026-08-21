/** 데이터 출처 색 — 입수 경로 패널과 배치 관리 화면이 같은 색을 쓰도록 한 곳에 둔다. */
export const SOURCE_COLOR: Record<string, string> = {
  "pykrx / KRX": "var(--c-accent)",
  SEIBRO: "var(--c-src-seibro)",
  "블룸버그 터미널": "var(--c-warn-2)",
  DataGuide: "var(--c-src-dg)",
  "수동 업로드": "var(--c-src-manual)",
};

export const STATUS_LABEL: Record<string, string> = {
  running: "실행 중",
  success: "성공",
  failed: "실패",
  holiday: "휴장일",
};

export const STATUS_COLOR: Record<string, string> = {
  running: "var(--c-running)",
  success: "var(--c-ok-2)",
  failed: "var(--c-bad-2)",
  holiday: "var(--c-holiday)",
};
