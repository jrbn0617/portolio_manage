"""KRX 업종분류(krx_sector)가 비어 있는 종목을 과거 스냅샷으로 채운다 (1회성).

daily_update.sync_krx_sector는 **오늘 상장된 종목만** 갱신하므로, 상장폐지·합병으로
사라진 종목은 영원히 krx_sector가 비어 있다. 지수 편입 이력을 2015년까지 소급하면서
과거에만 존재했던 종목이 대거 들어왔고(2015년 구 삼성물산·현대증권 등), 알고리즘 #1은
"섹터당 최대 2종목", 알고리즘 #2는 "케이산업당 2종목"이 확정안이라 분류가 없으면
그 종목들이 전부 '미분류' 한 덩어리로 묶여 제약이 왜곡된다.

과거 스냅샷을 **최신 → 과거 순서로** 훑으면서 아직 비어 있는 종목만 채운다(최신 분류를
우선). KRX는 2015년까지 조회가 되는 것을 실측으로 확인했다.

**KRX 호출 간격 주의**: 간격이 짧으면 조회가 실패하거나 **빈 결과를 정상처럼 돌려준다.**
실제로 1초 간격에서 2015-12-31이 0종목, 2016~2018이 ValueError로 나왔다가 3초로 늘리자
전부 정상(884~896종목) 조회됐다. 같은 함정을 코스닥150 편입종목 조회에서도 겪었으므로
(2015-06-30이 150종목으로 잘못 나옴) **빈 결과는 재시도로 검증한다.**

사용법:
  python scripts/backfill_krx_sector.py --dry-run
  python scripts/backfill_krx_sector.py
"""
import argparse
import datetime
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402

REQUEST_DELAY_SEC = 3  # 짧으면 빈 결과/ValueError가 나온다 (위 docstring 참고)
RETRIES = 3


def snapshot_dates(db, start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """최신 → 과거 순 반기 스냅샷. **실제 거래일로 스냅한다.**

    6/30·12/30을 그대로 쓰면 주말에 걸린 해(2017-12-30 토, 2018-12-30 일, 2019-06-30 일)에
    KRX가 빈 결과를 돌려줘 그 스냅샷이 통째로 날아간다. prices(D)에 있는 실제 거래일 중
    각 기준일 이하 최신 날짜를 쓴다.
    """
    out = []
    for year in range(end.year, start.year - 1, -1):
        for month, day in ((12, 31), (6, 30)):
            anchor = datetime.date(year, month, day)
            if not (start <= anchor <= end):
                continue
            d = db.execute(
                text("select max(date) from prices where period='D' and date <= :a"), {"a": anchor}
            ).scalar()
            if d and d not in out:
                out.append(d)
    return out


def fetch_sectors(day: datetime.date, market: str):
    """{ticker: 업종명}. 빈 결과는 rate limit일 수 있으므로 재시도한다."""
    for _ in range(RETRIES):
        try:
            df = stock.get_market_sector_classifications(day.strftime("%Y%m%d"), market)
            if df is not None and len(df):
                return {str(t): str(r["업종명"]) for t, r in df.iterrows()}
        except Exception:  # noqa: BLE001
            pass
        time.sleep(REQUEST_DELAY_SEC)
    return {}


def main(start: datetime.date, end: datetime.date, dry_run: bool):
    db = SessionLocal()
    missing = {
        r.ticker: r.id
        for r in db.execute(
            text(
                """select id, ticker from instruments
                   where coalesce(asset_type,'') <> 'index' and krx_sector is null"""
            )
        ).fetchall()
    }
    print(f"krx_sector 비어 있는 종목: {len(missing)}개")
    if not missing:
        return

    filled: dict[str, str] = {}
    for day in snapshot_dates(db, start, end):
        remaining = {t for t in missing if t not in filled}
        if not remaining:
            break
        for market in ("KOSPI", "KOSDAQ"):
            sectors = fetch_sectors(day, market)
            time.sleep(REQUEST_DELAY_SEC)
            if not sectors:
                print(f"  {day} {market}: 조회 실패/빈 결과 — 건너뜀")
                continue
            hit = {t: s for t, s in sectors.items() if t in remaining and t not in filled}
            filled.update(hit)
            print(f"  {day} {market}: {len(sectors)}종목 조회, 신규 채움 {len(hit)}개 (누적 {len(filled)})")

    print(f"\n채울 수 있는 종목: {len(filled)} / {len(missing)}")
    if dry_run:
        print("--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return

    for ticker, sector in filled.items():
        db.query(Instrument).filter(Instrument.id == missing[ticker]).update({"krx_sector": sector})
    db.commit()
    left = db.execute(
        text("select count(*) from instruments where coalesce(asset_type,'')<>'index' and krx_sector is null")
    ).scalar()
    print(f"갱신 완료. 남은 미분류: {left}개")
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", type=datetime.date.fromisoformat, default=datetime.date(2015, 6, 30))
    p.add_argument("--to", dest="end", type=datetime.date.fromisoformat, default=datetime.date(2026, 6, 30))
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    main(a.start, a.end, a.dry_run)
