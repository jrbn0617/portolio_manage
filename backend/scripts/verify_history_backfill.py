"""2014~2018 백필 적재 결과를 검증한다 (읽기 전용).

`load_history_backfill.py` 적재 후 돌린다. 확인 항목:

1. **테이블별 백필분 행수·날짜 범위** — 의도한 2013-12-30 ~ 2018-12-27 인가
2. **유령행** — 거래일 달력에 없는 날짜가 들어갔는가 (WISEfn은 비거래일을 앞값으로 채워 보낸다)
3. **기존 데이터 보존** — 2018-12-28 이후 행수가 적재 전과 같은가 (ON CONFLICT DO NOTHING 확인)
4. **단위 경계 검증** — 백필 구간(~2018-12-27)과 기존 구간(2018-12-28~)의 경계에서 값이
   자연스럽게 이어지는가. **이게 가장 중요하다.** 수급 수량 x1,000 / 대금 x1,000,000 /
   EBITDA x1,000 / 차입공매도금액 x1,000 네 군데에 환산이 걸려 있어, 스케일을 잘못 잡았으면
   경계에서 계단이 생긴다. 종목별 경계 전후 중앙값 비율로 판정한다.
5. **우선주 혼입** — 백필 데이터에 우선주가 들어갔는가
6. **커버리지** — 지수 편입 이력이 있는 종목 중 백필 가격이 있는 비율 (백테스트 가능 여부)

사용법: python scripts/verify_history_backfill.py
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402

CUT = "2018-12-28"   # 기존 데이터 시작일
BF_START = "2013-12-01"


def h(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main():
    db = SessionLocal()

    h("1. 테이블별 적재 현황 (asset_type='stock')")
    q = """
    select :label, count(*) filter (where d.date < :cut) as 백필분,
           count(*) filter (where d.date >= :cut) as 기존분,
           min(d.date), max(d.date), count(distinct d.instrument_id)
    from {tbl} d join instruments i on i.id = d.instrument_id where i.asset_type='stock' {extra}
    """
    specs = [("prices(D)", "prices", "and d.period='D'"), ("prices(M)", "prices", "and d.period='M'"),
             ("raw_closes", "raw_closes", ""), ("monthly_fundamentals", "monthly_fundamentals", ""),
             ("investor_trading", "investor_trading", ""), ("short_selling", "short_selling", ""),
             ("dividend_adjusted_prices(D)", "dividend_adjusted_prices", "and d.period='D'")]
    print(f"{'테이블':30s} {'백필분':>12s} {'기존분':>12s} {'시작':>12s} {'끝':>12s} {'종목':>7s}")
    for label, tbl, extra in specs:
        r = db.execute(text(q.format(tbl=tbl, extra=extra)), {"label": label, "cut": CUT}).fetchone()
        print(f"{label:30s} {r[1]:>12,} {r[2]:>12,} {str(r[3]):>12s} {str(r[4]):>12s} {r[5]:>7,}")

    h("2. 유령행 — 거래일이 아닌 날짜가 들어갔는가")
    db.execute(text("""create temp table td as select distinct p.date d from prices p
        join instruments i on i.id=p.instrument_id where i.asset_type='etf' and p.period='D'"""))
    db.execute(text("create unique index on td (d)"))
    for tbl, extra in (("prices", "and d.period='D'"), ("investor_trading", ""), ("short_selling", "")):
        n = db.execute(text(f"""select count(*) from {tbl} d
            join instruments i on i.id=d.instrument_id
            where i.asset_type='stock' and d.date < :cut and d.date >= :s {extra}
              and not exists (select 1 from td where td.d = d.date)"""),
            {"cut": CUT, "s": BF_START}).scalar()
        print(f"  {tbl:22s} 거래일 아닌 행 {n:,}건 {'✓' if n == 0 else '← 확인 필요'}")
    wk = db.execute(text("""select count(*) from prices p join instruments i on i.id=p.instrument_id
        where i.asset_type='stock' and p.period='D' and p.date < :cut and p.date >= :s
          and extract(dow from p.date) in (0,6)"""), {"cut": CUT, "s": BF_START}).scalar()
    print(f"  {'주말':22s} {wk:,}건 {'✓' if wk == 0 else '← 확인 필요'}")

    h("3. 우선주 혼입")
    n = db.execute(text("""select count(distinct i.ticker) from prices p
        join instruments i on i.id=p.instrument_id
        where i.asset_type='stock' and right(i.ticker,1) <> '0' and p.date < :cut"""),
        {"cut": CUT}).scalar()
    print(f"  끝자리≠0 종목의 백필 가격 {n:,}종목 {'✓' if n == 0 else '← 확인 필요'}")

    h("4. 단위 경계 검증 — 백필 구간 vs 기존 구간 (종목별 중앙값 비율)")
    print("  스케일이 맞으면 비율이 1 근처. 1000배/100만배로 벌어지면 환산 오류.\n")
    checks = [
        ("investor_trading.net_volume", "investor_trading", "abs(net_volume)", "and investor_type='기관합계'"),
        ("investor_trading.net_value", "investor_trading", "abs(net_value)", "and investor_type='기관합계'"),
        ("short_selling.short_value", "short_selling", "short_value", "and short_value > 0"),
        ("short_selling.total_value", "short_selling", "total_value", "and total_value > 0"),
        ("short_selling.short_volume", "short_selling", "short_volume", "and short_volume > 0"),
        ("short_selling.total_volume", "short_selling", "total_volume", "and total_volume > 0"),
        ("prices.close", "prices", "close", "and period='D'"),
        ("prices.market_cap", "prices", "market_cap", "and period='D' and market_cap > 0"),
    ]
    print(f"{'항목':34s} {'백필 중앙값':>18s} {'기존 중앙값':>18s} {'비율':>10s}")
    for label, tbl, col, extra in checks:
        r = db.execute(text(f"""
            with b as (select percentile_cont(0.5) within group (order by {col}::numeric) v
                       from {tbl} d join instruments i on i.id=d.instrument_id
                       where i.asset_type='stock' and d.date between :a and :b {extra}),
                 a as (select percentile_cont(0.5) within group (order by {col}::numeric) v
                       from {tbl} d join instruments i on i.id=d.instrument_id
                       where i.asset_type='stock' and d.date between :c and :e {extra})
            select b.v, a.v from b, a"""),
            {"a": "2018-10-01", "b": "2018-12-27", "c": CUT, "e": "2019-03-31"}).fetchone()
        bv, av = (float(r[0]) if r[0] else 0), (float(r[1]) if r[1] else 0)
        ratio = bv / av if av else float("nan")
        flag = "✓" if 0.2 <= ratio <= 5 else "← 확인 필요"
        print(f"{label:34s} {bv:>18,.1f} {av:>18,.1f} {ratio:>9.3f} {flag}")

    print("\n  월간팩터 (월말 기준이라 분기별 비교)")
    for metric in ("ebitda_ttm", "shares_outstanding_monthly", "free_float_ratio", "ev_ebitda_fwd_12m"):
        r = db.execute(text("""
            with b as (select percentile_cont(0.5) within group (order by value) v from monthly_fundamentals m
                       join instruments i on i.id=m.instrument_id
                       where i.asset_type='stock' and m.metric=:m and m.date between '2018-06-01' and '2018-12-27'),
                 a as (select percentile_cont(0.5) within group (order by value) v from monthly_fundamentals m
                       join instruments i on i.id=m.instrument_id
                       where i.asset_type='stock' and m.metric=:m and m.date between :c and '2019-06-30')
            select b.v, a.v from b, a"""), {"m": metric, "c": CUT}).fetchone()
        bv, av = (float(r[0]) if r[0] else 0), (float(r[1]) if r[1] else 0)
        ratio = bv / av if av else float("nan")
        flag = "✓" if 0.2 <= ratio <= 5 else "← 확인 필요"
        print(f"  {metric:32s} {bv:>18,.1f} {av:>18,.1f} {ratio:>9.3f} {flag}")

    h("5. 종목 단위 경계 연속성 — 가격이 실제로 이어지는가")
    r = db.execute(text("""
        with e as (
          select p.instrument_id,
                 max(p.close) filter (where p.date = (select max(date) from prices x
                    where x.instrument_id=p.instrument_id and x.period='D' and x.date < :cut)) prev,
                 max(p.close) filter (where p.date = (select min(date) from prices x
                    where x.instrument_id=p.instrument_id and x.period='D' and x.date >= :cut)) next
          from prices p join instruments i on i.id=p.instrument_id
          where i.asset_type='stock' and p.period='D'
            and p.date between '2018-12-01' and '2019-01-31'
          group by p.instrument_id)
        select count(*), count(*) filter (where prev is not null and next is not null
                 and abs(next/prev - 1) < 0.15),
               count(*) filter (where prev is not null and next is not null
                 and abs(next/prev - 1) >= 0.15)
        from e"""), {"cut": CUT}).fetchone()
    print(f"  경계 양쪽에 가격이 있는 종목 {r[0]:,} · 변화 15% 미만 {r[1]:,} · 15% 이상 {r[2]:,}")
    if r[0]:
        print(f"  연속 비율 {r[1]/max(r[1]+r[2],1):.1%} (배당락·급등락이 섞여 100%는 아님)")

    h("6. 백테스트 커버리지 — 지수 편입 종목 중 백필 가격 보유")
    for idx in ("KOSPI200", "KOSDAQ150", "KOSPI", "KOSDAQ"):
        r = db.execute(text("""
            with u as (select distinct instrument_id iid from index_memberships
                       where index_name=:x and as_of_date < :cut)
            select count(*), count(*) filter (where exists (
                select 1 from prices p where p.instrument_id=u.iid and p.period='D' and p.date < :cut))
            from u"""), {"x": idx, "cut": CUT}).fetchone()
        pct = r[1] / r[0] if r[0] else 0
        print(f"  {idx:10s} 편입이력 {r[0]:>4,}종목 중 백필가격 보유 {r[1]:>4,} ({pct:.1%})")

    h("7. market_holidays")
    for row in db.execute(text("""select extract(year from date)::int y, count(*) from market_holidays
        where date < '2019-01-01' group by 1 order by 1""")).fetchall():
        print(f"  {row[0]}년 {row[1]:>3}건")

    db.close()


if __name__ == "__main__":
    main()
