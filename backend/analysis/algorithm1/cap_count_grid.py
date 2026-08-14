"""실험#21 — 종목캡 25%→20% x 케이산업(krx_sector)당 종목수 2→3 (2x2). 선택 구간 전용.

실험 20에서 "머리 쪽 쏠림(상위 2종목이 캡 25%씩 합계 50%)은 모멘텀 틸트로 건드려지지
않는다"는 것이 확인됐다. 머리를 직접 자르는 쪽(종목캡)과 후보를 넓히는 쪽(산업당 종목수)을
같이 본다. 두 장치가 반대 방향(집중↓ / 후보↑)이라 상호작용을 보려고 2x2로 돌린다.

홀드아웃: 성과 구간은 2020-01-01부터. 봉인 구간을 열지 않는다.

사용법: python analysis/algorithm1/cap_count_grid.py --json out.json
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

HOLDOUT_START = date(2020, 1, 1)
END = date(2026, 7, 31)
COST = TransactionCost(sell_tax=0.0020, commission=0.00015)
MAX_AGE_DAYS = 100
INDEX = "KOSPI"

CAPS = [0.25, 0.20]
COUNTS = [2, 3]
DEFAULT_START, DEFAULT_END = HOLDOUT_START, END
BASE = dict(cagr=0.3834, sharpe=1.513, mdd=-0.249)  # 확정 스펙 (캡25% x 2종목)


def rebase(nav, start):
    pts = [(d, v) for d, v in nav if d >= start]
    return [(d, v / pts[0][1] * 100) for d, v in pts]


def holding_stats(result):
    counts, effn, top3, mx = [], [], [], []
    for rb in result.rebalances:
        w = [x for x in rb["weights"].values() if x]
        if not w:
            continue
        s = sum(w)
        counts.append(len(w))
        effn.append(1.0 / sum((x / s) ** 2 for x in w))
        top3.append(sum(sorted(w, reverse=True)[:3]) / s)
        mx.append(max(w) / s)
    med = lambda a: sorted(a)[len(a) // 2]  # noqa: E731
    return tuple(float(med(a)) for a in (counts, effn, top3, mx))


def run(db, cap, cnt, start=None, end=None):
    config = BacktestConfig(index_name=INDEX, top_n=20, max_per_sector=cnt,
                            sector_count_field="krx_sector",
                            start_date=start or HOLDOUT_START, end_date=end or END)
    screen_fn = partial(screen_by_ebitda_peg,
                        config=ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                                            consensus_lag_days=0, peg_min=0.0,
                                            max_age_days=MAX_AGE_DAYS),
                        warn=lambda m: None, info=lambda m: None)
    weight_fn = partial(compute_free_float_weights, max_weight=cap, group_field="krx_sector",
                        max_group_weight=0.50, min_weight=0.01)
    exposure_fn = partial(compute_regime_exposure, benchmark_ticker=INDEX, ma_window_days=200,
                          bull_exposure=1.0, bear_exposure=0.5)
    return run_momentum_backtest(db, config, on_warning=lambda m: None, on_info=lambda m: None,
                                 screen_fn=screen_fn, weight_fn=weight_fn, exposure_fn=exposure_fn,
                                 stop_loss_pct=0.10, stop_loss_execution="next_open",
                                 stop_loss_mode="cash", idle_mode="cash", cost=COST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    ap.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START,
                    help="성과 구간 시작. 2020-01-01 이전을 주면 홀드아웃을 여는 것이므로 선등록 필수")
    ap.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    ap.add_argument("--caps", default=None, help="쉼표 구분 (예: 0.25,0.20)")
    ap.add_argument("--counts", default=None, help="쉼표 구분 (예: 2)")
    args = ap.parse_args()
    caps = [float(x) for x in args.caps.split(",")] if args.caps else CAPS
    counts = [int(x) for x in args.counts.split(",")] if args.counts else COUNTS
    start, end = args.start, args.end
    if start < HOLDOUT_START:
        print(f"※ 홀드아웃 구간을 엽니다: {start} ~ {end} — 선등록 문서 확인 필요\n")
    db = SessionLocal()
    rows = []
    try:
        bm_rows = (db.query(Price.date, Price.close)
                   .join(Instrument, Instrument.id == Price.instrument_id)
                   .filter(Instrument.ticker == INDEX, Price.period == "D",
                           Price.date.between(start, end)).order_by(Price.date).all())
        bm = _compute_metrics([(r[0], float(r[1])) for r in bm_rows])

        for cap in caps:
            for cnt in counts:
                r = run(db, cap, cnt, start, end)
                m = _compute_metrics(rebase(r.nav_series, start))
                n_hold, eff_n, top3, mx = holding_stats(r)
                cs = r.cost_stats or {}
                base = (cap == 0.25 and cnt == 2)
                label = f"캡{cap*100:.0f}% x {cnt}종목" + (" (현행)" if base else "")
                rows.append(dict(cap=cap, count=cnt, label=label, base=base,
                                 cagr=m["cagr"], vol=m["annualized_volatility"], mdd=m["mdd"],
                                 sharpe=m["sharpe"], turnover=cs.get("avg_turnover"),
                                 n_hold=n_hold, eff_n=eff_n, top3=top3, max_w=mx,
                                 trigger=(r.stop_loss_stats or {}).get("trigger_rate")))
                print(f"  {label:20s} CAGR {m['cagr']:7.2%}  변동성 {m['annualized_volatility']:6.2%}  "
                      f"MDD {m['mdd']:7.2%}  샤프 {m['sharpe']:.3f}  회전 {cs.get('avg_turnover',0):.0%}  "
                      f"종목 {n_hold:.0f}  실효N {eff_n:.1f}  상위3 {top3:.0%}  최대 {mx:.0%}", flush=True)
    finally:
        db.close()

    b = next(r for r in rows if r["base"])
    insample = (start == HOLDOUT_START and end == END)
    ok = (abs(b["cagr"] - BASE["cagr"]) < 1e-3 and abs(b["sharpe"] - BASE["sharpe"]) < 2e-3)
    if insample:
        print(f"\n[회귀확인] 현행 {'일치' if ok else '★불일치★'} "
              f"(기대 CAGR {BASE['cagr']:.2%} 샤프 {BASE['sharpe']:.3f})")
    else:
        print(f"\n[구간] {start} ~ {end} — 확정 스펙 회귀 기준값은 인샘플 전용이라 대조하지 않음")
    print(f"[벤치마크] KOSPI CAGR {bm['cagr']:.2%} 샤프 {bm['sharpe']:.3f} MDD {bm['mdd']:.1%}\n")
    print(f"{'':22s}{'샤프':>8s}{'Δ샤프':>8s}{'CAGR':>9s}{'MDD':>9s}{'실효N':>7s}")
    for r in rows:
        print(f"{r['label']:22s}{r['sharpe']:>8.3f}{r['sharpe']-b['sharpe']:>+8.3f}"
              f"{r['cagr']:>9.2%}{r['mdd']:>9.1%}{r['eff_n']:>7.1f}")

    if args.json:
        args.json.write_text(json.dumps(dict(rows=rows, bm=bm, base=b, regression_ok=ok),
                                        ensure_ascii=False, indent=2, default=str))
        print(f"\n저장: {args.json}")


if __name__ == "__main__":
    main()
