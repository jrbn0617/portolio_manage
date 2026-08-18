"""손실 제한 폭 8% vs 현행 10% — 4개 유니버스 비교. 읽기 전용.

실험 22(워크포워드)가 "현행 10%는 9폴드 중 1위 0회·꼴찌 8회"로 지적했으나, 그 실험은
**코스피전체 단일 유니버스·샤프 단일 기준**이라는 한계가 있었다. 이 스크립트는 그 한계를
메워 4개 유니버스로 확인한다.

**판정 기준 (데이터 보기 전 고정, methodology §1.1)**
  신호  = 4개 유니버스 **전부**에서 샤프가 개선되고 MDD가 악화되지 않을 것
  노이즈 = 유니버스마다 부호가 갈리면 기각
  CAGR 단독 상승은 채택 근거로 쓰지 않는다 (실험 19 전례)

구간은 4개로 나눠 본다 — 전 구간 / 보고서 구간 / 검증 전용 / 설정값 선택.
DB 쓰기·외부 호출 없음.
"""
import json
import os
import sys
from datetime import date
from functools import partial
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest_service import (  # noqa: E402
    BacktestConfig, TransactionCost, compute_free_float_weights,
    compute_regime_exposure, run_momentum_backtest,
)
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg  # noqa: E402

# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
SP = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)
START, END = date(2015, 8, 1), date(2026, 7, 31)
UNIVERSES = [("KOSPI", "코스피전체"), ("KOSPI200", "코스피200"),
             ("KOSDAQ", "코스닥전체"), ("KOSDAQ150", "코스닥150")]
STOPS = [0.08, 0.10]
WINDOWS = [("전 구간", date(2015, 8, 1), END),
           ("보고서 구간", date(2017, 5, 22), END),
           ("검증 전용", date(2017, 5, 22), date(2019, 12, 30)),
           ("설정값 선택", date(2020, 1, 2), END)]


def run(index, stop):
    db = SessionLocal()
    r = run_momentum_backtest(
        db, BacktestConfig(index_name=index, top_n=20, max_per_sector=2,
                           sector_count_field="krx_sector", start_date=START, end_date=END),
        on_warning=lambda m: None, on_info=lambda m: None,
        screen_fn=partial(screen_by_ebitda_peg,
                          config=ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                                              consensus_lag_days=0, peg_min=0.0, max_age_days=100),
                          warn=lambda m: None, info=lambda m: None),
        weight_fn=partial(compute_free_float_weights, max_weight=0.25, group_field="krx_sector",
                          max_group_weight=0.50, min_weight=0.01),
        exposure_fn=partial(compute_regime_exposure, benchmark_ticker=index,
                            ma_window_days=200, bull_exposure=1.0, bear_exposure=0.5),
        stop_loss_pct=stop, stop_loss_execution="next_open", stop_loss_mode="cash",
        cost=TransactionCost(sell_tax=0.0020, commission=0.00015))
    db.close()
    return r


def metrics(nav, a, b):
    """구간을 자르고 **시작점으로 rebase** (methodology §1.2)."""
    pts = [(d, v) for d, v in nav if a <= d <= b]
    if len(pts) < 30:
        return None
    vals = np.array([v / pts[0][1] for _, v in pts])
    ret = np.diff(vals) / vals[:-1]
    vol = float(ret.std(ddof=1) * np.sqrt(252))
    yrs = (pts[-1][0] - pts[0][0]).days / 365.25
    return dict(cagr=float(vals[-1] ** (1 / yrs) - 1), vol=vol,
                sharpe=float(ret.mean() * 252 / vol) if vol else 0.0,
                mdd=float((vals / np.maximum.accumulate(vals) - 1).min()),
                cum=float(vals[-1] - 1))


res = {}
for idx, name in UNIVERSES:
    for s in STOPS:
        print(f"  실행 {name} · 손절 {s:.0%} …", flush=True)
        r = run(idx, s)
        sl, cs = r.stop_loss_stats, r.cost_stats
        res[(name, s)] = dict(
            windows={w: metrics(r.nav_series, a, b) for w, a, b in WINDOWS},
            triggered=sl["triggered"], positions=sl["positions"],
            trigger_rate=sl["trigger_rate"], idle_ratio=sl["idle_ratio"],
            turnover=cs["avg_turnover"], cost_pt=cs["total_cost"],
            stop_cost=cs["stop_cost"], rebal_cost=cs["rebalance_cost"])

print("\n" + "=" * 96)
for wname, _, _ in WINDOWS:
    print(f"\n■ {wname}")
    print(f"  {'유니버스':<11}{'CAGR 8%':>10}{'CAGR 10%':>10}{'  ':>2}"
          f"{'샤프 8%':>9}{'샤프 10%':>9}{'Δ샤프':>9}{'  ':>2}"
          f"{'MDD 8%':>9}{'MDD 10%':>9}{'ΔMDD':>9}")
    for _, name in UNIVERSES:
        a, b = res[(name, 0.08)]["windows"][wname], res[(name, 0.10)]["windows"][wname]
        if not a or not b:
            continue
        ds, dm = a["sharpe"] - b["sharpe"], abs(b["mdd"]) - abs(a["mdd"])
        print(f"  {name:<11}{a['cagr']:>+10.2%}{b['cagr']:>+10.2%}{'':>2}"
              f"{a['sharpe']:>9.3f}{b['sharpe']:>9.3f}{ds:>+9.3f}{'':>2}"
              f"{a['mdd']:>9.1%}{b['mdd']:>9.1%}{dm:>+9.1%}")

print("\n" + "=" * 96)
print("■ 판정 (기준: 4개 유니버스 전부 샤프 개선 + MDD 비악화)\n")
for wname, _, _ in WINDOWS:
    ds = [res[(n, 0.08)]["windows"][wname]["sharpe"] - res[(n, 0.10)]["windows"][wname]["sharpe"]
          for _, n in UNIVERSES if res[(n, 0.08)]["windows"][wname]]
    dm = [abs(res[(n, 0.10)]["windows"][wname]["mdd"]) - abs(res[(n, 0.08)]["windows"][wname]["mdd"])
          for _, n in UNIVERSES if res[(n, 0.08)]["windows"][wname]]
    ns, nm = sum(1 for x in ds if x > 0), sum(1 for x in dm if x >= 0)
    v = "신호 — 8% 우위" if (ns == 4 and nm == 4) else ("노이즈 — 부호 갈림" if 0 < ns < 4 else "10% 우위")
    print(f"  {wname:<12} 샤프 개선 {ns}/4 · MDD 비악화 {nm}/4  →  {v}")

print("\n■ 운용 통계 (전 구간)")
print(f"  {'유니버스':<11}{'손절 8%':>10}{'손절 10%':>10}{'회전 8%':>9}{'회전 10%':>9}"
      f"{'비용 8%':>9}{'비용 10%':>9}")
for _, name in UNIVERSES:
    a, b = res[(name, 0.08)], res[(name, 0.10)]
    print(f"  {name:<11}{a['triggered']:>10,}{b['triggered']:>10,}"
          f"{a['turnover']:>9.1%}{b['turnover']:>9.1%}{a['cost_pt']:>9.1f}{b['cost_pt']:>9.1f}")

(SP / "stop8v10.json").write_text(json.dumps(
    {f"{n}|{s}": v for (n, s), v in res.items()}, ensure_ascii=False, indent=2, default=str))
print(f"\n저장: {SP/'stop8v10.json'}")
