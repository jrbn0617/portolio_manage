"""특정 거래일의 가격 데이터를 prices/raw_closes에서 제거하고 파생 데이터를 되돌린다.

장중에 daily_update를 실행해 미확정(장중 스냅샷) 시세가 적재된 경우처럼,
특정 날짜분을 통째로 무르고 싶을 때 쓴다.

사용법: python scripts/purge_trading_day.py YYYY-MM-DD
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.models.raw_close import RawClose  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted, recompute_monthly_bar  # noqa: E402


def main(target: datetime.date):
    db = SessionLocal()

    affected = [
        r[0]
        for r in db.query(Price.instrument_id)
        .filter(Price.period == "D", Price.date == target)
        .distinct()
        .all()
    ]
    print(f"{target} 일봉 보유 종목: {len(affected)}개")
    if not affected:
        print("삭제할 데이터가 없습니다.")
        return

    deleted_p = (
        db.query(Price).filter(Price.period == "D", Price.date == target).delete(synchronize_session=False)
    )
    deleted_r = db.query(RawClose).filter(RawClose.date == target).delete(synchronize_session=False)
    db.commit()
    print(f"삭제: prices(D) {deleted_p}건, raw_closes {deleted_r}건")

    # 월봉은 '그 달 마지막 거래일' 기준이라 날짜가 앞당겨져야 한다.
    print("월봉 재계산 중...")
    for instrument_id in affected:
        with db.begin_nested():
            recompute_monthly_bar(db, instrument_id, target.year, target.month)
    db.commit()

    # 배당조정 지수는 증분 호출로 충분하다 — 내부에서 가격이 사라진 날짜의
    # 고아 행을 먼저 정리한 뒤 마지막 유효 지점부터 다시 이어붙인다.
    print("배당조정 지수 재계산 중...")
    for idx, instrument_id in enumerate(affected, start=1):
        with db.begin_nested():
            recompute_dividend_adjusted(db, instrument_id)
        if idx % 500 == 0:
            db.commit()
            print(f"  {idx}/{len(affected)}")
    db.commit()
    print("done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python scripts/purge_trading_day.py YYYY-MM-DD")
    main(datetime.date.fromisoformat(sys.argv[1]))
