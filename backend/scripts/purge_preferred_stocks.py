"""우선주를 DB에서 완전히 제거한다 (1회성).

우선주는 전략 대상이 아닌데 지수 시장구분 스냅샷·수급·배당 등을 통해 계속 흘러들어와
있었다. 요청 양식에서 빼는 것만으로는 부족해서 저장된 것도 지운다
(`app/services/instrument_rules.is_preferred` — 종목코드 끝자리가 '0'이 아니면 우선주).

`999999`처럼 끝자리가 0이 아니지만 우선주도 아닌 쓰레기 티커는 --include-junk를 줘야
지운다. 기본값으로는 건드리지 않는다.

instruments를 지우기 전에 자식 테이블부터 지운다(FK). 되돌릴 수 없으니 --dry-run으로
먼저 확인할 것.

사용법:
  python scripts/purge_preferred_stocks.py --dry-run
  python scripts/purge_preferred_stocks.py
"""
import argparse
import sys
from pathlib import Path

from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402

# instruments를 참조하는 테이블 — 삭제 순서대로
CHILD_TABLES = [
    "dividend_adjusted_prices",
    "raw_closes",
    "prices",
    "monthly_fundamentals",
    "investor_trading",
    "short_selling",
    "dividends",
    "index_memberships",
    "corporate_action_events",
]

JUNK_TICKERS = {"999999"}


def target_ids(db, include_junk: bool):
    rows = db.execute(
        text(
            """select id, ticker, name from instruments
               where coalesce(asset_type,'') <> 'index' and right(ticker, 1) <> '0'
               order by ticker"""
        )
    ).fetchall()
    if not include_junk:
        rows = [r for r in rows if r.ticker not in JUNK_TICKERS]
    return rows


def main(dry_run: bool, include_junk: bool):
    db = SessionLocal()
    rows = target_ids(db, include_junk)
    ids = [r.id for r in rows]
    print(f"삭제 대상 우선주: {len(ids):,}종목")
    for r in rows[:8]:
        print(f"   {r.ticker} {r.name}")
    if len(rows) > 8:
        print(f"   ... 외 {len(rows) - 8}개")
    if not ids:
        print("대상이 없습니다.")
        return

    # successor_instrument_id 로 우선주를 가리키는 이벤트가 있으면 FK 때문에 막힌다.
    ref = db.execute(
        text("select count(*) from corporate_action_events where successor_instrument_id = any(:ids)"),
        {"ids": ids},
    ).scalar()
    if ref:
        print(f"\n경고: corporate_action_events.successor_instrument_id가 우선주를 가리키는 행 {ref}건 — 확인 필요")

    print("\n자식 테이블 삭제 예정 건수:")
    total = 0
    for t in CHILD_TABLES:
        n = db.execute(text(f"select count(*) from {t} where instrument_id = any(:ids)"), {"ids": ids}).scalar()
        total += n
        if n:
            print(f"  {t:26s} {n:>10,}")
    print(f"  {'합계':26s} {total:>10,}")

    if dry_run:
        print("\n--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return

    print("\n삭제 중 ...")
    for t in CHILD_TABLES:
        r = db.execute(text(f"delete from {t} where instrument_id = any(:ids)"), {"ids": ids})
        if r.rowcount:
            print(f"  {t:26s} {r.rowcount:>10,}행 삭제")
    r = db.execute(text("delete from instruments where id = any(:ids)"), {"ids": ids})
    db.commit()
    print(f"  {'instruments':26s} {r.rowcount:>10,}행 삭제")

    left = db.execute(
        text("select count(*) from instruments where coalesce(asset_type,'')<>'index' and right(ticker,1)<>'0'")
    ).scalar()
    print(f"\n완료. 남은 끝자리≠0 종목: {left}개 (JUNK 제외분)")
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--include-junk", action="store_true", help=f"{sorted(JUNK_TICKERS)} 같은 비우선주 쓰레기 티커도 함께 삭제")
    a = p.parse_args()
    main(a.dry_run, a.include_junk)
