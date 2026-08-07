"""관심종목(가격 데이터가 있는 종목)의 상장주식수를 pykrx로 일괄 점검해서,
직전에 기록해둔 값과 다르면(액면분할/무상증자/유상증자/소각 등 권리락 이벤트로 추정)
해당 종목의 가격을 pykrx에서 전체 재수집하고 파생 데이터(월봉/배당조정 수정주가)도
다시 계산한다.

- 상장주식수 조회는 KOSPI/KOSDAQ 전종목을 한 번씩만 호출한다 (get_market_cap).
- 재수집은 실제로 값이 바뀐 종목에 대해서만 개별 호출한다.
- 사전 준비: backend/.env 에 KRX_ID, KRX_PW 설정 필요.

사용법: python scripts/check_shares_outstanding.py
"""
import datetime
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted, recompute_monthly_bar  # noqa: E402

REQUEST_DELAY_SEC = 1
MAX_LOOKBACK_DAYS = 7


def _recent_trading_day() -> datetime.date:
    day = datetime.date.today()
    for _ in range(MAX_LOOKBACK_DAYS):
        df = stock.get_market_cap(day.strftime("%Y%m%d"), market="KOSPI", alternative=False)
        if not df.empty and (df["시가총액"] != 0).any():
            return day
        day -= datetime.timedelta(days=1)
    raise RuntimeError("최근 거래일을 찾지 못했습니다 (KRX_ID/KRX_PW 설정을 확인하세요).")


def _refetch_prices(db, instrument_id: int, ticker: str, start: datetime.date, end: datetime.date) -> bool:
    df = stock.get_market_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker, adjusted=True)
    if df is None or df.empty:
        return False

    rows = []
    touched_months = set()
    for idx, r in df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        rows.append(
            dict(
                instrument_id=instrument_id,
                date=d,
                period="D",
                open=float(r["시가"]) if r["시가"] else None,
                high=float(r["고가"]) if r["고가"] else None,
                low=float(r["저가"]) if r["저가"] else None,
                close=float(r["종가"]),
                volume=int(r["거래량"]) if r["거래량"] else None,
            )
        )
        touched_months.add((d.year, d.month))

    for i in range(0, len(rows), 1000):
        batch = rows[i : i + 1000]
        stmt = pg_insert(Price).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "period"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        db.execute(stmt)
    db.commit()

    for year, month in touched_months:
        recompute_monthly_bar(db, instrument_id, year, month)
    db.commit()

    # 가격 히스토리 자체가 통째로 재수집됐으므로 배당조정 지수도 처음부터 다시 계산한다.
    recompute_dividend_adjusted(db, instrument_id, force_full=True)
    db.commit()
    return True


def main():
    db = SessionLocal()
    today = _recent_trading_day()
    print(f"기준 거래일: {today}")

    cap_by_ticker: dict[str, int] = {}
    for market in ("KOSPI", "KOSDAQ"):
        df = stock.get_market_cap(today.strftime("%Y%m%d"), market=market, alternative=True)
        for ticker, row in df.iterrows():
            cap_by_ticker[ticker] = int(row["상장주식수"])
        print(f"{market}: {len(df)}개 종목 상장주식수 조회")
        time.sleep(REQUEST_DELAY_SEC)

    # 관심종목: 가격 데이터가 이미 있는 종목만 대상으로 한다.
    instruments = (
        db.query(Instrument)
        .filter(Instrument.id.in_(db.query(Price.instrument_id).distinct()))
        .all()
    )
    print(f"점검 대상 종목: {len(instruments)}개")

    changed, baseline, unchanged, needs_review = 0, 0, 0, []

    for inst in instruments:
        fetched = cap_by_ticker.get(inst.ticker)
        if fetched is None:
            needs_review.append((inst.ticker, inst.name, "pykrx 조회 결과에 없음 (상장폐지/합병 등 의심)"))
            continue

        if inst.shares_outstanding is None:
            inst.shares_outstanding = fetched
            baseline += 1
            continue

        if inst.shares_outstanding == fetched:
            unchanged += 1
            continue

        print(f"변경 감지: {inst.ticker} {inst.name} {inst.shares_outstanding} -> {fetched}")
        price_start = (
            db.query(Price.date)
            .filter(Price.instrument_id == inst.id, Price.period == "D")
            .order_by(Price.date)
            .first()
        )
        start = price_start[0] if price_start else datetime.date(2018, 12, 31)

        ok = False
        try:
            ok = _refetch_prices(db, inst.id, inst.ticker, start, today)
        except Exception as exc:  # noqa: BLE001
            print(f"  재수집 실패: {exc}")

        if ok:
            inst.shares_outstanding = fetched
            changed += 1
        else:
            needs_review.append((inst.ticker, inst.name, "재수집 실패 - 다음 점검에서 재시도"))

        time.sleep(REQUEST_DELAY_SEC)

    db.commit()

    print("\n=== 요약 ===")
    print(f"기준값 최초 설정: {baseline}")
    print(f"변동 없음: {unchanged}")
    print(f"변경 감지 및 재수집 완료: {changed}")
    print(f"확인 필요: {len(needs_review)}")
    for ticker, name, reason in needs_review:
        print(f"  - {ticker} {name}: {reason}")


if __name__ == "__main__":
    main()
