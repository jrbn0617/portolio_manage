"""코스피 전체 유니버스의 리밸런싱 시점별 스크리닝 깔때기를 연도별로 집계.

읽기 전용 진단이다. 성과(NAV/수익률)를 산출하지 않으므로 홀드아웃 추가 소비가 아니다.
확정 스펙과 동일한 ScreenConfig를 쓴다.

단계:
  유니버스(지수편입) → 거래정지·스팩 제외 → PEG 3지표 보유(신선도 100일 이내)
  → PEG>0 → 섹터별 하위 50% 통과 → 모멘텀 계산가능 → 섹터당 2종목 제한 후 상위 20
"""
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path("/Users/mckim/Documents/GitHub/portolio_manage/backend")
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest_service import (  # noqa: E402
    _last_trading_day_of_month,
    _shift_month,
    compute_momentum,
    find_halted_instruments,
    resolve_universe,
)
from app.services.factor_screen_service import (  # noqa: E402
    FWD_METRIC,
    MULTIPLE_METRIC,
    TTM_METRIC,
    ScreenConfig,
    _build_metric_index,
    _latest_at_or_before,
    screen_by_ebitda_peg,
)

INDEX = "KOSPI"
CFG = ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                   consensus_lag_days=0, peg_min=0.0, max_age_days=100)

db = SessionLocal()

dates = []
y, m = 2015, 7
while (y, m) <= (2026, 7):
    dates.append(_last_trading_day_of_month(db, y, m))
    y, m = _shift_month(y, m, 1)

agg = defaultdict(lambda: defaultdict(list))
print(f"{'형성일':12s} {'유니버스':>7s} {'거래가능':>7s} {'3지표보유':>8s} {'PEG>0':>7s} "
      f"{'섹터하위50':>9s} {'모멘텀가능':>8s}", flush=True)

for d in dates:
    universe = resolve_universe(db, INDEX, d)
    if not universe:
        continue
    halted = find_halted_instruments(db, universe, d, 10)
    tradable = [i for i in universe if i not in halted]

    ttm_idx = _build_metric_index(db, tradable, TTM_METRIC)
    fwd_idx = _build_metric_index(db, tradable, FWD_METRIC)
    mult_idx = _build_metric_index(db, tradable, MULTIPLE_METRIC)
    ttm_as_of = d - timedelta(days=CFG.ttm_lag_days)

    have3 = peg_pos = 0
    for iid in tradable:
        ttm = _latest_at_or_before(ttm_idx, iid, ttm_as_of, CFG.max_age_days)
        fwd = _latest_at_or_before(fwd_idx, iid, d, CFG.max_age_days)
        mult = _latest_at_or_before(mult_idx, iid, d, CFG.max_age_days)
        if ttm is None or fwd is None or mult is None or ttm == 0 or fwd <= 0:
            continue
        have3 += 1
        growth = (fwd - ttm) / abs(ttm)
        if growth == 0 or mult / growth <= CFG.peg_min:
            continue
        peg_pos += 1

    passed = screen_by_ebitda_peg(db, tradable, d, config=CFG,
                                  warn=lambda m: None, info=lambda m: None)
    mom = compute_momentum(db, passed, d)
    rankable = sum(1 for v in mom.values() if v is not None)

    row = (len(universe), len(tradable), have3, peg_pos, len(passed), rankable)
    print(f"{str(d):12s} {row[0]:>7d} {row[1]:>7d} {row[2]:>8d} {row[3]:>7d} "
          f"{row[4]:>9d} {row[5]:>8d}", flush=True)
    for k, v in zip(("universe", "tradable", "have3", "peg_pos", "passed", "rankable"), row):
        agg[d.year][k].append(v)

print("\n" + "=" * 78)
print("연도별 평균 (코스피 전체)")
print(f"{'연도':6s} {'월수':>4s} {'유니버스':>8s} {'거래가능':>8s} {'3지표보유':>9s} "
      f"{'PEG>0':>7s} {'섹터하위50':>10s} {'모멘텀가능':>9s}  {'3지표/거래가능':>12s}")
for yr in sorted(agg):
    a = agg[yr]
    n = len(a["universe"])
    avg = {k: sum(v) / len(v) for k, v in a.items()}
    print(f"{yr:6d} {n:>4d} {avg['universe']:>8.0f} {avg['tradable']:>8.0f} "
          f"{avg['have3']:>9.0f} {avg['peg_pos']:>7.0f} {avg['passed']:>10.0f} "
          f"{avg['rankable']:>9.0f}  {avg['have3']/avg['tradable']:>11.1%}")
db.close()
