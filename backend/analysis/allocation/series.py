"""자산배분 트랙 공용 — 지수 시계열 로딩과 캘린더 정렬. 읽기 전용.

**캘린더를 한 곳에서 다룬다.** 한국·미국·금이 서로 다른 날 쉬기 때문에 스크립트마다
따로 맞추면 반드시 어긋난다. 실측: 2026-08-17(한국 대체휴일)에 LEGATRUU·XAU 등 8종이
값을 갖고 국내 지수만 없다. 그대로 일별로 합치면 국내 자산이 그날 0% 수익을 낸 것처럼
보여 변동성이 과소추정된다.

정렬 방식 세 가지와 언제 쓰는지:

  monthly()    각 계열의 **그 달 마지막 관측치**. 시장마다 월말 거래일이 달라도 각자의
               마지막 값을 쓰므로 캘린더 문제가 사라진다. 월 단위 리밸런싱이 기본인
               자산배분에서는 이게 표준이고, 특별한 이유가 없으면 이걸 쓴다.
  intersect()  모든 계열에 값이 있는 날만 남긴다. 일별 상관·변동성을 볼 때 쓴다.
               날짜가 줄어드는 대신 가짜 0% 수익이 안 생긴다.
  ffill()      합집합 + 직전 값 이월. **변동성·상관을 과소추정한다** — 성과 측정에는
               쓰지 않는다. 차트를 끊기지 않게 그릴 때만.

값은 `prices.close` 를 쓴다. 코스피 계열만 배당포인트로 총수익을 직접 계산해 넣은
값이고 나머지는 이미 TR/NTR/현물이라 PX_LAST 그대로다 (scripts/refresh_benchmark_indices_bbg.py).
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.session import SessionLocal  # noqa: E402

# ── 로딩 ────────────────────────────────────────────────────────────────────
def load_series(db, tickers: list[str], start: date | None = None,
                end: date | None = None) -> pd.DataFrame:
    """일별 지수 레벨 (index=date, columns=ticker). 값이 없는 날은 NaN 으로 둔다.

    비워 두는 게 요점이다 — 메우는 시점은 호출부가 정한다. 여기서 미리 채우면
    "그 시장이 쉰 날"과 "값이 진짜 없는 날"이 구분되지 않는다.
    """
    sql = """
        SELECT i.ticker, p.date, p.close
        FROM prices p JOIN instruments i ON i.id = p.instrument_id
        WHERE i.asset_type = 'index' AND p.period = 'D' AND i.ticker = ANY(:t)
    """
    params: dict = {"t": list(tickers)}
    if start:
        sql += " AND p.date >= :s"
        params["s"] = start
    if end:
        sql += " AND p.date <= :e"
        params["e"] = end

    rows = db.execute(text(sql), params).all()
    if not rows:
        raise RuntimeError(f"시세가 없습니다: {', '.join(tickers)}")
    df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
    df["close"] = df["close"].astype(float)
    out = df.pivot(index="date", columns="ticker", values="close").sort_index()
    out.index = pd.to_datetime(out.index)

    missing = [t for t in tickers if t not in out.columns]
    if missing:
        raise RuntimeError(f"등록은 됐지만 시세가 없는 티커: {', '.join(missing)}")
    return out[list(tickers)]


# ── 정렬 ────────────────────────────────────────────────────────────────────
def monthly(df: pd.DataFrame) -> pd.DataFrame:
    """각 계열의 **그 달 마지막 관측치**. 자산배분의 기본 형태다.

    계열마다 월말 거래일이 다를 수 있다(한국 12-30 / 미국 12-31). 각자의 마지막 값을
    쓰는 게 맞다 — 억지로 같은 날짜에 맞추면 하루씩 어긋난 값을 비교하게 된다.
    """
    return df.resample("ME").last().dropna(how="all")


def intersect(df: pd.DataFrame) -> pd.DataFrame:
    """모든 계열에 값이 있는 날만. 일별 상관·변동성을 볼 때 쓴다."""
    return df.dropna(how="any")


def ffill(df: pd.DataFrame) -> pd.DataFrame:
    """합집합 + 직전 값 이월. **성과·변동성 측정에는 쓰지 않는다** (0% 수익일이 생긴다)."""
    return df.ffill().dropna(how="any")


def coverage(db, tickers: list[str]) -> pd.DataFrame:
    """티커별 시작·끝·행수. 조합을 정하기 전에 어디서 잘리는지 보는 용도."""
    rows = db.execute(text("""
        SELECT i.ticker, MIN(p.date), MAX(p.date), count(*)
        FROM prices p JOIN instruments i ON i.id = p.instrument_id
        WHERE i.asset_type = 'index' AND p.period = 'D' AND i.ticker = ANY(:t)
        GROUP BY i.ticker ORDER BY MIN(p.date)"""), {"t": list(tickers)}).all()
    return pd.DataFrame(rows, columns=["ticker", "from", "to", "rows"])


if __name__ == "__main__":
    # 인자로 준 티커들의 가용 구간과 정렬 결과를 보여준다 — 조합을 고를 때 쓴다.
    tickers = sys.argv[1:] or ["SPTR500N", "NDDUEAFE", "NDUEEGF", "LEGATRUU",
                               "LT09TRUU", "LD20TRUU", "XAU"]
    db = SessionLocal()
    try:
        print(coverage(db, tickers).to_string(index=False))
        raw = load_series(db, tickers)
        m, i = monthly(raw), intersect(raw)
        print(f"\n일별 원본   {len(raw):>6,}행  {raw.index[0].date()} ~ {raw.index[-1].date()}")
        print(f"교집합      {len(i):>6,}행  {i.index[0].date()} ~ {i.index[-1].date()}")
        print(f"월말        {len(m):>6,}행  {m.index[0].date()} ~ {m.index[-1].date()}")
        full = m.dropna(how="any")
        print(f"월말(전종목) {len(full):>6,}행  {full.index[0].date()} ~ {full.index[-1].date()}")
    finally:
        db.close()
