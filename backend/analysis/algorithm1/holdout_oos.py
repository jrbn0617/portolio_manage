"""알고리즘 #1 표본 외(Out-of-Sample) 검증 — **홀드아웃 1회 개방**.

    ┌──────────────────────────────────────────────────────────────────────┐
    │ 이 스크립트는 봉인 구간(2020-01-01 이전)의 성과를 산출한다.          │
    │ 선등록: docs/algorithms/algorithm1-holdout-prereg.md (2026-08-13 승인)│
    │ 1회만 실행하고, 결과를 보고 파라미터를 바꾸지 않는다.                │
    └──────────────────────────────────────────────────────────────────────┘

성과 구간 2015-08-01 ~ 2019-12-31, 첫 형성일 2015-07-31(성과 구간 직전 리밸런싱).
시작일은 index_memberships 최초 스냅샷에서 온다 — 코스피/코스피200/코스닥은 2015-06-30,
코스닥150은 2015-07-31이라 네 유니버스가 모두 해소되는 첫 시점이 2015-07-31이다.

파라미터는 algorithm1-overview.md 3번 확정 스펙 그대로이며 **단 하나도 바꾸지 않는다.**

판정 (선등록 6절, 데이터 보기 전 확정):
  주 기준 P1 — 각 유니버스에서 전략 샤프 > 벤치마크 샤프 (비용 후)
    4/4 통과 · 2~3개 부분통과 · 1개 이하 실패
  보조 P2 — MDD가 벤치마크보다 얕을 것 (판정에는 안 쓰고 서술에만 반영)
  CAGR 단독 우위는 통과 근거로 쓰지 않는다 (실험 19 전례)

사용법:
  python analysis/algorithm1/holdout_oos.py --json /path/to/out.json
"""
import argparse
import json
import sys
from datetime import date
from functools import partial
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.backtest_service import (  # noqa: E402
    BacktestConfig,
    TransactionCost,
    _compute_metrics,
    compute_free_float_weights,
    compute_regime_exposure,
    run_momentum_backtest,
)
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg  # noqa: E402

# 선등록 3절 — 실행 후 바꾸지 않는다.
OOS_START = date(2015, 8, 1)   # 성과 측정 시작 (첫 형성일은 그 직전 리밸런싱 = 2015-07-31)
OOS_END = date(2019, 12, 31)   # 홀드아웃 경계(2020-01-01) 직전
COST = TransactionCost(sell_tax=0.0020, commission=0.00015)
MAX_AGE_DAYS = 100

UNIVERSES = [("KOSPI", "코스피전체"), ("KOSPI200", "코스피200"),
             ("KOSDAQ", "코스닥전체"), ("KOSDAQ150", "코스닥150")]


def rebase(nav_series, start: date):
    """성과 구간을 start부터로 자르고 100으로 리베이스.

    엔진의 nav_series는 첫 형성일(2015-07-31)부터 시작한다. 그대로 지표를 내면
    형성일 하루가 성과에 섞인다(methodology §1.2).
    """
    pts = [(d, v) for d, v in nav_series if d >= start]
    base = pts[0][1]
    return [(d, v / base * 100) for d, v in pts]


def bm_series(db, ticker: str, days: list[date]) -> list[float]:
    """벤치마크 지수(TR) 를 days 첫날 100 기준으로 환산."""
    rows = (db.query(Price.date, Price.close)
            .join(Instrument, Instrument.id == Price.instrument_id)
            .filter(Instrument.ticker == ticker, Price.period == "D").all())
    by_date = {r.date: float(r.close) for r in rows}
    out, last = [], None
    for d in days:
        last = by_date.get(d, last)
        out.append(last)
    return [v / out[0] * 100 for v in out]


def holding_stats(result) -> tuple[float, float]:
    """리밸런싱별 (편입종목수, 실효N=1/HHI)의 중앙값. 비중 0은 편입이 아니므로 제외."""
    counts, effn = [], []
    for rb in result.rebalances:
        w = [x for x in rb["weights"].values() if x]
        if not w:
            continue
        s = sum(w)
        counts.append(len(w))
        effn.append(1.0 / sum((x / s) ** 2 for x in w))
    if not counts:
        return float("nan"), float("nan")
    counts.sort()
    effn.sort()
    return float(counts[len(counts) // 2]), float(effn[len(effn) // 2])


def run(db, index_name: str, cost):
    """알고리즘 #1 확정 스펙 — algorithm1-overview.md 3번 표와 동일."""
    config = BacktestConfig(
        index_name=index_name, top_n=20, max_per_sector=2,
        sector_count_field="krx_sector", start_date=OOS_START, end_date=OOS_END,
    )
    screen_fn = partial(screen_by_ebitda_peg,
                        config=ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                                            consensus_lag_days=0, peg_min=0.0,
                                            max_age_days=MAX_AGE_DAYS),
                        warn=lambda m: None, info=lambda m: None)
    weight_fn = partial(compute_free_float_weights, max_weight=0.25,
                        group_field="krx_sector", max_group_weight=0.50, min_weight=0.01)
    exposure_fn = partial(compute_regime_exposure, benchmark_ticker=index_name,
                          ma_window_days=200, bull_exposure=1.0, bear_exposure=0.5)
    return run_momentum_backtest(
        db, config, on_warning=lambda m: None, on_info=lambda m: None,
        screen_fn=screen_fn, weight_fn=weight_fn, exposure_fn=exposure_fn,
        stop_loss_pct=0.10, stop_loss_execution="next_open", stop_loss_mode="cash",
        idle_mode="cash", cost=cost,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    db = SessionLocal()
    rows = []
    try:
        for index_name, label in UNIVERSES:
            print(f"{label} 실행 중...", flush=True)
            gross_r = run(db, index_name, cost=None)
            net_r = run(db, index_name, cost=COST)
            gross = rebase(gross_r.nav_series, OOS_START)
            net = rebase(net_r.nav_series, OOS_START)
            days = [d for d, _ in net]
            bm = list(zip(days, bm_series(db, index_name, days)))

            g, n, b = _compute_metrics(gross), _compute_metrics(net), _compute_metrics(bm)
            n_hold, eff_n = holding_stats(net_r)
            cs = net_r.cost_stats or {}
            rows.append(dict(
                유니버스=label,
                첫형성일=str(net_r.nav_series[0][0]), 성과시작=str(days[0]), 성과종료=str(days[-1]),
                리밸런싱=cs.get("rebalances"),
                CAGR_전=g["cagr"], CAGR_후=n["cagr"],
                변동성=n["annualized_volatility"], MDD=n["mdd"],
                샤프_전=g["sharpe"], 샤프_후=n["sharpe"],
                BM_CAGR=b["cagr"], BM_샤프=b["sharpe"], BM_MDD=b["mdd"],
                P1=n["sharpe"] > b["sharpe"], P2=n["mdd"] > b["mdd"],
                회전율=cs.get("avg_turnover"),
                손절발동=(net_r.stop_loss_stats or {}).get("trigger_rate"),
                편입종목=n_hold, 실효N=eff_n,
            ))
    finally:
        db.close()

    print("\n" + "=" * 100)
    print(f"알고리즘 #1 표본 외 검증 — 성과구간 {OOS_START} ~ {OOS_END} (홀드아웃 1회 개방)")
    print(f"선등록: docs/algorithms/algorithm1-holdout-prereg.md\n")
    print(f"{'유니버스':10s} {'CAGR(후)':>9s} {'변동성':>7s} {'MDD':>8s} {'샤프':>7s} │ "
          f"{'BM CAGR':>8s} {'BM 샤프':>7s} {'BM MDD':>8s} │ {'P1':>3s} {'P2':>3s} "
          f"{'회전':>5s} {'종목':>4s} {'실효N':>5s}")
    for r in rows:
        print(f"{r['유니버스']:10s} {r['CAGR_후']:>+8.2%} {r['변동성']:>7.1%} {r['MDD']:>8.1%} "
              f"{r['샤프_후']:>7.3f} │ {r['BM_CAGR']:>+8.2%} {r['BM_샤프']:>7.3f} "
              f"{r['BM_MDD']:>8.1%} │ {'O' if r['P1'] else 'X':>3s} {'O' if r['P2'] else 'X':>3s} "
              f"{r['회전율']:>4.0%} {r['편입종목']:>4.0f} {r['실효N']:>5.1f}")

    n_pass = sum(1 for r in rows if r["P1"])
    verdict = "통과" if n_pass == 4 else ("부분 통과" if n_pass >= 2 else "실패")
    print(f"\n=== 판정 (선등록 6절) ===")
    print(f"  P1(전략 샤프 > BM 샤프) {n_pass}/4 충족 → **{verdict}**")
    print(f"  P2(MDD가 BM보다 얕음) {sum(1 for r in rows if r['P2'])}/4 — 서술에만 반영")

    if args.json:
        args.json.write_text(json.dumps(
            dict(prereg="docs/algorithms/algorithm1-holdout-prereg.md",
                 oos_start=str(OOS_START), oos_end=str(OOS_END),
                 p1_pass=n_pass, verdict=verdict, rows=rows),
            ensure_ascii=False, indent=2, default=str))
        print(f"\n저장: {args.json}")


if __name__ == "__main__":
    main()
