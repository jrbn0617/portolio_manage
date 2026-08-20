"""국내 채권지수 과거 구간을 소스 DB(`underlying_index`)에서 옮긴다. 1회성.

kis-net.kr 이 주는 이력은 2015년부터다(30년 지수는 2016-03-10 산출 개시). 그 이전은
소스 DB 에만 있다 — 뒤로 이어붙인 합성 계열이라 KIS 공시에는 없다.

    KR10YTR  → KIS10Y    1998-12-31 ~
    KR30YTR  → KIS30Y    1998-12-31 ~   (실제 지수 개시는 2016-03-10, 그때 10,000)
    KRCD91TR → KISCD     1998-12-31 ~   (실제 지수 개시는 2015-01-02, 그때 10,000)

**백필 먼저, KIS 수집 나중** 순서다. 소스의 `nav` 가 MySQL `float` 라 유효숫자 7자리에서
잘린다(16,078.0000 vs 실제 16,077.9783). 전 구간을 넣은 뒤 일별 배치를 돌리면 2015년
이후가 제 정밀도로 덮인다. 순서를 뒤집지 말 것 — SOFR·KOFR 때와 같다.

정렬은 확인했다. 겹치는 2,481~2,774일에서 비율이 1.000000 이고, 30년·CD 지수가 개시일에
정확히 10,000 이다.

소스에는 총수익지수만 있어 `prices.raw_close`(순가격지수)는 KIS 구간부터 채워진다.

사용법:
  python scripts/backfill_kr_bond_indices.py --dry-run
  python scripts/backfill_kr_bond_indices.py
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.db.base import Base  # noqa: E402,F401
from app.db.fund_source import fund_source_query  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.kis_index import INDICES  # noqa: E402

BATCH_SIZE = 5000


def ensure_instrument(db, ticker: str, name: str) -> int:
    inst = (db.query(Instrument)
            .filter(Instrument.ticker == ticker, Instrument.asset_type == "index").first())
    if inst is None:
        inst = Instrument(ticker=ticker, name=name, asset_type="index")
        db.add(inst)
        db.flush()
    return inst.id


def main(dry_run: bool) -> None:
    db = SessionLocal()
    try:
        for ticker, (_, name, src) in INDICES.items():
            rows = fund_source_query(
                "SELECT base_dt, nav FROM underlying_index WHERE ticker = :t ORDER BY base_dt",
                {"t": src})
            if not rows:
                print(f"  {ticker:<8} 소스에 {src} 없음 — 건너뜀")
                continue
            print(f"  {ticker:<8} ← {src}  소스 {len(rows):>6,}건  {rows[0][0]} ~ {rows[-1][0]}")
            if dry_run:
                continue

            iid = ensure_instrument(db, ticker, name)
            values = [dict(instrument_id=iid, date=d, period="D", close=float(v))
                      for d, v in rows]
            for i in range(0, len(values), BATCH_SIZE):
                stmt = pg_insert(Price).values(values[i:i + BATCH_SIZE])
                db.execute(stmt.on_conflict_do_update(
                    index_elements=["instrument_id", "date", "period"],
                    set_={"close": stmt.excluded.close}))
            db.commit()
            n = db.query(Price).filter(Price.instrument_id == iid, Price.period == "D").count()
            print(f"  {ticker:<8} 적재 완료 — {n:,}행")

        if dry_run:
            print("\n--dry-run 이므로 적재하지 않고 종료합니다.")
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    main(p.parse_args().dry_run)
