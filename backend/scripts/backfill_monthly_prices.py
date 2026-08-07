"""기존에 적재된 일봉(prices, period='D') 전체를 대상으로 월봉을 생성/갱신한다.
새로 들어오는 업로드는 upload_service.process_upload가 자동으로 처리하므로,
이 스크립트는 이미 쌓여 있던 데이터에 대한 1회성 백필용이다.

사용법: python scripts/backfill_monthly_prices.py
"""
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.services.derived_prices import recompute_monthly_bar  # noqa: E402

COMMIT_EVERY = 500


def main():
    db = SessionLocal()

    combos = db.execute(
        text(
            """
            SELECT DISTINCT instrument_id,
                   EXTRACT(YEAR FROM date)::int AS year,
                   EXTRACT(MONTH FROM date)::int AS month
            FROM prices
            WHERE period = 'D'
            ORDER BY instrument_id, year, month
            """
        )
    ).all()
    print(f"대상 (종목, 연월) 조합: {len(combos)}개")

    for i, (instrument_id, year, month) in enumerate(combos, start=1):
        try:
            recompute_monthly_bar(db, instrument_id, year, month)
        except Exception as exc:  # noqa: BLE001
            print(f"  실패 instrument_id={instrument_id} {year}-{month:02d}: {exc}")
            db.rollback()
            continue

        if i % COMMIT_EVERY == 0:
            db.commit()
            print(f"  {i}/{len(combos)}")

    db.commit()
    print("done.")


if __name__ == "__main__":
    main()
