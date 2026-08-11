"""DataGuide 백필분의 "공매도 없음"을 NULL 대신 0으로 통일한다 (1회성).

두 소스가 "그날 공매도가 한 건도 없었다"를 다르게 표현한다:
  - DataGuide 백필(~2026-08-03): 아예 값을 주지 않음 -> NULL
  - pykrx 일별 수집(2026-08-04~): 0으로 기록
그대로 두면 같은 사실이 구간별로 NULL/0으로 갈려서, 평균·중앙값 등을 낼 때 앞 구간만
표본에서 빠지는 편향이 생긴다(실측: 2026-08-04 하루에만 0 기록이 468종목).

변환 규칙 — 거래량이 있는데(= 그날 실제로 거래된 종목) 공매도 값이 없으면 "공매도 0"으로
간주한다:
  total_volume > 0 AND short_volume IS NULL
    -> short_volume=0, short_value=0, volume_ratio=0
    -> value_ratio=0 (단 total_value가 있을 때만. 거래대금은 백필 청크2가 누락돼
       500종목분이 비어 있어 그 경우는 NULL 유지)

거래량 자체가 없거나 0인 행(거래정지 등)은 "공매도가 0이었다"고 단정할 수 없으므로
NULL로 남긴다.

재실행해도 안전하다(이미 0인 행은 조건에 걸리지 않음). 백필을 다시 돌린 뒤에는 이 스크립트도
다시 실행해야 한다.

사용법:
  python scripts/normalize_short_selling_zeros.py --dry-run   # 건수만 확인
  python scripts/normalize_short_selling_zeros.py             # 실제 변환
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402

DIAGNOSE_SQL = """
select
  count(*) total,
  sum(case when short_volume is null and total_volume > 0 then 1 else 0 end) target,
  sum(case when short_volume is null and (total_volume is null or total_volume = 0) then 1 else 0 end) kept_null,
  sum(case when short_volume is null and total_volume > 0 and (total_value is null or total_value = 0) then 1 else 0 end) no_total_value
from short_selling
"""

UPDATE_SQL = """
update short_selling
   set short_volume = 0,
       short_value  = 0,
       volume_ratio = 0,
       value_ratio  = case when total_value > 0 then 0 else null end
 where short_volume is null
   and total_volume > 0
"""


def diagnose(db) -> None:
    r = db.execute(text(DIAGNOSE_SQL)).fetchone()
    print(f"  전체 행: {r[0]:,}")
    print(f"  변환 대상(거래량>0 & 공매도 NULL): {r[1]:,}")
    print(f"  NULL 유지(거래량 없음/0 = 거래정지 등): {r[2]:,}")
    print(f"  변환 대상 중 거래대금 없어 value_ratio는 NULL 유지: {r[3]:,}")


def main(dry_run: bool):
    db = SessionLocal()
    print("=== 변환 전")
    diagnose(db)

    if dry_run:
        print("\n--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return

    print("\n변환 중 ...")
    result = db.execute(text(UPDATE_SQL))
    db.commit()
    print(f"  {result.rowcount:,}행 변경")

    print("\n=== 변환 후")
    diagnose(db)
    db.close()
    print("완료.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="건수만 확인하고 변경하지 않음")
    main(p.parse_args().dry_run)
