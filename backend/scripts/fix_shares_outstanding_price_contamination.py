"""`shares_outstanding_monthly` 자리에 **주가**가 들어간 행을 pykrx로 교정한다.

2026-08-13에 발견. 24종목 × 2019-12-30 ~ 2026-05, 1,152행이 상장주식수가 아니라 그 시점
주가를 담고 있다 (메리츠화재 18,100 vs 그날 종가 17,850, 쌍용양회 6,233 vs 6,211).
2019-12-30분 26행은 DataGuide 재요청분 적재로 교정됐고 2020년 이후가 남았다.

**왜 문제인가.** `compute_free_float_weights`가 `raw_close x 상장주식수 x 유동주식비율`로
시가총액을 잡는다. 주식수가 4자리수 배로 작으면 그 종목은 사실상 0 비중이 되고, 알고리즘 #1의
"비중 1% 미만 제외" 규칙에 걸려 **조용히 편입에서 빠진다.** 24종목 전부 지수 편입 이력이 있다.

**왜 pykrx인가.** 과거 시계열 백필은 원칙적으로 DataGuide로 받지만(CLAUDE.md), 상장주식수는
KRX 공개데이터라 원래 pykrx가 정규 수집원이다(`load_shares_outstanding_pykrx.py` 월간 cron).
같은 원천으로 같은 항목을 메우는 것이라 규칙과 충돌하지 않는다.

**호출량은 종목당 1회.** `get_market_cap_by_date(from, to, ticker)`가 한 번에 그 종목의
전 구간 일별 상장주식수를 준다. 날짜별 벌크(`get_market_cap_by_ticker`)를 쓰면 78회가
필요한데 그럴 이유가 없다. 간격 3초, 빈 응답은 재시도로 확인한다(CLAUDE.md "KRX 호출 간격").

기본은 조회만 하고 보여준다. 실제 반영은 `--apply`.

사용법:
  python scripts/fix_shares_outstanding_price_contamination.py            # 조회 + 비교표
  python scripts/fix_shares_outstanding_price_contamination.py --apply
"""
import argparse
import datetime
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.monthly_fundamental import MonthlyFundamental  # noqa: E402

METRIC = "shares_outstanding_monthly"
# 상장 종목의 상장주식수가 이보다 작을 수는 없다 — 이 값 미만이면 주가가 들어간 것으로 본다.
CONTAMINATION_THRESHOLD = 100_000
REQUEST_DELAY_SEC = 3
RETRIES = 3
NEAREST_DAYS = 7   # 그 날짜에 값이 없으면(휴장·거래정지) 며칠 전까지 되짚을지


def affected_rows(db) -> pd.DataFrame:
    return pd.read_sql(text("""
        select i.ticker, i.name, f.instrument_id, f.date, f.value::float bad,
               p.close::float px
        from monthly_fundamentals f
        join instruments i on i.id = f.instrument_id
        left join prices p on p.instrument_id = f.instrument_id
                          and p.period = 'D' and p.date = f.date
        where f.metric = :m and f.value < :t
        order by i.ticker, f.date"""),
        db.bind, params={"m": METRIC, "t": CONTAMINATION_THRESHOLD})


def fetch_shares(ticker: str, start: datetime.date, end: datetime.date):
    """그 종목의 일별 상장주식수 {date: shares}. 빈 응답은 재시도로 검증한다."""
    f, t = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    for attempt in range(RETRIES):
        try:
            df = stock.get_market_cap_by_date(f, t, ticker)
            if df is not None and len(df) and "상장주식수" in df.columns:
                s = df["상장주식수"]
                return {d.date(): float(v) for d, v in s.items() if v and v > 0}
        except Exception as exc:  # noqa: BLE001
            if attempt == RETRIES - 1:
                print(f"    조회 실패: {exc}")
        time.sleep(REQUEST_DELAY_SEC)
    return {}


def pick(series: dict, d: datetime.date):
    """그 날짜 값, 없으면 최대 NEAREST_DAYS 이전까지 되짚어 가장 가까운 값."""
    for back in range(NEAREST_DAYS + 1):
        v = series.get(d - datetime.timedelta(days=back))
        if v:
            return v, back
    return None, None


def main(apply: bool):
    db = SessionLocal()
    bad = affected_rows(db)
    if bad.empty:
        print("오염된 행이 없습니다.")
        db.close()
        return

    tickers = sorted(bad["ticker"].unique())
    print(f"오염 행 {len(bad):,}건 · {len(tickers)}종목")
    print(f"KRX 호출 {len(tickers)}회 (종목당 1회, 간격 {REQUEST_DELAY_SEC}초) — "
          f"예상 소요 {len(tickers) * REQUEST_DELAY_SEC // 60}분 내외\n")

    fixes, missing = [], []
    for n, tk in enumerate(tickers, 1):
        g = bad[bad["ticker"] == tk]
        lo, hi = g["date"].min(), g["date"].max()
        series = fetch_shares(tk, lo, hi + datetime.timedelta(days=5))
        time.sleep(REQUEST_DELAY_SEC)
        got = 0
        for r in g.itertuples():
            v, back = pick(series, r.date)
            if v is None:
                missing.append(dict(ticker=tk, name=r.name, date=r.date, bad=r.bad))
                continue
            fixes.append(dict(instrument_id=r.instrument_id, ticker=tk, name=r.name,
                              date=r.date, bad=r.bad, px=r.px, new=v, back=back))
            got += 1
        print(f"  [{n:>2}/{len(tickers)}] {tk} {r.name:<14s} 응답 {len(series):>5}일 · "
              f"교정 {got}/{len(g)}건")

    fx = pd.DataFrame(fixes)
    print("\n" + "=" * 92)
    if fx.empty:
        print("교정 가능한 행이 없습니다 (전부 pykrx 응답 없음).")
        db.close()
        return

    # 진단 확인 — 기존 값이 정말 그날 주가였나
    chk = fx.dropna(subset=["px"])
    close_to_px = (chk["bad"] - chk["px"]).abs() / chk["px"] < 0.05
    print(f"진단 확인: 기존 값이 그날 종가의 ±5% 이내인 행 "
          f"{int(close_to_px.sum()):,}/{len(chk):,}건  → 주가 오염이 맞다")
    print(f"교정 대상 {len(fx):,}건 · 교정 불가 {len(missing):,}건")
    print(f"새 값이 임계치({CONTAMINATION_THRESHOLD:,}) 미만인 건 "
          f"{int((fx['new'] < CONTAMINATION_THRESHOLD).sum()):,}건 (0이어야 정상)")

    pd.set_option("display.width", 220)
    smp = fx.groupby("ticker").head(1).head(12)
    print("\n종목별 첫 행 샘플")
    print(smp[["ticker", "name", "date", "bad", "px", "new"]].to_string(index=False))

    if missing:
        md = pd.DataFrame(missing)
        print(f"\n교정 불가 {len(md):,}건 (상폐 등으로 pykrx 응답 없음) — 종목별")
        print(md.groupby(["ticker", "name"]).size().to_string())

    if not apply:
        print("\n--apply 없이 실행되어 DB는 건드리지 않았습니다.")
        db.close()
        return

    rows = [dict(instrument_id=int(r.instrument_id), date=r.date, metric=METRIC, value=float(r.new))
            for r in fx.itertuples()]
    for i in range(0, len(rows), 5000):
        stmt = pg_insert(MonthlyFundamental).values(rows[i:i + 5000])
        db.execute(stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "metric"], set_={"value": stmt.excluded.value}))
        db.commit()
    left = db.execute(text("select count(*) from monthly_fundamentals where metric=:m and value<:t"),
                      {"m": METRIC, "t": CONTAMINATION_THRESHOLD}).scalar()
    print(f"\n{len(rows):,}행 교정 완료. 남은 오염 행 {left:,}건")
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="실제로 DB에 반영")
    main(p.parse_args().apply)
