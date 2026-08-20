"""월간 매크로 지표 수집 — FRED, FINRA.

`macro_indicators(indicator_name, date, value)` 에 적재한다. 스키마에 단위 칸이 없으므로
**단위는 원천 그대로 두고 여기 표로 남긴다** — 임의로 환산하면 원천과 대조가 안 된다.

| indicator_name      | 내용                              | 단위        | 원천  |
|---------------------|-----------------------------------|-------------|-------|
| `DGS10`             | 미국 10년 국채금리 **월평균**      | 퍼센트      | FRED  |
| `M2NS`              | 미국 M2 통화량 (계절조정 없음)     | 십억 달러   | FRED  |
| `FINRA_MARGIN_DEBT` | 고객 증거금계좌 차변잔고(마진부채) | 백만 달러   | FINRA |

**날짜는 전부 그 달의 마지막 날로 통일한다.** FRED 월간 시계열은 관측월 1일로 오고
(2026-07-01) FINRA 는 'YYYY-MM' 문자열로 온다. 섞어 두면 지수 데이터(월말 기준)와
한 달씩 어긋나 보인다.

세 계열 다 400행 미만이라 **매번 전 구간을 다시 받아 덮어쓴다.** M2 는 사후 개정이 있어
증분으로 이어붙이면 과거가 틀어진 채 남는다.
"""
import calendar
import datetime
import io
import os

import pandas as pd
import requests

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FINRA_PAGE = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
FINRA_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
FINRA_DEBIT_COL = "Debit Balances in Customers' Securities Margin Accounts"

# FINRA 는 헤드리스 브라우저를 막는다 (_download_finra_xlsx 주석 참고).
TIMEOUT = 60


def month_end(d: datetime.date) -> datetime.date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def fetch_fred(series_id: str, start: str = "1990-01-01", **params) -> list[tuple]:
    """FRED 월간 관측치 → [(월말 date, value)].

    결측은 '.' 으로 오고(휴장·미발표) 그대로 두면 float 변환에서 터진다 — 걸러낸다.
    """
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY 환경변수가 없습니다 (backend/.env 확인)")
    q = dict(series_id=series_id, api_key=key, file_type="json",
             observation_start=start, **params)
    r = requests.get(FRED_URL, params=q, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for o in r.json()["observations"]:
        if o["value"] in (".", "", None):
            continue
        d = datetime.date.fromisoformat(o["date"])
        out.append((month_end(d), float(o["value"])))
    return out


def _download_finra_xlsx() -> bytes:
    """FINRA 마진 통계 xlsx 를 받아 온다.

    **헤드리스로는 못 받는다 — 반드시 headless=False 다.** requests 로도, 헤드리스
    크로미엄으로도 403 이 온다. 브라우저에서는 같은 URL 이 정상으로 열리고, 실측으로
    아래처럼 갈렸다:

        bundled chromium · headless    page=403  xlsx=403
        bundled chromium · headed      page=200  xlsx=200  20,426B
        real Chrome      · headed      page=200  xlsx=200  20,426B

    UA·Referer·세션 쿠키를 맞춰도 헤드리스면 막히므로 WAF 가 헤드리스 자체를 보고
    있는 것으로 판단했다. 처음 두어 번은 requests 로도 받아졌는데 곧 막힌 걸 보면
    반복 호출에 대해 켜지는 판정으로 보인다.

    **화면 세션이 없는 환경(SSH 등)에서는 실패한다.** 창이 잠깐 뜬다 — 월 1회라 그대로 둔다.
    파일 URL 에 2021-03 이 박혀 있지만 FINRA 가 같은 파일을 계속 갱신한다(2026-07 확인).
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            # **user_agent 를 덮어쓰지 않는다.** UA 문자열만 바꾸면 Chrome 이 함께 보내는
            # 클라이언트 힌트(Sec-CH-UA)와 어긋나고, 그 불일치 자체가 탐지 신호가 된다.
            # 실측 — UA 를 지정하자 headed 인데도 403 이 났고 빼니 200 이 됐다.
            ctx = browser.new_context()
            ctx.new_page().goto(FINRA_PAGE, wait_until="domcontentloaded",
                                timeout=TIMEOUT * 1000)
            resp = ctx.request.get(FINRA_XLSX, headers={"Referer": FINRA_PAGE},
                                   timeout=TIMEOUT * 1000)
            if not resp.ok:
                raise RuntimeError(f"FINRA 다운로드 실패 {resp.status} — WAF 차단일 수 있습니다.")
            return resp.body()
        finally:
            browser.close()


def fetch_finra_margin_debt() -> list[tuple]:
    """FINRA 마진 통계 xlsx → [(월말 date, 차변잔고)]. 1997-01 이후."""
    df = pd.read_excel(io.BytesIO(_download_finra_xlsx()), sheet_name="Customer Margin Balances")
    if FINRA_DEBIT_COL not in df.columns:
        raise RuntimeError(
            f"FINRA 파일에 '{FINRA_DEBIT_COL}' 열이 없습니다 — 양식이 바뀐 것으로 보입니다."
            f" 현재 열: {list(df.columns)}")

    out = []
    for ym, v in zip(df["Year-Month"], df[FINRA_DEBIT_COL]):
        if pd.isna(ym) or pd.isna(v):
            continue
        # 'YYYY-MM' 문자열로 오지만 엑셀이 날짜로 해석해 두는 경우가 있어 양쪽을 받는다
        if isinstance(ym, str):
            y, m = ym.strip().split("-")[:2]
            d = datetime.date(int(y), int(m), 1)
        else:
            d = pd.Timestamp(ym).date().replace(day=1)
        out.append((month_end(d), float(v)))
    return sorted(out)


# 배치가 도는 순서대로. (indicator_name, 설명, 수집 함수)
COLLECTORS = [
    ("DGS10", "미국 10년 국채금리 월평균 (%)",
     lambda: fetch_fred("DGS10", frequency="m", aggregation_method="avg")),
    ("M2NS", "미국 M2 통화량, 계절조정 없음 (십억 달러)",
     lambda: fetch_fred("M2NS")),
    ("FINRA_MARGIN_DEBT", "고객 증거금계좌 차변잔고 (백만 달러)",
     fetch_finra_margin_debt),
]
