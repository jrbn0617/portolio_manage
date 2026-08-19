"""수정기준가 재계산 — fund_adjusted_navs.

결산·분배로 기준가가 리셋된 지점을 이어붙여 총수익 계열을 만든다. 주식·ETF 의
dividend_adjusted_prices 와 같은 역할이다.

    adj_factor = 누적곱(결산전 기준가 / 결산후 기준가)      # ex_dividend_dt 기준
    adj_nav    = nav × adj_factor × 0.1

**0.1 은 단위 환산이 아니라 설정 시 100 기준 리베이스**다(기준가는 설정 시 1,000원).
결산 이력이 없는 펀드에서 adj_nav/nav = 0.100000 으로 확인했다.

price_fetcher 의 FundKrAdjustedNAVCalculatorByFundCode 를 그대로 옮겼다. 소스의
fund_kr_adjusted_nav 와 대조해야 하므로 아래 세부를 **의도적으로 똑같이** 맞췄다.
  - settlement_type 은 '결산'·'분배' 만. '상환'은 post_settlement_nav 가 0 이라
    나누면 무한대가 된다(적재된 1,102건). '배당' 2건도 원본이 제외하므로 뺀다.
  - 같은 ex_dividend_dt 에 여러 건이면 **마지막 것만** 남긴다.
  - 수정계수를 기준가 날짜에 reindex 하므로, **거래일이 아닌 락일의 결산은 반영되지
    않는다**. 원본 동작이며 대조를 위해 유지한다.
  - 결산 목록은 마지막 기준가 날짜까지만 쓴다.

사용법:
  python scripts/compute_fund_adjusted_navs.py [--dry-run] [--limit N] [--no-resume]
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from psycopg2.extras import execute_values  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal, engine  # noqa: E402

FUND_CHUNK = 300
REBASE = 0.1
SETTLEMENT_TYPES = ("결산", "분배")

# 컬럼 한도 — 소스(price_fetcher)와 같은 정밀도를 쓰기로 했으므로 넘는 행은 버린다.
#   adj_factor Numeric(15,12) → 1,000 미만
#   adj_nav    Numeric(18,12) → 1,000,000 미만
# 소스의 by_date 계산기도 같은 값으로 걸러낸다(adj_factor >= 1000 이상치 제거, limit 10**6).
# 버려지는 것은 두 부류다.
#   ① 원본을 대규모 분배한 부동산·특별자산 펀드 (nav 484.88 → post 1.16 이면 factor 418)
#   ② 기준가 자체가 큰 펀드 (최대 9,758만 → ×0.1 만 해도 한도를 넘는다)
MAX_FACTOR = 1000
MAX_ADJ_NAV = 10 ** 6

UPSERT = """
INSERT INTO fund_adjusted_navs (fund_id, base_dt, nav, adj_nav, adj_factor)
VALUES %s
ON CONFLICT (fund_id, base_dt) DO UPDATE
SET nav = EXCLUDED.nav, adj_nav = EXCLUDED.adj_nav,
    adj_factor = EXCLUDED.adj_factor, updated_at = now()
"""


def compute_one(navs: pd.DataFrame, stl: pd.DataFrame) -> pd.DataFrame:
    """navs: index=base_dt, col nav (정렬됨) / stl: index=ex_dividend_dt, cols nav, post"""
    df = navs.copy()
    if stl.empty:
        df["adj_factor"] = 1.0
    else:
        s = stl[stl.index <= df.index[-1]]
        s = s[(s["post"] > 0) & s["nav"].notna()]
        if s.empty:
            df["adj_factor"] = 1.0
        else:
            factor = (s["nav"] / s["post"])
            factor = factor[~factor.index.duplicated(keep="last")]
            df["adj_factor"] = factor.reindex(df.index).fillna(1.0).cumprod()
    df["adj_nav"] = df["nav"] * df["adj_factor"] * REBASE
    return df


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-resume", action="store_true")
    a = p.parse_args()

    db = SessionLocal()
    funds = {r[0]: r[1] for r in db.execute(text(
        "SELECT id, fund_code FROM funds WHERE id IN (SELECT DISTINCT fund_id FROM fund_navs)"))}
    done = set() if a.no_resume else {
        r[0] for r in db.execute(text("SELECT DISTINCT fund_id FROM fund_adjusted_navs"))}
    todo = sorted(fid for fid in funds if fid not in done)
    if a.limit:
        todo = todo[:a.limit]
    print(f"기준가 있는 펀드 {len(funds):,} · 이미 계산 {len(done):,} · 이번에 {len(todo):,}")
    if not todo:
        db.close()
        return

    raw = engine.raw_connection()
    total, no_stl, t0 = 0, 0, time.time()
    dropped_rows, dropped_funds = 0, set()
    try:
        for i in range(0, len(todo), FUND_CHUNK):
            ids = todo[i:i + FUND_CHUNK]
            ph = ",".join(str(x) for x in ids)
            nav_df = pd.DataFrame(db.execute(text(
                f"SELECT fund_id, base_dt, nav FROM fund_navs WHERE fund_id IN ({ph}) "
                f"ORDER BY fund_id, base_dt")).all(), columns=["fund_id", "base_dt", "nav"])
            stl_df = pd.DataFrame(db.execute(text(
                f"""SELECT fund_id, ex_dividend_dt, nav, post_settlement_nav
                    FROM fund_settlements WHERE fund_id IN ({ph})
                      AND settlement_type IN :types AND ex_dividend_dt IS NOT NULL
                    ORDER BY fund_id, period_end_value""").bindparams(types=SETTLEMENT_TYPES)).all(),
                columns=["fund_id", "ex_dividend_dt", "nav", "post"])
            nav_df["nav"] = nav_df["nav"].astype(float)
            if not stl_df.empty:
                stl_df["nav"] = stl_df["nav"].astype(float)
                stl_df["post"] = stl_df["post"].astype(float)
            stl_by = dict(tuple(stl_df.groupby("fund_id"))) if not stl_df.empty else {}

            values = []
            for fid, g in nav_df.groupby("fund_id"):
                navs = g.set_index("base_dt")[["nav"]]
                s = stl_by.get(fid)
                stl = (s.set_index("ex_dividend_dt")[["nav", "post"]] if s is not None
                       else pd.DataFrame(columns=["nav", "post"]))
                if s is None:
                    no_stl += 1
                out = compute_one(navs, stl)
                over = (out["adj_factor"].abs() >= MAX_FACTOR) | (out["adj_nav"].abs() >= MAX_ADJ_NAV)
                if over.any():
                    dropped_rows += int(over.sum())
                    dropped_funds.add(fid)
                    out = out[~over]
                # psycopg2 는 numpy 스칼라를 못 넘기므로 파이썬 float 로 되돌린다
                values.extend((int(fid), d, round(float(r.nav), 4), round(float(r.adj_nav), 12),
                               round(float(r.adj_factor), 12)) for d, r in out.iterrows())
            if values and not a.dry_run:
                with raw.cursor() as cur:
                    execute_values(cur, UPSERT, values, page_size=5000)
                raw.commit()
            total += len(values)
            el = time.time() - t0
            print(f"  [{(i+len(ids))/len(todo)*100:5.1f}%] 펀드 {i+len(ids):,}/{len(todo):,} · "
                  f"{total:,}행 · {total/el if el else 0:,.0f}행/s · {el/60:.1f}분", flush=True)
    finally:
        raw.close()
        db.close()
    print(f"\n{'(dry-run) ' if a.dry_run else ''}완료: {total:,}행 · 결산이력 없는 펀드 "
          f"{no_stl:,} · {(time.time()-t0)/60:.1f}분")
    if dropped_rows:
        print(f"  한도 초과로 제외: {dropped_rows:,}행 · {len(dropped_funds):,}펀드 "
              f"(adj_factor>={MAX_FACTOR} 또는 adj_nav>={MAX_ADJ_NAV:,})")


if __name__ == "__main__":
    main()
