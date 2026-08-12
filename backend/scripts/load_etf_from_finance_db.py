"""로컬 MySQL `finance` DB의 KRX/SEIBRO ETF 원천 테이블을 우리 스키마로 옮긴다.

소스 (localhost:3306 finance):
  etf_kr_krx_basic_info      종목 기본정보 (상장일·폐지일·보수·자산분류)
  etf_kr_krx_daily_price     일별 OHLCV + 상장좌수 — **원주가(미조정)**
  etf_kr_seibro_dividend     분배금 — ESTM_STDPRC 가 주당분배금(원)
  etf_kr_total_return_index  사용자가 계산해둔 TR 지수 — **검증 전용, 적재하지 않음**

대상 (Postgres portfolio):
  instruments(asset_type='etf') / prices(D) / raw_closes / dividends
  -> 이후 recompute_dividend_adjusted 로 dividend_adjusted_prices 생성

**설계 결정 5가지** (2026-08-12 사용자 승인):

1. **원주가를 그대로 넣는다.** KRX에는 분할조정가가 없어 `prices`와 `raw_closes`가 같은 값이
   된다. 주식은 벤더 수정주가를 쓰지만 ETF는 이 경로가 없다. 제한폭(-30%)을 넘게 빠진
   16건(14종목)이 있는데 배율이 1.5~1.7배라 깔끔한 분할비가 아니다 — 레버리지/인버스의 실제
   급락일 수도 있어 **손대지 않고 목록만 출력한다.**
2. **`dividends.ex_date` 에는 배정기준일(RGT_STD_DT)을 넣는다.** 주식 SEIBRO 적재와 같은
   규약이며 `derived_prices._dividend_effective_date` 가 -1 거래일을 적용한다. 소스에 있는
   `ex_dividend_dt`(배당락일)와 일치하는지 적재 후 대조한다.
3. **주당분배금은 `ESTM_STDPRC`.** TR 지수 역산으로 확정했다 — TR 수익률이 가격 수익률과
   갈리는 날의 역산 분배금이 정확히 정수(220·400·456·475원)로 떨어지고 SEIBRO 기록과 전건
   일치했다. TR은 **배당락일**에 분배금을 되돌려준다.
4. **상장폐지 ETF 32종목도 전부 넣는다.** 빼면 생존편향이 생긴다. 단 **청산분배(RGT_RSN_DTAIL_NM
   ='청산분배')는 배당에서 제외한다** — 이익분배는 배당락으로 주가가 떨어지니 되돌려주는 게
   맞지만, 청산분배는 상장폐지하며 NAV를 그대로 지급하는 것이라 주가가 떨어지지 않는다.
   되돌려주면 이중계상이다. 실제로 292730(FOCUS KRX300)은 청산분배 26,660원이 직전종가
   27,190원과 맞먹어 배당조정 지수가 2배로 뛰었다(TR 대비 오차 98%).
5. **기존 ETF는 지우고 다시 넣는다.** kretf.xlsx로 넣었던 16종목은 DataGuide 수정주가(분할조정)
   라 KRX 원주가와 섞이면 안 된다.

`prices.market_cap` 은 종가 × 상장좌수(LIST_SHRS)로 채운다 — ETF의 시가총액이자 AUM 대용이다.

**소스 TR 지수의 한계**: 분배금 반영이 2025-10-30에서 멈춰 있다(TR 테이블 created_at 2025-11-03).
그 이후 분배금은 빠져 있으므로 **검증은 2025-10-30까지만 유효**하고, 이후 구간은 우리 값이
더 높게 나오는 것이 정상이다.

사용법:
  python scripts/load_etf_from_finance_db.py --dry-run
  python scripts/load_etf_from_finance_db.py
  python scripts/load_etf_from_finance_db.py --verify-only   # 적재 후 TR 대조만
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
from app.services.derived_prices import recompute_dividend_adjusted, recompute_monthly_bar  # noqa: E402

MYSQL = ["mysql", "-h127.0.0.1", "-P3306", "-uroot", "-p0617",
         "--default-character-set=utf8mb4", "--batch", "--raw", "-N", "finance", "-e"]
ETF_MARKET = "유가증권시장"


def q(sql: str) -> list[list[str]]:
    r = subprocess.run(MYSQL + [sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"MySQL 조회 실패: {r.stderr.strip()}")
    return [line.split("\t") for line in r.stdout.splitlines() if line.strip()]


def copy_into(db, table: str, columns: list[str], rows) -> int:
    """psycopg2 COPY 로 임시테이블에 부은 뒤 본 테이블로 upsert 한다 (146만 행 대응)."""
    tmp = f"tmp_{table}"
    db.execute(text(f"create temp table {tmp} (like {table} including defaults) on commit drop"))
    buf = io.StringIO()
    n = 0
    for row in rows:
        buf.write("\t".join("\\N" if v is None else str(v) for v in row) + "\n")
        n += 1
    buf.seek(0)
    raw = db.connection().connection
    with raw.cursor() as cur:
        cur.copy_expert(f"copy {tmp} ({','.join(columns)}) from stdin with (format text)", buf)
    return n


def main(dry_run: bool, verify_only: bool):
    db = SessionLocal()

    # ---------- 1. 기본정보 ----------
    info = q("""select ISU_SRT_CD, ISU_ABBRV, LIST_DD, delist_dt, IDX_ASST_CLSS_NM, ETF_TOT_FEE
                from etf_kr_krx_basic_info order by ISU_SRT_CD""")
    print(f"기본정보 {len(info):,}종목 (상장폐지 {sum(1 for r in info if r[3] not in ('NULL', ''))}종목 포함)")

    if dry_run:
        px = q("select count(*), count(distinct ISU_SRT_CD), min(base_dt), max(base_dt) from etf_kr_krx_daily_price")[0]
        dv = q("""select count(*) from etf_kr_seibro_dividend d
                  join etf_kr_krx_basic_info b on b.ISU_SRT_CD=d.ticker where d.ESTM_STDPRC > 0""")[0]
        print(f"일별시세 {int(px[0]):,}행 / {px[1]}종목 / {px[2]} ~ {px[3]}")
        print(f"분배금 {int(dv[0]):,}행")
        cur = db.execute(text("select count(*) from instruments where asset_type='etf'")).scalar()
        print(f"\n기존 ETF {cur}종목은 삭제 후 재적재됩니다.")
        print("--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return

    if not verify_only:
        # ---------- 2. 기존 ETF 초기화 ----------
        old = db.execute(text("select count(*) from instruments where asset_type='etf'")).scalar()
        if old:
            db.execute(text("delete from instruments where asset_type='etf'"))  # 자식은 FK cascade
            db.commit()
            print(f"기존 ETF {old}종목 삭제 (자식 테이블 cascade)")

        # ---------- 3. instruments ----------
        db.execute(text("""
            create temp table tmp_inst (ticker varchar(32), name varchar(200)) on commit drop"""))
        buf = io.StringIO()
        for t, name, *_ in info:
            buf.write(f"{t}\t{(name or t).strip()}\n")
        buf.seek(0)
        with db.connection().connection.cursor() as cur:
            cur.copy_expert("copy tmp_inst (ticker,name) from stdin with (format text)", buf)
        db.execute(text(f"""
            insert into instruments (ticker, name, asset_type, market)
            select ticker, name, 'etf', '{ETF_MARKET}' from tmp_inst
            on conflict (ticker) do update set name=excluded.name, asset_type='etf'"""))
        db.commit()
        print(f"instruments {len(info):,}종목 등록")

        # ---------- 4. prices / raw_closes ----------
        print("일별시세 전송 중 ...")
        px = q("""select ISU_SRT_CD, base_dt, TDD_OPNPRC, TDD_HGPRC, TDD_LWPRC, TDD_CLSPRC,
                         ACC_TRDVOL, LIST_SHRS
                  from etf_kr_krx_daily_price order by ISU_SRT_CD, base_dt""")
        db.execute(text("""create temp table tmp_px (ticker varchar(32), d date, o numeric, h numeric,
                           l numeric, c numeric, vol bigint, shrs bigint) on commit drop"""))
        buf = io.StringIO()
        for r in px:
            vals = ["\\N" if v in ("NULL", "") else v for v in r]
            buf.write("\t".join(vals) + "\n")
        buf.seek(0)
        with db.connection().connection.cursor() as cur:
            cur.copy_expert("copy tmp_px from stdin with (format text)", buf)

        db.execute(text("""
            insert into prices (instrument_id, date, period, open, high, low, close, volume, market_cap)
            select i.id, t.d, 'D', nullif(t.o,0), nullif(t.h,0), nullif(t.l,0), t.c, t.vol,
                   case when t.shrs is not null then (t.c * t.shrs)::bigint end
            from tmp_px t join instruments i on i.ticker = t.ticker and i.asset_type='etf'
            where t.c is not null and t.c > 0
            on conflict (instrument_id, date, period) do update set
              open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
              volume=excluded.volume, market_cap=excluded.market_cap"""))
        db.execute(text("""
            insert into raw_closes (instrument_id, date, close)
            select i.id, t.d, t.c from tmp_px t
            join instruments i on i.ticker = t.ticker and i.asset_type='etf'
            where t.c is not null and t.c > 0
            on conflict (instrument_id, date) do update set close=excluded.close"""))
        db.commit()
        print(f"prices / raw_closes {len(px):,}행 적재")

        # ---------- 5. dividends ----------
        dv = q("""select d.ticker, d.RGT_STD_DT, d.TH1_PAY_TERM_BEGIN_DT, d.ESTM_STDPRC
                  from etf_kr_seibro_dividend d join etf_kr_krx_basic_info b on b.ISU_SRT_CD=d.ticker
                  where d.ESTM_STDPRC > 0 and d.RGT_RSN_DTAIL_NM = '이익분배'
                  order by d.ticker, d.RGT_STD_DT""")
        db.execute(text("""create temp table tmp_dv (ticker varchar(32), ex date, pay date,
                           amt numeric) on commit drop"""))
        buf = io.StringIO()
        for r in dv:
            buf.write("\t".join("\\N" if v in ("NULL", "") else v for v in r) + "\n")
        buf.seek(0)
        with db.connection().connection.cursor() as cur:
            cur.copy_expert("copy tmp_dv from stdin with (format text)", buf)
        # 같은 (종목, 배정기준일)에 여러 행이 오면 합산한다 (이익분배 + 청산분배 동시 등)
        db.execute(text("""
            insert into dividends (instrument_id, ex_date, pay_date, amount)
            select i.id, t.ex, max(t.pay), sum(t.amt)
            from tmp_dv t join instruments i on i.ticker = t.ticker and i.asset_type='etf'
            group by i.id, t.ex
            on conflict (instrument_id, ex_date) do update set
              pay_date=excluded.pay_date, amount=excluded.amount"""))
        db.commit()
        print(f"dividends {len(dv):,}행 적재")

        # ---------- 6. 파생 (월봉 + 배당조정) ----------
        ids = [r[0] for r in db.execute(text(
            "select id from instruments where asset_type='etf' order by ticker")).fetchall()]
        print(f"\n파생 계산 {len(ids):,}종목 ...")
        for k, iid in enumerate(ids, 1):
            months = db.execute(text("""select distinct extract(year from date)::int y,
                                        extract(month from date)::int m from prices
                                        where instrument_id=:i and period='D'"""), {"i": iid}).fetchall()
            for y, m in months:
                recompute_monthly_bar(db, iid, y, m)
            recompute_dividend_adjusted(db, iid, force_full=True)
            if k % 100 == 0:
                db.commit()
                print(f"  {k:,}/{len(ids):,}")
        db.commit()
        print("파생 계산 완료")

    # ---------- 7. 검증 ----------
    verify(db)
    db.close()


def verify(db):
    print("\n" + "=" * 78)
    print("검증 — 소스 TR 지수 대비")
    print("=" * 78)

    counts = db.execute(text("""
        select (select count(*) from instruments where asset_type='etf'),
               (select count(*) from prices p join instruments i on i.id=p.instrument_id
                where i.asset_type='etf' and p.period='D'),
               (select count(*) from prices p join instruments i on i.id=p.instrument_id
                where i.asset_type='etf' and p.period='M'),
               (select count(*) from raw_closes r join instruments i on i.id=r.instrument_id
                where i.asset_type='etf'),
               (select count(*) from dividends d join instruments i on i.id=d.instrument_id
                where i.asset_type='etf'),
               (select count(*) from dividend_adjusted_prices a join instruments i on i.id=a.instrument_id
                where i.asset_type='etf' and a.period='D')""")).fetchone()
    print(f"적재 결과: instruments {counts[0]:,} · prices(D) {counts[1]:,} · prices(M) {counts[2]:,} · "
          f"raw_closes {counts[3]:,} · dividends {counts[4]:,} · 배당조정(D) {counts[5]:,}")

    # 배당락일 규약 대조 — 우리가 계산한 반영일 == 소스의 ex_dividend_dt 인가
    src_ex = {(r[0], r[1]): r[2] for r in q("""
        select ticker, RGT_STD_DT, ex_dividend_dt from etf_kr_seibro_dividend
        where ESTM_STDPRC > 0 and ex_dividend_dt is not null""")}
    from datetime import date as _date

    from app.services.derived_prices import _dividend_effective_date
    trading = [r[0] for r in db.execute(text(
        "select distinct date from prices where period='D' order by date")).fetchall()]
    rows = db.execute(text("""select i.ticker, d.ex_date from dividends d
        join instruments i on i.id=d.instrument_id where i.asset_type='etf'""")).fetchall()
    match = mismatch = 0
    samples = []
    for ticker, ex in rows:
        want = src_ex.get((ticker, str(ex)))
        if not want:
            continue
        got = _dividend_effective_date(ex, trading)
        if got == _date.fromisoformat(want):
            match += 1
        else:
            mismatch += 1
            if len(samples) < 5:
                samples.append(f"{ticker} 기준일{ex}: 우리 {got} vs 소스 {want}")
    print(f"\n배당락일 규약: 일치 {match:,} / 불일치 {mismatch:,}")
    for s in samples:
        print(f"  {s}")

    # TR 지수 대조 (소스 분배금 반영이 2025-10-30에서 멈춰 있어 그 이전만 유효)
    print("\nTR 지수 대조 (2025-10-30 이전 구간, 종목별 최대 상대오차)")
    src = q("""select ticker, base_dt, total_return_index from etf_kr_total_return_index
               where base_dt <= '2025-10-30' and total_return_index is not null""")
    by_ticker: dict[str, dict[str, float]] = {}
    for t, d, v in src:
        by_ticker.setdefault(t, {})[d] = float(v)

    ours = db.execute(text("""
        select i.ticker, a.date, a.adj_close from dividend_adjusted_prices a
        join instruments i on i.id=a.instrument_id
        where i.asset_type='etf' and a.period='D' and a.date <= '2025-10-30'""")).fetchall()
    mine: dict[str, dict[str, float]] = {}
    for t, d, v in ours:
        mine.setdefault(t, {})[str(d)] = float(v)

    worst = []
    exact = 0
    for t, series in mine.items():
        ref = by_ticker.get(t)
        if not ref:
            continue
        common = [d for d in series if d in ref]
        if len(common) < 30:
            continue
        # 양쪽 다 시작 100 기준 지수지만 시작일이 다를 수 있어 공통 첫날로 재정규화한다
        base = min(common)
        if series[base] == 0 or ref[base] == 0:
            continue
        err = max(abs((series[d] / series[base]) / (ref[d] / ref[base]) - 1) for d in common)
        worst.append((err, t, len(common)))
        if err < 1e-6:
            exact += 1

    worst.sort(reverse=True)
    print(f"  비교 종목 {len(worst):,}개 · 완전일치(오차<1e-6) {exact:,}개 "
          f"({exact / max(len(worst),1):.1%})")
    for thr, label in ((1e-6, "<0.0001%"), (1e-4, "<0.01%"), (1e-2, "<1%")):
        print(f"    오차 {label:9s}: {sum(1 for e,_,_ in worst if e < thr):,}개")
    print("  오차 상위 10종목:")
    for err, t, n in worst[:10]:
        print(f"    {t}  최대오차 {err:>10.4%}  ({n:,}일 비교)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    a = p.parse_args()
    main(a.dry_run, a.verify_only)
