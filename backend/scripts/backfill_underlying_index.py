"""SOFRINDEX·KOFRINDEX 과거 구간을 소스 DB(`underlying_index`)에서 옮긴다. 1회성.

일별 배치(`refresh_macro_daily.py`)가 만드는 구간은 원천이 시작한 2018년부터다.
그 이전은 소스 DB 에만 있다 — 뒤로 이어붙인 합성 계열이라 원천 공시에는 없다.

    SOFRINDEX   1954-07-01 ~     실제 SOFR 공시는 2018-04-02 부터
    KOFRINDEX   1995-01-03 ~     실제 KOFR 공시는 2018-01-02 부터

**겹치는 구간은 원천 값이 이긴다.** 소스 DB 의 `nav` 가 MySQL `float`(단정밀도)라
유효숫자 7자리에서 잘린다 — KOFR 1194.28656 이 1194.29 로 들어가 있다. 그래서
이 스크립트로 전 구간을 넣은 뒤 일별 배치를 돌리면 2018년 이후가 제 정밀도로 덮인다.
순서를 뒤집지 말 것.

정렬은 확인했다 — 소스 KOFRINDEX 2018-01-02 = 1000.0(기준시점과 일치), SOFRINDEX
2018-04-02 = 1.0. 겹치는 날 값도 반올림 오차 안에서 같다.

사용법:
  python scripts/backfill_underlying_index.py --dry-run
  python scripts/backfill_underlying_index.py
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
from app.models.macro_indicator import MacroIndicator  # noqa: E402

TICKERS = ["SOFRINDEX", "KOFRINDEX"]
BATCH_SIZE = 5000


def main(dry_run: bool) -> None:
    db = SessionLocal()
    try:
        for ticker in TICKERS:
            rows = fund_source_query(
                "SELECT base_dt, nav FROM underlying_index WHERE ticker = :t ORDER BY base_dt",
                {"t": ticker})
            if not rows:
                print(f"  {ticker:<12} 소스에 없음 — 건너뜀")
                continue

            before = db.query(MacroIndicator).filter(
                MacroIndicator.indicator_name == ticker).count()
            print(f"  {ticker:<12} 소스 {len(rows):>6,}건  {rows[0][0]} ~ {rows[-1][0]}"
                  f"   (기존 적재 {before:,}건)")
            if dry_run:
                continue

            values = [dict(indicator_name=ticker, date=d, value=float(v)) for d, v in rows]
            for i in range(0, len(values), BATCH_SIZE):
                stmt = pg_insert(MacroIndicator).values(values[i:i + BATCH_SIZE])
                db.execute(stmt.on_conflict_do_update(
                    index_elements=["indicator_name", "date"],
                    set_={"value": stmt.excluded.value}))
            db.commit()
            after = db.query(MacroIndicator).filter(
                MacroIndicator.indicator_name == ticker).count()
            print(f"  {ticker:<12} 적재 완료 — {after:,}건 (신규 {after - before:,})")

        if dry_run:
            print("\n--dry-run 이므로 적재하지 않고 종료합니다.")
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    main(p.parse_args().dry_run)
