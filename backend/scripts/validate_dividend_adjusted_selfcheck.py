"""dividend_adjusted_prices를 독립 재구현으로 대조 검증한다. **읽기 전용** — DB에 쓰지 않는다.

기존 `validate_dividend_adjusted_full.py`는 DataGuide 참조파일(수정주가수익률 xlsx)과
비교하는데, 그 파일은 `reference/`(gitignore)에 있어 PC가 바뀌면 못 쓴다. 이 스크립트는
**외부 파일 없이** app/services/derived_prices.py 의 점화식을 pandas로 다시 구현해
DB에 적재된 값과 일 단위로 맞춰본다.

    idx_t = idx_{t-1} x (close_t / close_{t-1}) x PROD(1 + 배당금 / 실제종가)

배당 반영일은 `_dividend_effective_date`와 동일하게
"배정기준일 이하 마지막 거래일에서 1거래일 전"이고, 배당수익률의 분모는 그 날의
raw_closes(비조정 실제종가)다.

**레벨이 아니라 일별 배수(ratio)를 비교한다.** 지수는 최초 가격일 100에서 누적되므로
레벨을 맞추려면 전 이력을 다 끌어와야 하지만, 배수는 앵커와 무관하게 매 스텝을 독립
검증한다 — 오히려 이쪽이 재계산 검증에 정확하다.

검사 항목
  1. 행 커버리지    prices(D) 대비 dividend_adjusted_prices 누락/고아
  2. 값 위생        NULL·0·음수
  3. 일별 배수 일치  |DB배수 - 재계산배수| / 재계산배수 > TOL 인 날
  4. 배당 반영       배당이 실제로 배수에 반영됐는지(무배당일 배수 = 순수 가격배수)

사용법:
  python scripts/validate_dividend_adjusted_selfcheck.py                     # 2014-01-01~
  python scripts/validate_dividend_adjusted_selfcheck.py --from 2019-01-01
  python scripts/validate_dividend_adjusted_selfcheck.py --limit 300         # 빠른 표본검사
"""
import argparse
import bisect
import datetime
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402

# 상대오차 허용치. adj_close가 소수 4자리로 반올림 저장되므로 지수 레벨이 낮은 종목
# (대폭락 후 1 미만)에서는 반올림만으로 1e-4 수준이 날 수 있다. 그보다 넉넉히 잡는다.
TOL = 5e-5
BATCH = 400
EX_DATE_SETTLEMENT_OFFSET = 1


def effective_date(ex_date, daily_dates):
    """배정기준일 -> 실제 배당락 반영 거래일 (derived_prices._dividend_effective_date와 동일)."""
    record_idx = bisect.bisect_right(daily_dates, ex_date) - 1
    if record_idx < 0:
        return None
    idx = record_idx - EX_DATE_SETTLEMENT_OFFSET
    return daily_dates[idx] if idx >= 0 else None


def main(start, limit, out_csv):
    db = SessionLocal()
    ids = [r[0] for r in db.execute(text("""
        select distinct p.instrument_id from prices p join instruments i on i.id = p.instrument_id
        where p.period='D' and i.asset_type='stock' order by 1""")).fetchall()]
    if limit:
        ids = ids[:limit]
    print(f"검증 대상 {len(ids):,}종목 · 시작일 {start}\n")

    stat = defaultdict(int)
    bad_rows, worst = [], []
    n_batches = (len(ids) + BATCH - 1) // BATCH

    for b in range(n_batches):
        chunk = ids[b * BATCH:(b + 1) * BATCH]
        params = {"ids": tuple(chunk), "s": start}
        px = pd.read_sql(text("""select instrument_id iid, date, close::float close from prices
            where period='D' and instrument_id in :ids order by instrument_id, date"""), db.bind,
            params={"ids": tuple(chunk)})
        adj = pd.read_sql(text("""select instrument_id iid, date, adj_close::float adj
            from dividend_adjusted_prices where period='D' and instrument_id in :ids"""), db.bind,
            params={"ids": tuple(chunk)})
        raw = pd.read_sql(text("""select instrument_id iid, date, close::float rc from raw_closes
            where instrument_id in :ids"""), db.bind, params={"ids": tuple(chunk)})
        div = pd.read_sql(text("""select instrument_id iid, ex_date, amount::float amount
            from dividends where instrument_id in :ids and amount > 0"""), db.bind,
            params={"ids": tuple(chunk)})

        adj_map = {(r.iid, r.date): r.adj for r in adj.itertuples()}
        raw_map = {(r.iid, r.date): r.rc for r in raw.itertuples()}
        div_by_iid = defaultdict(list)
        for r in div.itertuples():
            div_by_iid[r.iid].append((r.ex_date, r.amount))

        for iid, g in px.groupby("iid", sort=False):
            dates = list(g["date"])
            closes = list(g["close"])
            stat["price_rows"] += len(dates)

            # 배당 -> 반영일별 (금액, 그날 실제종가)
            eff = defaultdict(list)
            for ex, amt in div_by_iid.get(iid, []):
                d = effective_date(ex, dates)
                if d is None:
                    stat["div_before_history"] += 1
                    continue
                eff[d].append((amt, raw_map.get((iid, d))))
                stat["div_applied"] += 1

            for i, d in enumerate(dates):
                a = adj_map.get((iid, d))
                if a is None:
                    stat["missing_adj"] += 1
                    continue
                if a <= 0:
                    stat["nonpositive_adj"] += 1
                if d < start or i == 0:
                    continue
                pa = adj_map.get((iid, dates[i - 1]))
                pc = closes[i - 1]
                if pa is None or not pa or not pc:
                    stat["skip_no_prev"] += 1
                    continue
                f = 1.0
                for amt, rc in eff.get(d, []):
                    basis = rc if rc is not None else closes[i]
                    if basis:
                        f *= 1 + amt / basis
                expect = (closes[i] / pc) * f
                actual = a / pa
                stat["checked"] += 1
                rel = abs(actual - expect) / expect if expect else 0.0
                if rel > TOL:
                    stat["mismatch"] += 1
                    bad_rows.append(dict(instrument_id=iid, date=d, db_ratio=actual,
                                         expect_ratio=expect, rel_diff=rel,
                                         has_div=bool(eff.get(d))))
                worst.append(rel)
        print(f"  배치 {b+1}/{n_batches}  누적검사 {stat['checked']:,}건 · 불일치 {stat['mismatch']:,}건",
              flush=True)

    orphan = db.execute(text("""select count(*) from dividend_adjusted_prices d
        where d.period='D' and not exists (select 1 from prices p where p.instrument_id=d.instrument_id
        and p.period='D' and p.date=d.date)""")).scalar()

    print("\n" + "=" * 74)
    print("배당조정 지수 자체검증 결과")
    print("=" * 74)
    print(f"  prices(D) 행            {stat['price_rows']:>12,}")
    print(f"  배당조정 누락            {stat['missing_adj']:>12,}")
    print(f"  고아 행(가격 없음)       {orphan:>12,}")
    print(f"  0 이하 지수값            {stat['nonpositive_adj']:>12,}")
    print(f"  적용된 배당              {stat['div_applied']:>12,}   (이력 이전이라 미적용 {stat['div_before_history']:,})")
    print(f"  배수 검사                {stat['checked']:>12,}")
    print(f"  허용치({TOL:.0e}) 초과      {stat['mismatch']:>12,}  ({stat['mismatch']/max(stat['checked'],1):.6%})")

    if worst:
        s = pd.Series(worst)
        print("\n  상대오차 분위수")
        for q in (0.5, 0.9, 0.99, 0.999, 1.0):
            print(f"    p{q*100:>6.1f}  {s.quantile(q):.3e}")

    if bad_rows:
        bad = pd.DataFrame(bad_rows).sort_values("rel_diff", ascending=False)
        names = pd.read_sql(text("select id instrument_id, ticker, name from instruments"), db.bind)
        bad = bad.merge(names, on="instrument_id", how="left")
        out = REPO_DIR / "reference" / out_csv
        bad.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n  불일치 상세 -> {out}")
        print(f"  배당일에 발생한 불일치 {int(bad['has_div'].sum()):,}건 / 무배당일 {int((~bad['has_div']).sum()):,}건")
        pd.set_option("display.width", 200)
        print("\n  상위 15건")
        print(bad.head(15)[["ticker", "name", "date", "db_ratio", "expect_ratio",
                            "rel_diff", "has_div"]].to_string(index=False))
    else:
        print("\n  불일치 없음 — 적재된 지수가 점화식과 완전히 일치합니다.")
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", type=datetime.date.fromisoformat,
                   default=datetime.date(2014, 1, 1))
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", default="배당조정_자체검증_불일치.csv")
    a = p.parse_args()
    main(a.start, a.limit, a.out)
