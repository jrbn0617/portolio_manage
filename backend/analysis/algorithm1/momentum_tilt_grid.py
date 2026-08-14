"""실험#20 — 최종 비중에 모멘텀을 λ만큼 섞는다. **선택 구간(2020~) 탐색 전용.**

    w = (1-λ) x 유동시총비중 + λ x 모멘텀스코어비중   → 기존 캡 파이프라인 통과

스코어 3안(raw/log/rank) x λ 격자를 코스피 전체에서 비교한다. 방향성 판단용이며,
**여기서 고른 설정 1개만** 나중에 2015~2019에서 1회 검증한다(선등록 필요).

홀드아웃: 성과 구간은 2020-01-01부터다. 이 스크립트는 봉인 구간을 열지 않는다.

사용법:
  python analysis/algorithm1/momentum_tilt_grid.py --json out.json
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
    BacktestConfig, TransactionCost, _compute_metrics, compute_free_float_weights,
    compute_regime_exposure, run_momentum_backtest,
)
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg  # noqa: E402

HOLDOUT_START = date(2020, 1, 1)  # 성과 구간 시작 — 이 이전은 열지 않는다
END = date(2026, 7, 31)
COST = TransactionCost(sell_tax=0.0020, commission=0.00015)
MAX_AGE_DAYS = 100
INDEX = "KOSPI"

LAMBDAS = [0.0, 0.15, 0.30, 0.50, 0.75, 1.00]
SCORES = ["raw", "log", "rank"]

# 회귀 확인 — λ=0은 확정 스펙(2026-08-13 재실행값)과 일치해야 한다
BASE = dict(cagr=0.3834, sharpe=1.513, mdd=-0.249)


def rebase(nav, start):
    """성과 구간을 start부터 잘라 100 리베이스. nav_series는 첫 형성일부터 시작한다."""
    pts = [(d, v) for d, v in nav if d >= start]
    return [(d, v / pts[0][1] * 100) for d, v in pts]


def holding_stats(result):
    counts, effn, top3 = [], [], []
    for rb in result.rebalances:
        w = [x for x in rb["weights"].values() if x]
        if not w:
            continue
        s = sum(w)
        counts.append(len(w))
        effn.append(1.0 / sum((x / s) ** 2 for x in w))
        top3.append(sum(sorted(w, reverse=True)[:3]) / s)
    if not counts:
        return float("nan"), float("nan"), float("nan")
    med = lambda a: sorted(a)[len(a) // 2]  # noqa: E731
    return float(med(counts)), float(med(effn)), float(med(top3))


def run(db, lam, score):
    config = BacktestConfig(index_name=INDEX, top_n=20, max_per_sector=2,
                            sector_count_field="krx_sector",
                            start_date=HOLDOUT_START, end_date=END)
    screen_fn = partial(screen_by_ebitda_peg,
                        config=ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                                            consensus_lag_days=0, peg_min=0.0,
                                            max_age_days=MAX_AGE_DAYS),
                        warn=lambda m: None, info=lambda m: None)
    weight_fn = partial(compute_free_float_weights, max_weight=0.25, group_field="krx_sector",
                        max_group_weight=0.50, min_weight=0.01,
                        momentum_tilt=lam, tilt_score=score)
    exposure_fn = partial(compute_regime_exposure, benchmark_ticker=INDEX, ma_window_days=200,
                          bull_exposure=1.0, bear_exposure=0.5)
    return run_momentum_backtest(db, config, on_warning=lambda m: None, on_info=lambda m: None,
                                 screen_fn=screen_fn, weight_fn=weight_fn, exposure_fn=exposure_fn,
                                 stop_loss_pct=0.10, stop_loss_execution="next_open",
                                 stop_loss_mode="cash", idle_mode="cash", cost=COST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    db = SessionLocal()
    rows = []
    try:
        # 벤치마크
        bm_rows = (db.query(Price.date, Price.close).join(Instrument, Instrument.id == Price.instrument_id)
                   .filter(Instrument.ticker == INDEX, Price.period == "D",
                           Price.date.between(HOLDOUT_START, END)).order_by(Price.date).all())
        bm = _compute_metrics([(r[0], float(r[1])) for r in bm_rows])

        for score in SCORES:
            for lam in LAMBDAS:
                if lam == 0.0 and score != SCORES[0]:
                    continue  # λ=0은 스코어와 무관 — 한 번만 돈다
                r = run(db, lam, score)
                m = _compute_metrics(rebase(r.nav_series, HOLDOUT_START))
                n_hold, eff_n, top3 = holding_stats(r)
                cs = r.cost_stats or {}
                label = "현행(λ=0)" if lam == 0 else f"{score} λ={lam:.2f}"
                rows.append(dict(score=("-" if lam == 0 else score), lam=lam, label=label,
                                 cagr=m["cagr"], vol=m["annualized_volatility"], mdd=m["mdd"],
                                 sharpe=m["sharpe"], turnover=cs.get("avg_turnover"),
                                 n_hold=n_hold, eff_n=eff_n, top3=top3,
                                 trigger=(r.stop_loss_stats or {}).get("trigger_rate")))
                print(f"  {label:14s} CAGR {m['cagr']:7.2%}  변동성 {m['annualized_volatility']:6.2%}  "
                      f"MDD {m['mdd']:7.2%}  샤프 {m['sharpe']:.3f}  회전 {cs.get('avg_turnover',0):.0%}  "
                      f"종목 {n_hold:.0f}  실효N {eff_n:.1f}  상위3 {top3:.0%}", flush=True)
    finally:
        db.close()

    base = rows[0]
    ok = (abs(base["cagr"] - BASE["cagr"]) < 1e-3 and abs(base["sharpe"] - BASE["sharpe"]) < 2e-3
          and abs(base["mdd"] - BASE["mdd"]) < 1e-3)
    print(f"\n[회귀확인] λ=0 {'일치' if ok else '★불일치★'} "
          f"(기대 CAGR {BASE['cagr']:.2%} 샤프 {BASE['sharpe']:.3f} MDD {BASE['mdd']:.1%})")
    print(f"[벤치마크] KOSPI CAGR {bm['cagr']:.2%} 샤프 {bm['sharpe']:.3f} MDD {bm['mdd']:.1%}")

    if args.json:
        args.json.write_text(json.dumps(dict(rows=rows, bm=bm, base=base, regression_ok=ok),
                                        ensure_ascii=False, indent=2, default=str))
        print(f"\n저장: {args.json}")


if __name__ == "__main__":
    main()
