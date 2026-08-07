"""가격 데이터가 있는 전체 종목에 대해 배당조정 수정종가 지수(dividend_adjusted_prices)를
처음부터(force_full) 다시 계산한다. 계산 방식이 바뀌었을 때(예: 역산->순방향 전환) 재실행하는
1회성 스크립트. 이후 신규 업로드는 upload_service.process_upload가 증분으로 자동 재계산한다.

사용법: python scripts/backfill_dividend_adjusted_prices.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted  # noqa: E402

COMMIT_EVERY = 50


def main():
    db = SessionLocal()

    instrument_ids = [row[0] for row in db.query(Price.instrument_id).distinct().all()]
    print(f"대상 종목: {len(instrument_ids)}개")

    for i, instrument_id in enumerate(instrument_ids, start=1):
        try:
            recompute_dividend_adjusted(db, instrument_id, force_full=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  실패 instrument_id={instrument_id}: {exc}")
            db.rollback()
            continue

        if i % COMMIT_EVERY == 0:
            db.commit()
            print(f"  {i}/{len(instrument_ids)}")

    db.commit()
    print("done.")


if __name__ == "__main__":
    main()
