"""ETF 청산분배금을 dividends에서 걷어내고 해당 종목의 배당조정을 다시 계산한다 (1회성).

`load_etf_from_finance_db.py` 최초 실행 때 SEIBRO 분배금을 사유 구분 없이 전부 넣었다.
**청산분배는 배당이 아니다** — 이익분배는 배당락으로 주가가 떨어지니 되돌려주는 게 맞지만,
청산분배는 상장폐지하며 NAV를 그대로 지급하는 것이라 주가가 떨어지지 않는다. 되돌려주면
이중계상이고 배당조정 지수가 통째로 부풀어 오른다.

실측: 292730(FOCUS KRX300)은 청산분배 26,660원이 직전종가 27,190원과 맞먹어 지수가 거의
2배로 뛰었다(소스 TR 대비 오차 98%). 같은 유형이 248건 있다(평균 24,225원).

적재 시점에 `(instrument, ex_date)` 단위로 합산했으므로, 같은 날 이익분배와 청산분배가
함께 있는 경우가 있을 수 있다. 그래서 **삭제가 아니라 이익분배만으로 다시 계산**해서
값이 남으면 갱신, 0이면 삭제한다.

로더 본체는 이미 `RGT_RSN_DTAIL_NM='이익분배'`만 적재하도록 고쳤으므로 재실행 시에는
이 문제가 재발하지 않는다. 이 스크립트는 이미 들어간 데이터를 고치는 용도다.

사용법:
  python scripts/fix_etf_liquidation_dividends.py --dry-run
  python scripts/fix_etf_liquidation_dividends.py
"""
import argparse
import io
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted  # noqa: E402

MYSQL = ["mysql", "-h127.0.0.1", "-P3306", "-uroot", "-p0617",
         "--default-character-set=utf8mb4", "--batch", "--raw", "-N", "finance", "-e"]


def q(sql: str) -> list[list[str]]:
    r = subprocess.run(MYSQL + [sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"MySQL 조회 실패: {r.stderr.strip()}")
    return [line.split("\t") for line in r.stdout.splitlines() if line.strip()]


def main(dry_run: bool):
    db = SessionLocal()

    # 이익분배만으로 다시 집계한 (종목, 배정기준일)별 정답
    ok = q("""select d.ticker, d.RGT_STD_DT, sum(d.ESTM_STDPRC)
              from etf_kr_seibro_dividend d
              join etf_kr_krx_basic_info b on b.ISU_SRT_CD = d.ticker
              where d.ESTM_STDPRC > 0 and d.RGT_RSN_DTAIL_NM = '이익분배'
              group by d.ticker, d.RGT_STD_DT""")
    print(f"이익분배 기준 정답 {len(ok):,}건")

    db.execute(text("""create temp table tmp_ok (ticker varchar(32), ex date, amt numeric)
                       on commit drop"""))
    buf = io.StringIO()
    for t, ex, amt in ok:
        buf.write(f"{t}\t{ex}\t{amt}\n")
    buf.seek(0)
    with db.connection().connection.cursor() as cur:
        cur.copy_expert("copy tmp_ok from stdin with (format text)", buf)

    # 현재 ETF 배당 중 정답과 다른 것 = 청산분배가 섞인 행
    diff = db.execute(text("""
        select count(*) filter (where o.amt is null) as 삭제대상,
               count(*) filter (where o.amt is not null and abs(d.amount - o.amt) > 0.0001) as 금액수정,
               count(distinct d.instrument_id) filter
                 (where o.amt is null or abs(d.amount - o.amt) > 0.0001) as 영향종목
        from dividends d
        join instruments i on i.id = d.instrument_id and i.asset_type = 'etf'
        left join tmp_ok o on o.ticker = i.ticker and o.ex = d.ex_date""")).fetchone()
    print(f"삭제 대상 {diff[0]:,}건 · 금액 수정 {diff[1]:,}건 · 영향 종목 {diff[2]:,}개")

    sample = db.execute(text("""
        select i.ticker, i.name, d.ex_date, d.amount, o.amt
        from dividends d
        join instruments i on i.id = d.instrument_id and i.asset_type = 'etf'
        left join tmp_ok o on o.ticker = i.ticker and o.ex = d.ex_date
        where o.amt is null or abs(d.amount - o.amt) > 0.0001
        order by d.amount desc limit 5""")).fetchall()
    print("\n상위 5건 (현재 -> 수정후):")
    for t, n, ex, cur_amt, new_amt in sample:
        print(f"  {t} {n[:28]:30s} {ex}  {float(cur_amt):>10,.0f} -> "
              f"{'삭제' if new_amt is None else f'{float(new_amt):,.0f}'}")

    if dry_run:
        print("\n--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return

    affected = [r[0] for r in db.execute(text("""
        select distinct d.instrument_id
        from dividends d
        join instruments i on i.id = d.instrument_id and i.asset_type = 'etf'
        left join tmp_ok o on o.ticker = i.ticker and o.ex = d.ex_date
        where o.amt is null or abs(d.amount - o.amt) > 0.0001""")).fetchall()]

    db.execute(text("""
        update dividends d set amount = o.amt
        from instruments i, tmp_ok o
        where i.id = d.instrument_id and i.asset_type='etf'
          and o.ticker = i.ticker and o.ex = d.ex_date
          and abs(d.amount - o.amt) > 0.0001"""))
    deleted = db.execute(text("""
        delete from dividends d
        using instruments i
        where i.id = d.instrument_id and i.asset_type='etf'
          and not exists (select 1 from tmp_ok o where o.ticker = i.ticker and o.ex = d.ex_date)""")).rowcount
    db.commit()
    print(f"\n삭제 {deleted:,}건 · 금액 수정 완료. 영향 종목 {len(affected):,}개 배당조정 재계산 ...")

    for k, iid in enumerate(affected, 1):
        recompute_dividend_adjusted(db, iid, force_full=True)
        if k % 50 == 0:
            db.commit()
            print(f"  {k:,}/{len(affected):,}")
    db.commit()
    print("재계산 완료")

    left = db.execute(text("""select count(*) from dividends d
        join instruments i on i.id=d.instrument_id where i.asset_type='etf'""")).scalar()
    print(f"남은 ETF 배당 {left:,}건")
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    main(a.dry_run)
