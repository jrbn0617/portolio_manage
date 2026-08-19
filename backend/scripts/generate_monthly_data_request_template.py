"""WISEfn/DataGuide에 매월 수동으로 요청하는 4개 팩터(유동주식비율/EBITDA(TTM)/
EBITDA(Fwd.12M)/EV EBITDA(Fwd.12M))의 요청 양식을 만든다.
(상장주식수는 pykrx로 자동 수집하므로 이 양식에 없다 — load_shares_outstanding_pykrx.py)

양식 생성 로직은 `app/services/monthly_fundamental_template_service.py`에 있다 —
화면(데이터 업로드 탭)의 '양식 다운로드' 버튼과 같은 코드를 쓴다.

**예전에는 reference/monthly_data_template.xlsx의 'sample' 시트를 복제해 채웠는데,
reference/가 gitignore 대상이라 다른 PC에는 그 파일이 없어 스크립트가 아예 돌지 않았다.**
지금은 레이아웃을 코드로 만들어서 파일 의존이 없다.

받아온 응답 파일은 화면에서 그대로 업로드하면 된다 (monthly_fundamental_bulk_service).

사용법:
  python scripts/generate_monthly_data_request_template.py                  # 당월 기준 6개월치
  python scripts/generate_monthly_data_request_template.py --month 2026-06  # 2026년 6월 기준
  python scripts/generate_monthly_data_request_template.py --lookback 12
"""
import argparse
import datetime
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.services.monthly_fundamental_template_service import (  # noqa: E402
    DEFAULT_LOOKBACK_MONTHS,
    build_template,
    fetch_universe,
    holiday_coverage_through,
    month_end_business_days,
)

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "reference"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--month", type=str, default=None, help="대상월 YYYY-MM (기본: 당월)")
    p.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_MONTHS, help="담을 개월 수")
    p.add_argument("--out", type=Path, default=None, help="저장 경로 (기본: reference/)")
    return p.parse_args()


def main():
    args = parse_args()
    if args.month is not None:
        year, month = (int(x) for x in args.month.split("-"))
    else:
        today = datetime.date.today()
        year, month = today.year, today.month

    db = SessionLocal()
    try:
        dates = month_end_business_days(db, year, month, args.lookback)
        print(f"대상 {len(dates)}개월 마지막 영업일: {[d.isoformat() for d in dates]}")

        # 휴장일 테이블은 지나간 날짜만 채워져 있다 — 그 뒤 월말은 휴장 여부를 알 수 없다.
        covered = holiday_coverage_through(db)
        if covered and dates[-1] > covered:
            print(f"  주의: market_holidays가 {covered}까지만 채워져 있어 "
                  f"{dates[-1]} 의 휴장 여부는 확인되지 않았습니다.")

        start = datetime.date(dates[0].year, dates[0].month, 1)
        universe = fetch_universe(db, start, dates[-1])
        print(f"유니버스({start}~{dates[-1]} 코스피/코스닥 보통주): {len(universe)}종목")
        wb = build_template(db, year, month, args.lookback)
    finally:
        db.close()

    out_path = args.out or OUTPUT_DIR / f"monthly_data_request_{year:04d}-{month:02d}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    print(f"시트 {len(wb.sheetnames)}개 · 저장 완료: {out_path}")


if __name__ == "__main__":
    main()
