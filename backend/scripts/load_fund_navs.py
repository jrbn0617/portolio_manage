"""펀드 일별 기준가 백필 — fund_navs.

소스(MySQL)의 fund_kr_kofia_daily_price 1,438만 행을 옮긴다. 통째로 읽으면 메모리에
안 들어가므로 **펀드코드 묶음 단위로 스트리밍**한다.

재개 가능 — 묶음 하나가 한 트랜잭션이라 중간에 끊겨도 부분 적재가 남지 않고, 다시
돌리면 이미 들어간 펀드는 건너뛴다. `--no-resume` 으로 전체를 다시 훑을 수 있다
(값이 정정된 경우; ON CONFLICT DO UPDATE 라 중복 적재는 나지 않는다).

사용법:
  python scripts/load_fund_navs.py --dry-run
  python scripts/load_fund_navs.py --manage-only     # 운용펀드만
  python scripts/load_fund_navs.py                   # 전체 (운용펀드+클래스)
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

FUND_CHUNK = 200        # 한 트랜잭션에 담을 펀드 수
PAGE_SIZE = 5000        # execute_values 페이지

UPSERT = """
INSERT INTO fund_navs (fund_id, base_dt, nav, tax_base_nav, aum)
VALUES %s
ON CONFLICT (fund_id, base_dt) DO UPDATE
SET nav = EXCLUDED.nav, tax_base_nav = EXCLUDED.tax_base_nav,
    aum = EXCLUDED.aum, updated_at = now()
"""


def target_funds(db, manage_only):
    sql = "SELECT fund_code, id FROM funds"
    if manage_only:
        sql += " WHERE is_manage_fund"
    return {r[0]: r[1] for r in db.execute(text(sql))}


def already_loaded(db):
    """이미 기준가가 들어간 fund_id. 묶음 단위 트랜잭션이라 있으면 완결된 것이다."""
    return {r[0] for r in db.execute(text("SELECT DISTINCT fund_id FROM fund_navs"))}


def fetch_chunk(src_conn, codes):
    ph = ",".join(f":c{i}" for i in range(len(codes)))
    return src_conn.execute(text(
        f"""SELECT fund_code, base_dt, nav, tax_base_nav, aum
            FROM fund_kr_kofia_daily_price WHERE fund_code IN ({ph})"""),
        {f"c{i}": c for i, c in enumerate(codes)}).all()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--manage-only", action="store_true", help="운용펀드만 적재")
    p.add_argument("--no-resume", action="store_true", help="이미 적재된 펀드도 다시 훑는다")
    p.add_argument("--limit", type=int, default=None, help="펀드 N개만 (시험용)")
    a = p.parse_args()

    db = SessionLocal()
    funds = target_funds(db, a.manage_only)
    done = set() if a.no_resume else already_loaded(db)
    todo = sorted(c for c, fid in funds.items() if fid not in done)
    if a.limit:
        todo = todo[:a.limit]

    print(f"대상 펀드 {len(funds):,} · 이미 적재 {len(done):,} · 이번에 처리 {len(todo):,}")
    if a.dry_run:
        with fund_source_connection() as src:
            sample = fetch_chunk(src, todo[:20])
        print(f"\n--dry-run · 앞 20개 펀드의 소스 행수 {len(sample):,}")
        for r in sample[:5]:
            print(f"  {r[0]}  {r[1]}  nav={r[2]}  tax={r[3]}  aum={r[4]}")
        db.close()
        return
    if not todo:
        print("적재할 펀드가 없습니다.")
        db.close()
        return

    raw = engine.raw_connection()
    total, t0 = 0, time.time()
    try:
        with fund_source_connection() as src:
            for i in range(0, len(todo), FUND_CHUNK):
                codes = todo[i:i + FUND_CHUNK]
                rows = fetch_chunk(src, codes)
                values = [(funds[r[0]], r[1], r[2], r[3], r[4]) for r in rows]
                if values:
                    with raw.cursor() as cur:
                        execute_values(cur, UPSERT, values, page_size=PAGE_SIZE)
                    raw.commit()      # 묶음 단위 커밋 = 재개 지점
                total += len(values)
                pct = (i + len(codes)) / len(todo) * 100
                el = time.time() - t0
                rate = total / el if el else 0
                print(f"  [{pct:5.1f}%] 펀드 {i+len(codes):,}/{len(todo):,} · "
                      f"{total:,}행 · {rate:,.0f}행/s · 경과 {el/60:.1f}분", flush=True)
    finally:
        raw.close()
        db.close()
    print(f"\n완료: {total:,}행 · {(time.time()-t0)/60:.1f}분")


if __name__ == "__main__":
    main()
