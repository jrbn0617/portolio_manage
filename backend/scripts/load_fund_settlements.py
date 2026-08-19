"""펀드 결산·상환·분배 백필 — fund_settlements.

수정기준가의 원천이다. 회계기말 다음 영업일(ex_dividend_dt)에 기준가가
post_settlement_nav 로 리셋된다 — 주식의 배당락과 같은 성격이다.

**상환(settlement_type='상환')은 post_settlement_nav 가 0 이다**(136,048행). 수정계수를
구할 때 나누면 무한대가 되므로 계산 쪽에서 '결산'·'분배'만 쓴다. 여기서는 원본 그대로
다 적재한다 — 상환일 자체가 펀드 종료 시점 정보라 버릴 이유가 없다.

사용법:  python scripts/load_fund_settlements.py [--dry-run]
"""
import argparse
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from psycopg2.extras import execute_values  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.fund_source import fund_source_connection  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402

FUND_CHUNK = 2000

UPSERT = """
INSERT INTO fund_settlements
  (fund_id, period_start_value, period_end_value, settlement_type, elapsed_days,
   inception_principal, nav, tax_base_nav, post_settlement_nav, ex_dividend_dt)
VALUES %s
ON CONFLICT (fund_id, period_end_value, settlement_type) DO UPDATE
SET period_start_value = EXCLUDED.period_start_value,
    elapsed_days = EXCLUDED.elapsed_days,
    inception_principal = EXCLUDED.inception_principal,
    nav = EXCLUDED.nav, tax_base_nav = EXCLUDED.tax_base_nav,
    post_settlement_nav = EXCLUDED.post_settlement_nav,
    ex_dividend_dt = EXCLUDED.ex_dividend_dt, updated_at = now()
"""

SELECT_SRC = """
SELECT fund_code, period_start_value, period_end_value, settlement_type, elapsed_days,
       inception_principal, nav, tax_base_nav, post_settlement_nav, ex_dividend_dt
FROM fund_kr_kofia_settlement WHERE fund_code IN ({ph})
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    db = SessionLocal()
    funds = {r[0]: r[1] for r in db.execute(text("SELECT fund_code, id FROM funds"))}
    codes = sorted(funds)
    print(f"대상 펀드 {len(codes):,}")

    raw = engine.raw_connection()
    total, dup, t0 = 0, 0, time.time()
    try:
        with fund_source_connection() as src:
            for i in range(0, len(codes), FUND_CHUNK):
                ch = codes[i:i + FUND_CHUNK]
                ph = ",".join(f":c{j}" for j in range(len(ch)))
                rows = src.execute(text(SELECT_SRC.format(ph=ph)),
                                   {f"c{j}": c for j, c in enumerate(ch)}).all()
                # 소스 PK 는 (fund_code, period_end_value, settlement_type) 이지만 같은 키가
                # 여러 번 오는 경우가 있어 마지막 것만 남긴다 — ON CONFLICT 는 한 문장 안의
                # 중복을 처리하지 못한다.
                seen = {}
                for r in rows:
                    seen[(r[0], r[2], r[3])] = r
                dup += len(rows) - len(seen)
                values = [(funds[r[0]], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9])
                          for r in seen.values()]
                if values and not a.dry_run:
                    with raw.cursor() as cur:
                        execute_values(cur, UPSERT, values, page_size=5000)
                    raw.commit()
                total += len(values)
                print(f"  [{(i+len(ch))/len(codes)*100:5.1f}%] {total:,}행 · "
                      f"{time.time()-t0:.0f}s", flush=True)
    finally:
        raw.close()
        db.close()
    print(f"\n{'(dry-run) ' if a.dry_run else ''}완료: {total:,}행 · 키 중복 제거 {dup:,}건 · "
          f"{(time.time()-t0)/60:.1f}분")


if __name__ == "__main__":
    main()
