"""KRX(pykrx)에서 최신 거래일분을 받아 prices(+시가총액)/raw_closes/상장주식수/휴장일/
지수편입/KRX 업종분류/투자자별 매매동향/공매도 거래량·거래대금을 증분 갱신하고,
파생 데이터(월봉·배당조정 지수)를 다시 계산한다.

시장 단위 벌크 호출(get_market_ohlcv(날짜, market=) 등)을 쓰므로 하루 20콜 안팎이면
충분하다 (종목별 반복 호출 아님).

배당(dividends)은 pykrx에 배정기준일 API가 없어 이 스크립트 범위 밖이다 —
기존대로 외부 파일 업로드 또는 SEIBRO 스크래핑(load_dividends_seibro.py) 경로를 쓴다.
공매도 "잔고"도 pykrx에 전종목 벌크 API가 없어(Top50만 있고 그마저 현재 깨져 있음)
수집 대상에서 제외했다 — 거래량/거래대금만 수집한다.

사전 준비: backend/.env 에 KRX_ID, KRX_PW 설정 필요.

사용법: python scripts/daily_update.py
"""
import datetime
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402  (KRX_ID/KRX_PW가 os.environ에 있어야 하므로 load_dotenv 이후)

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.index_membership import IndexMembership  # noqa: E402,F401
from app.models.instrument import Instrument  # noqa: E402
from app.models.investor_trading import InvestorTrading  # noqa: E402
from app.models.market_holiday import MarketHoliday  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.models.raw_close import RawClose  # noqa: E402
from app.models.short_selling import ShortSelling  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted, recompute_monthly_bar  # noqa: E402
from app.services.index_market_cap_service import fetch_and_store_index_market_caps  # noqa: E402
from app.services.upload_service import _upsert_index_membership  # noqa: E402

REQUEST_DELAY_SEC = 1
MAX_LOOKBACK_DAYS = 7
KOSPI_INDEX_CODE = "1001"  # 코스피 종합지수 — 거래일 캘린더 확인용
BATCH_SIZE = 5000

MARKET_LABELS = {"KOSPI": "유가증권시장", "KOSDAQ": "코스닥시장"}
INVESTOR_TYPES = ("기관합계", "외국인", "개인")

KST = ZoneInfo("Asia/Seoul")
# KRX 정규장은 15:30에 마감된다. 장중에 실행하면 pykrx가 "현재가"를 종가 자리에
# 넣어주므로, 마감 후 시세가 확정되기까지 여유를 두고 이 시각 이후에만 당일분을 적재한다.
MARKET_CLOSE_HOUR_KST = 16


def _latest_confirmed_day() -> datetime.date:
    """시세가 확정된 가장 최근 날짜. 장중/장 마감 직후에 실행하면 당일은 제외한다."""
    now = datetime.datetime.now(KST)
    day = now.date()
    if now.hour < MARKET_CLOSE_HOUR_KST:
        print(f"현재 {now:%H:%M} KST — 당일 종가 미확정이므로 {day} 제외하고 갱신합니다.")
        day -= datetime.timedelta(days=1)

    for _ in range(MAX_LOOKBACK_DAYS):
        df = stock.get_market_cap(day.strftime("%Y%m%d"), market="KOSPI", alternative=False)
        if not df.empty and (df["시가총액"] != 0).any():
            return day
        day -= datetime.timedelta(days=1)
    raise RuntimeError("최근 거래일을 찾지 못했습니다 (KRX_ID/KRX_PW 설정을 확인하세요).")


def sync_calendar(db, start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """구간의 실제 거래일 목록을 지수 시세로 확인하고(1콜), 그 사이 평일 중
    거래일이 아닌 날은 market_holidays에 추가한다. 거래일 목록을 반환한다."""
    df = stock.get_index_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), KOSPI_INDEX_CODE)
    trading_days = sorted({ts.date() for ts in df.index})

    weekdays = set()
    d = start
    while d <= end:
        if d.weekday() < 5:
            weekdays.add(d)
        d += datetime.timedelta(days=1)

    existing = {r[0] for r in db.query(MarketHoliday.date).all()}
    new_holidays = sorted(weekdays - set(trading_days) - existing)
    for h in new_holidays:
        db.add(MarketHoliday(date=h))
    if new_holidays:
        db.commit()
        print(f"휴장일 신규 {len(new_holidays)}건 추가: {new_holidays}")

    return trading_days


def fetch_day(db, day: datetime.date, instruments_by_ticker: dict[str, int]) -> tuple[set[int], set[tuple[int, int, int]], int]:
    """해당 거래일의 전 시장 OHLCV를 받아 prices/raw_closes에 upsert한다.
    (영향받은 instrument_id 집합, (instrument_id, year, month) 집합, 신규등록 종목수) 반환."""
    price_rows: list[dict] = []
    raw_rows: list[dict] = []
    touched: set[int] = set()
    touched_months: set[tuple[int, int, int]] = set()
    created = 0

    for market in ("KOSPI", "KOSDAQ"):
        df = stock.get_market_ohlcv(day.strftime("%Y%m%d"), market=market)
        if df is None or df.empty:
            print(f"  {day} {market}: 데이터 없음")
            continue

        for ticker, r in df.iterrows():
            close = float(r["종가"])
            if close == 0:
                continue  # 거래정지 등으로 시세가 없는 종목

            instrument_id = instruments_by_ticker.get(ticker)
            if instrument_id is None:
                # 신규 상장 — 건너뛰지 않고 자동 등록한다.
                try:
                    name = stock.get_market_ticker_name(ticker)
                except Exception:  # noqa: BLE001
                    name = ticker
                instrument = Instrument(
                    ticker=ticker,
                    name=name or ticker,
                    asset_type="stock",
                    market=MARKET_LABELS[market],
                )
                db.add(instrument)
                db.flush()
                instrument_id = instrument.id
                instruments_by_ticker[ticker] = instrument_id
                created += 1
                print(f"  신규 종목 등록: {ticker} {name}")

            price_rows.append(
                dict(
                    instrument_id=instrument_id,
                    date=day,
                    period="D",
                    open=float(r["시가"]) or None,
                    high=float(r["고가"]) or None,
                    low=float(r["저가"]) or None,
                    close=close,
                    volume=int(r["거래량"]) if r["거래량"] else None,
                    market_cap=int(r["시가총액"]) if r.get("시가총액") else None,
                )
            )
            raw_rows.append(dict(instrument_id=instrument_id, date=day, close=close))
            touched.add(instrument_id)
            touched_months.add((instrument_id, day.year, day.month))

        print(f"  {day} {market}: {len(df)}개 종목")
        time.sleep(REQUEST_DELAY_SEC)

    for i in range(0, len(price_rows), BATCH_SIZE):
        batch = price_rows[i : i + BATCH_SIZE]
        stmt = pg_insert(Price).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "period"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "market_cap": stmt.excluded.market_cap,
            },
        )
        db.execute(stmt)

    for i in range(0, len(raw_rows), BATCH_SIZE):
        batch = raw_rows[i : i + BATCH_SIZE]
        stmt = pg_insert(RawClose).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date"],
            set_={"close": stmt.excluded.close},
        )
        db.execute(stmt)

    db.commit()
    return touched, touched_months, created


def refetch_prices(db, instrument_id: int, ticker: str, start: datetime.date, end: datetime.date) -> bool:
    """권리락 이벤트가 감지된 종목의 prices 히스토리를 분할반영(adjusted=True)으로 전체 재수집한다.

    raw_closes는 '실제(미조정) 종가'를 보존해야 배당수익률 계산 기준이 유지되므로 건드리지 않는다.
    """
    df = stock.get_market_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker, adjusted=True)
    if df is None or df.empty:
        return False

    rows = []
    touched_months = set()
    for idx, r in df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        if float(r["종가"]) == 0:
            continue
        rows.append(
            dict(
                instrument_id=instrument_id,
                date=d,
                period="D",
                open=float(r["시가"]) or None,
                high=float(r["고가"]) or None,
                low=float(r["저가"]) or None,
                close=float(r["종가"]),
                volume=int(r["거래량"]) if r["거래량"] else None,
            )
        )
        touched_months.add((d.year, d.month))

    if not rows:
        return False

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
    return True


def sync_shares_outstanding(db, today: datetime.date, instruments_by_ticker: dict[str, int]) -> set[int]:
    """상장주식수를 점검해 값이 바뀐 종목(권리락 추정)의 가격을 전체 재수집한다.
    재수집된 instrument_id 집합을 반환한다."""
    cap_by_ticker: dict[str, int] = {}
    for market in ("KOSPI", "KOSDAQ"):
        df = stock.get_market_cap(today.strftime("%Y%m%d"), market=market, alternative=True)
        for ticker, row in df.iterrows():
            cap_by_ticker[ticker] = int(row["상장주식수"])
        time.sleep(REQUEST_DELAY_SEC)
    print(f"상장주식수 조회: {len(cap_by_ticker)}개 종목")

    instruments = (
        db.query(Instrument)
        .filter(Instrument.id.in_(db.query(Price.instrument_id).distinct()))
        .all()
    )

    refetched: set[int] = set()
    baseline = 0
    for inst in instruments:
        fetched = cap_by_ticker.get(inst.ticker)
        if fetched is None:
            continue  # 상장폐지/이전 등 — 이번 갱신 대상 아님
        if inst.shares_outstanding is None:
            inst.shares_outstanding = fetched
            baseline += 1
            continue
        if inst.shares_outstanding == fetched:
            continue

        print(f"권리락 감지: {inst.ticker} {inst.name} {inst.shares_outstanding} -> {fetched}")
        price_start = (
            db.query(func.min(Price.date))
            .filter(Price.instrument_id == inst.id, Price.period == "D")
            .scalar()
        )
        start = price_start or datetime.date(2018, 12, 31)
        try:
            if refetch_prices(db, inst.id, inst.ticker, start, today):
                inst.shares_outstanding = fetched
                refetched.add(inst.id)
            else:
                print(f"  재수집 실패 — 다음 실행에서 재시도: {inst.ticker}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"  재수집 오류: {inst.ticker} {exc}")
        time.sleep(REQUEST_DELAY_SEC)

    db.commit()
    if baseline:
        print(f"상장주식수 기준값 최초 설정: {baseline}개")
    return refetched


def sync_index_memberships(db, today: datetime.date, instruments_by_ticker: dict[str, int]) -> None:
    """KOSPI/KOSDAQ 시장구분은 매일, KOSPI200/KOSDAQ150은 반기 시점 데이터가 없을 때만 갱신한다."""
    for market in ("KOSPI", "KOSDAQ"):
        tickers = stock.get_market_ticker_list(today.strftime("%Y%m%d"), market=market)
        for ticker in tickers:
            if ticker not in instruments_by_ticker:
                continue
            with db.begin_nested():
                _upsert_index_membership(
                    db, {"ticker": ticker, "index_name": market, "as_of_date": today}, instruments_by_ticker
                )
        db.commit()
        print(f"{market} 시장구분 {len(tickers)}개 갱신")
        time.sleep(REQUEST_DELAY_SEC)

    # 반기 리밸런싱(6/30, 12/31) 시점 구성종목이 아직 없으면 채운다.
    for month, day in ((6, 30), (12, 31)):
        as_of = datetime.date(today.year, month, day)
        if as_of > today:
            continue
        for index_name, market, index_label in (
            ("KOSPI200", "KOSPI", "코스피 200"),
            ("KOSDAQ150", "KOSDAQ", "코스닥 150"),
        ):
            exists = (
                db.query(IndexMembership)
                .filter(IndexMembership.index_name == index_name, IndexMembership.as_of_date == as_of)
                .first()
            )
            if exists:
                continue
            code = next(
                (c for c in stock.get_index_ticker_list(market=market) if stock.get_index_ticker_name(c) == index_label),
                None,
            )
            if code is None:
                print(f"  {index_name} 지수코드를 찾지 못함 — 건너뜀")
                continue
            members = stock.get_index_portfolio_deposit_file(code, as_of.strftime("%Y%m%d"), alternative=True)
            for ticker in members:
                if ticker not in instruments_by_ticker:
                    continue
                with db.begin_nested():
                    _upsert_index_membership(
                        db, {"ticker": ticker, "index_name": index_name, "as_of_date": as_of}, instruments_by_ticker
                    )
            db.commit()
            print(f"{index_name} {as_of}: {len(members)}개 종목 적재")
            time.sleep(REQUEST_DELAY_SEC)

            if index_name == "KOSPI200":
                cap_count = fetch_and_store_index_market_caps(
                    db, stock, index_name, as_of, market, instruments_by_ticker
                )
                print(f"{index_name} {as_of}: 시가총액 {cap_count}건 적재")
                time.sleep(REQUEST_DELAY_SEC)


def sync_krx_sector(db, today: datetime.date, instruments_by_ticker: dict[str, int]) -> None:
    """KRX 자체 업종분류(TradingView 분류와 별개)를 종목마다 최신값으로 갱신한다."""
    for market in ("KOSPI", "KOSDAQ"):
        df = stock.get_market_sector_classifications(today.strftime("%Y%m%d"), market)
        if df is None or df.empty:
            continue
        for ticker, r in df.iterrows():
            instrument_id = instruments_by_ticker.get(ticker)
            if instrument_id is None:
                continue
            db.query(Instrument).filter(Instrument.id == instrument_id).update({"krx_sector": r["업종명"]})
        db.commit()
        print(f"{market} KRX 업종분류 {len(df)}개 갱신")
        time.sleep(REQUEST_DELAY_SEC)


def fetch_investor_trading(db, day: datetime.date, instruments_by_ticker: dict[str, int]) -> None:
    """투자자(기관합계/외국인/개인)별 일별 순매수 동향을 적재한다."""
    day_str = day.strftime("%Y%m%d")
    rows: list[dict] = []
    for market in ("KOSPI", "KOSDAQ"):
        for investor in INVESTOR_TYPES:
            df = stock.get_market_net_purchases_of_equities_by_ticker(day_str, day_str, market=market, investor=investor)
            if df is None or df.empty:
                continue
            for ticker, r in df.iterrows():
                instrument_id = instruments_by_ticker.get(ticker)
                if instrument_id is None:
                    continue
                rows.append(
                    dict(
                        instrument_id=instrument_id,
                        date=day,
                        investor_type=investor,
                        sell_volume=int(r["매도거래량"]),
                        buy_volume=int(r["매수거래량"]),
                        net_volume=int(r["순매수거래량"]),
                        sell_value=int(r["매도거래대금"]),
                        buy_value=int(r["매수거래대금"]),
                        net_value=int(r["순매수거래대금"]),
                    )
                )
            time.sleep(REQUEST_DELAY_SEC)

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        stmt = pg_insert(InvestorTrading).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "investor_type"],
            set_={
                "sell_volume": stmt.excluded.sell_volume,
                "buy_volume": stmt.excluded.buy_volume,
                "net_volume": stmt.excluded.net_volume,
                "sell_value": stmt.excluded.sell_value,
                "buy_value": stmt.excluded.buy_value,
                "net_value": stmt.excluded.net_value,
            },
        )
        db.execute(stmt)
    db.commit()
    print(f"  {day} 투자자별 매매동향 {len(rows)}건")


def fetch_short_selling(db, day: datetime.date, instruments_by_ticker: dict[str, int]) -> None:
    """일별 공매도 거래량/거래대금을 적재한다 (잔고는 벌크 API가 없어 제외)."""
    day_str = day.strftime("%Y%m%d")
    by_ticker: dict[str, dict] = {}
    for market in ("KOSPI", "KOSDAQ"):
        vol_df = stock.get_shorting_volume_by_ticker(day_str, market=market)
        time.sleep(REQUEST_DELAY_SEC)
        val_df = stock.get_shorting_value_by_ticker(day_str, market=market)
        time.sleep(REQUEST_DELAY_SEC)

        for ticker, r in vol_df.iterrows():
            by_ticker.setdefault(ticker, {})["short_volume"] = int(r["공매도"])
            by_ticker[ticker]["total_volume"] = int(r["매수"])
            by_ticker[ticker]["volume_ratio"] = float(r["비중"])
        for ticker, r in val_df.iterrows():
            by_ticker.setdefault(ticker, {})["short_value"] = int(r["공매도"])
            by_ticker[ticker]["total_value"] = int(r["매수"])
            by_ticker[ticker]["value_ratio"] = float(r["비중"])

    rows = []
    for ticker, fields in by_ticker.items():
        instrument_id = instruments_by_ticker.get(ticker)
        if instrument_id is None:
            continue
        rows.append(dict(instrument_id=instrument_id, date=day, **fields))

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        stmt = pg_insert(ShortSelling).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date"],
            set_={
                "short_volume": stmt.excluded.short_volume,
                "total_volume": stmt.excluded.total_volume,
                "volume_ratio": stmt.excluded.volume_ratio,
                "short_value": stmt.excluded.short_value,
                "total_value": stmt.excluded.total_value,
                "value_ratio": stmt.excluded.value_ratio,
            },
        )
        db.execute(stmt)
    db.commit()
    print(f"  {day} 공매도 거래량/거래대금 {len(rows)}건")


def main() -> dict:
    """일일 갱신을 실행하고 요약 dict를 반환한다 (BatchRun.summary로 저장됨)."""
    db = SessionLocal()

    last_date = db.query(func.max(Price.date)).filter(Price.period == "D").scalar()
    if last_date is None:
        raise RuntimeError("prices에 기존 데이터가 없습니다. 먼저 벌크 적재를 수행하세요.")

    today = _latest_confirmed_day()
    print(f"마지막 적재일: {last_date} / 시세 확정된 최근 거래일: {today}")

    # 캘린더는 last_date와 무관하게 최소 최근 10일은 확인한다 — 그래야 "이미
    # last_date == today"인 상태(같은 날 재실행 등)에서도 전영업일을 알아낼 수 있다.
    calendar_start = min(last_date, today - datetime.timedelta(days=10))
    trading_days = sync_calendar(db, calendar_start, today)

    # KRX는 당일 발표 이후에도 정정(오류 정정, 지연 신고된 대량매매 등)이 반영될 수
    # 있어, 신규 거래일뿐 아니라 그 직전 영업일도 매번 다시 받아 덮어쓴다.
    prev_trading_day = next((d for d in reversed(trading_days) if d < today), None)

    fetch_days = {d for d in trading_days if d > last_date}
    if prev_trading_day is not None:
        fetch_days.add(prev_trading_day)
    missing = sorted(fetch_days)

    if not missing:
        print("갱신할 거래일이 없습니다.")
        return {"missing_days": 0, "message": "갱신할 거래일 없음"}
    print(f"수집 대상 거래일 {len(missing)}건(전영업일 재확인 포함): {missing}")

    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    touched: set[int] = set()
    touched_months: set[tuple[int, int, int]] = set()
    created_total = 0
    for day in missing:
        t, tm, created = fetch_day(db, day, instruments_by_ticker)
        touched |= t
        touched_months |= tm
        created_total += created

    print(f"\n가격 적재 완료 — 영향 종목 {len(touched)}개, 신규 등록 {created_total}개")

    print("\n투자자별 매매동향 수집 중...")
    for day in missing:
        fetch_investor_trading(db, day, instruments_by_ticker)

    print("\n공매도 거래량/거래대금 수집 중...")
    for day in missing:
        fetch_short_selling(db, day, instruments_by_ticker)

    refetched = sync_shares_outstanding(db, today, instruments_by_ticker)

    print("\n월봉 재계산 중...")
    for instrument_id, year, month in touched_months:
        with db.begin_nested():
            recompute_monthly_bar(db, instrument_id, year, month)
    db.commit()

    print("배당조정 지수 재계산 중...")
    failed = []
    for idx, instrument_id in enumerate(sorted(touched | refetched), start=1):
        try:
            with db.begin_nested():
                recompute_dividend_adjusted(db, instrument_id, force_full=instrument_id in refetched)
        except Exception as exc:  # noqa: BLE001
            failed.append((instrument_id, str(exc)))
        if idx % 500 == 0:
            db.commit()
            print(f"  {idx}/{len(touched | refetched)}")
    db.commit()

    print("\n지수 편입 갱신 중...")
    sync_index_memberships(db, today, instruments_by_ticker)

    print("\nKRX 업종분류 갱신 중...")
    sync_krx_sector(db, today, instruments_by_ticker)

    print("\n=== 요약 ===")
    print(f"신규 적재 거래일: {len(missing)}건 ({missing[0]} ~ {missing[-1]})")
    print(f"신규 등록 종목: {created_total}개")
    print(f"권리락 감지·재수집: {len(refetched)}개")
    print(f"배당조정 지수 재계산 실패: {len(failed)}건")
    for instrument_id, msg in failed[:20]:
        print(f"  - instrument_id={instrument_id}: {msg}")

    return {
        "missing_days": len(missing),
        "date_range": [str(missing[0]), str(missing[-1])],
        "created_instruments": created_total,
        "corporate_actions_refetched": len(refetched),
        "dividend_recompute_failed": len(failed),
    }


class _Tee:
    """print() 출력을 실제 stdout에도 내보내면서 문자열로도 모아 BatchRun.log에 저장한다."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def run(trigger: str = "manual") -> str:
    """main()을 BatchRun 이력 기록과 함께 실행한다. cron/수동 트리거 모두 이 경로를 탄다.
    반환값은 최종 status 문자열 — DB 세션이 함수 종료 후 정리돼도 안전하게 쓸 수 있도록
    ORM 인스턴스가 아니라 값 자체를 반환한다."""
    import io
    import traceback

    from app.models.batch_run import BatchRun

    db = SessionLocal()
    batch = BatchRun(job_name="daily_update", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = _Tee(real_stdout, buf)
    status = "running"
    try:
        summary = main()
        status = "success"
        batch.status = status
        batch.summary = str(summary)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        batch.status = status
        batch.error = f"{exc}\n{traceback.format_exc()}"
    finally:
        sys.stdout = real_stdout
        batch.log = buf.getvalue()
        batch.finished_at = datetime.datetime.now(datetime.timezone.utc)
        db.add(batch)
        db.commit()
    return status


if __name__ == "__main__":
    trigger_arg = sys.argv[1] if len(sys.argv) > 1 else "cron"
    if run(trigger=trigger_arg) == "failed":
        sys.exit(1)
