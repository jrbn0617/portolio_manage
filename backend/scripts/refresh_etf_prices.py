"""ETF 일별 시세를 KRX(pykrx)에서 증분 갱신한다 (cron 배치).

**거래일 1일당 KRX 호출 1회**로 전 ETF 시세가 끝난다 —
`stock.get_etf_ohlcv_by_ticker(날짜)` 가 그 날짜의 전 종목을 한 번에 준다.
종목별 반복 호출(`get_etf_ohlcv_by_date`)은 쓰지 않는다.

과거 원천은 로컬 MySQL `finance` DB에서 `load_etf_from_finance_db.py` 로 넣었고,
이 배치는 **그 이후 구간만** 이어서 채운다. 마지막 적재일 다음 거래일부터 확정 거래일까지
하루씩 호출한다.

**분배금은 이 배치 범위 밖이다.** pykrx에 ETF 분배금 API가 없다. 분배금은 SEIBRO 원천
(MySQL `etf_kr_seibro_dividend`)에서 `load_etf_from_finance_db.py` 로 들어온다. 따라서
이 배치가 채운 구간은 분배금이 반영되지 않은 상태이며, 분배금이 갱신되면 배당조정을
다시 계산해야 한다. 새로 들어온 분배금이 있으면 그 종목만 재계산하도록 되어 있다.

**우선주 규칙은 적용하지 않는다.** 끝자리 규칙은 보통주/우선주 판별용이라 ETF에는 무의미하다
(CLAUDE.md "우선주" 절).

**KRX 호출 간격 3초.** 짧으면 빈 결과를 정상처럼 돌려준다(CLAUDE.md "KRX 호출 간격" 절).
빈 결과는 재시도로 검증한다.

사용법:
  python scripts/refresh_etf_prices.py --dry-run
  python scripts/refresh_etf_prices.py            # cron
  python scripts/refresh_etf_prices.py --from 2026-07-17
"""
import argparse
import datetime
import io
import sys
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.market_holiday import MarketHoliday  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.models.raw_close import RawClose  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted, recompute_monthly_bar  # noqa: E402
from sqlalchemy import text  # noqa: E402

REQUEST_DELAY_SEC = 3
RETRIES = 3
MAX_DAYS = 60          # 한 번에 따라잡을 수 있는 최대 거래일 (그 이상이면 원천 재적재를 검토)
BATCH_SIZE = 5000
ETF_MARKET = "유가증권시장"
KST = ZoneInfo("Asia/Seoul")
MARKET_CLOSE_HOUR_KST = 16   # 장중 실행 시 당일 종가가 미확정이라 제외


def target_days(db, start: datetime.date | None) -> list[datetime.date]:
    """마지막 ETF 적재일 다음날부터 확정 거래일까지의 후보 날짜 (주말·기존 휴장일 제외)."""
    last = db.execute(text("""
        select max(p.date) from prices p join instruments i on i.id = p.instrument_id
        where i.asset_type = 'etf' and p.period = 'D'""")).scalar()
    begin = start or ((last + datetime.timedelta(days=1)) if last else None)
    if begin is None:
        raise SystemExit("ETF 시세가 하나도 없습니다 — load_etf_from_finance_db.py 를 먼저 실행하세요.")

    now = datetime.datetime.now(KST)
    end = now.date() if now.hour >= MARKET_CLOSE_HOUR_KST else now.date() - datetime.timedelta(days=1)
    holidays = {r[0] for r in db.query(MarketHoliday.date).all()}

    days, d = [], begin
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            days.append(d)
        d += datetime.timedelta(days=1)
    print(f"마지막 적재일 {last} · 확정 거래일 {end} · 후보 {len(days)}일")
    return days


def fetch(day: datetime.date):
    """그 날짜의 전 ETF OHLCV. 빈 결과는 rate limit일 수 있으므로 재시도한다."""
    for attempt in range(RETRIES):
        try:
            df = stock.get_etf_ohlcv_by_ticker(day.strftime("%Y%m%d"))
            if df is not None and len(df):
                return df
        except Exception as exc:  # noqa: BLE001
            if attempt == RETRIES - 1:
                print(f"  {day}: 조회 실패 — {exc}")
        time.sleep(REQUEST_DELAY_SEC)
    return None


def main(start: datetime.date | None, dry_run: bool) -> dict:
    db = SessionLocal()
    days = target_days(db, start)
    if not days:
        print("갱신할 거래일이 없습니다.")
        db.close()
        return {"days": 0, "rows": 0}
    if len(days) > MAX_DAYS:
        raise SystemExit(f"후보가 {len(days)}일로 너무 많습니다(>{MAX_DAYS}). "
                         f"--from 으로 범위를 나누거나 원천 재적재를 검토하세요.")
    if dry_run:
        print(f"KRX 호출 예정 {len(days)}회: {days[0]} ~ {days[-1]}")
        print("--dry-run 이므로 호출/적재하지 않고 종료합니다.")
        db.close()
        return {"days": len(days), "rows": 0}

    known = {t: i for t, i in db.query(Instrument.ticker, Instrument.id)
             .filter(Instrument.asset_type == "etf").all()}
    new_holidays, created, total_rows = [], 0, 0
    touched: set[int] = set()
    touched_months: set[tuple[int, int, int]] = set()

    for day in days:
        df = fetch(day)
        time.sleep(REQUEST_DELAY_SEC)
        if df is None or df.empty:
            # 재시도까지 빈 결과면 휴장일로 본다. market_holidays 에 남겨 다음 실행부터 건너뛴다.
            new_holidays.append(day)
            print(f"  {day}: 데이터 없음 -> 휴장일로 기록")
            continue

        price_rows, raw_rows = [], []
        for ticker, r in df.iterrows():
            close = float(r.get("종가") or 0)
            if close <= 0:
                continue
            iid = known.get(ticker)
            if iid is None:
                try:
                    name = stock.get_etf_ticker_name(ticker)
                except Exception:  # noqa: BLE001
                    name = ticker
                inst = Instrument(ticker=ticker, name=name or ticker,
                                  asset_type="etf", market=ETF_MARKET)
                db.add(inst)
                db.flush()
                iid = inst.id
                known[ticker] = iid
                created += 1
                print(f"  신규 ETF 등록: {ticker} {name}")

            shares = r.get("상장좌수")
            price_rows.append(dict(
                instrument_id=iid, date=day, period="D",
                open=float(r.get("시가") or 0) or None,
                high=float(r.get("고가") or 0) or None,
                low=float(r.get("저가") or 0) or None,
                close=close,
                volume=int(r.get("거래량") or 0) or None,
                market_cap=int(close * float(shares)) if shares else None,
                raw_close=close,
            ))
            raw_rows.append(dict(instrument_id=iid, date=day, close=close))
            touched.add(iid)
            touched_months.add((iid, day.year, day.month))

        for i in range(0, len(price_rows), BATCH_SIZE):
            stmt = pg_insert(Price).values(price_rows[i:i + BATCH_SIZE])
            db.execute(stmt.on_conflict_do_update(
                index_elements=["instrument_id", "date", "period"],
                set_={c: getattr(stmt.excluded, c) for c in
                      ("open", "high", "low", "close", "volume", "market_cap", "raw_close")}))
        for i in range(0, len(raw_rows), BATCH_SIZE):
            stmt = pg_insert(RawClose).values(raw_rows[i:i + BATCH_SIZE])
            db.execute(stmt.on_conflict_do_update(
                index_elements=["instrument_id", "date"], set_={"close": stmt.excluded.close}))
        db.commit()
        total_rows += len(price_rows)
        print(f"  {day}: {len(price_rows):,}종목")

    for h in new_holidays:
        db.add(MarketHoliday(date=h))
    if new_holidays:
        db.commit()

    # 파생 — 건드린 종목/월만
    for iid, y, m in sorted(touched_months):
        recompute_monthly_bar(db, iid, y, m)
    db.commit()
    failed = 0
    for iid in sorted(touched):
        try:
            recompute_dividend_adjusted(db, iid)      # 증분
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  배당조정 실패 instrument_id={iid}: {exc}")
            db.rollback()
    db.commit()

    summary = {"days": len(days), "krx_calls": len(days), "rows": total_rows,
               "new_etf": created, "holidays": len(new_holidays),
               "months": len(touched_months), "dividend_recompute_failed": failed}
    print(f"\n완료: {summary}")
    db.close()
    return summary


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def run(trigger: str = "manual", start=None, dry_run: bool = False) -> str:
    """BatchRun 이력과 함께 실행한다 (daily_update.run 과 동일한 패턴)."""
    from app.models.batch_run import BatchRun

    db = SessionLocal()
    batch = BatchRun(job_name="refresh_etf_prices", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf, real = io.StringIO(), sys.stdout
    sys.stdout = _Tee(real, buf)
    status = "running"
    try:
        batch.summary = str(main(start, dry_run))
        status = "success"
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        batch.error = f"{exc}\n{traceback.format_exc()}"
    finally:
        sys.stdout = real
        batch.status = status
        batch.log = buf.getvalue()
        batch.finished_at = datetime.datetime.now(datetime.timezone.utc)
        db.add(batch)
        db.commit()
        db.close()
    return status


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", type=datetime.date.fromisoformat, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--trigger", default="manual")
    a = p.parse_args()
    if run(trigger=a.trigger, start=a.start, dry_run=a.dry_run) == "failed":
        sys.exit(1)
