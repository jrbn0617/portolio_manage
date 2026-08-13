"""거래비용(증권거래세 0.20% 매도 + 위탁수수료 0.015% 양방향)을 반영해 전 전략을 재실행한다.

**왜 다시 도는가.** 알고리즘 #1은 엔진(app/services/backtest_service.run_momentum_backtest)에
비용 항목 자체가 없어 **총비용 0**으로 측정돼 있었다. 알고리즘 #2는 왕복 0.30% 정액이었는데,
정액 가정은 두 가지를 놓친다:

  1. 한국 주식은 **거래세가 매도에만** 붙는다 — 매수 0.015% / 매도 0.215%로 비대칭이다
  2. **손절 동결도 매도**다. 정액 회전율 비용만 물리면 손절 청산이 공짜가 된다

또 회전율을 "직전 목표비중 vs 신규 목표비중"으로 재면 보유 중의 가격 드리프트와 손절
청산분이 빠져 과소평가된다. 엔진과 알고리즘 #2 스크립트 모두 **직전 구간 종료 시점의
실제 비중** 기준으로 고쳤다.

산출: reference/거래비용반영_요약.csv, scratchpad용 JSON(--json)

홀드아웃: 성과 구간은 2020-01-01부터다. 형성일 lookback으로 그 이전 가격을 참조하는 건
소비가 아니다(CLAUDE.md "홀드아웃" 절).

사용법:
  python analysis/transaction_costs/rerun_with_costs.py
  python analysis/transaction_costs/rerun_with_costs.py --json /path/to/out.json
"""
import argparse
import json
import sys
from datetime import date
from functools import partial
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
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

HOLDOUT_START = date(2020, 1, 1)  # 이 날짜 이전 성과는 봉인 — CLAUDE.md "홀드아웃" 절
END = date(2026, 7, 31)
COST = TransactionCost(sell_tax=0.0020, commission=0.00015)
# 팩터값 신선도 상한 — 조회 기준일로부터 100일 넘은 값은 없는 것으로 보고 후보에서 뺀다.
# 없으면 2014년 EV/EBITDA로 2026년 종목을 고르게 된다(2026-08-13 실측: 후보의 62%가 스테일).
MAX_AGE_DAYS = 100

# 알고리즘 #1 확정 파라미터 (docs/algorithms/algorithm1-overview.md 3번)
A1_UNIVERSES = [("KOSPI", "코스피전체"), ("KOSPI200", "코스피200"),
                ("KOSDAQ", "코스닥전체"), ("KOSDAQ150", "코스닥150")]


def run_algo1(db, index_name: str, cost: TransactionCost | None):
    config = BacktestConfig(
        index_name=index_name, top_n=20, max_per_sector=2,
        sector_count_field="krx_sector", start_date=HOLDOUT_START, end_date=END,
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
        cost=cost,
    )


def holding_stats(result) -> tuple[float, float]:
    """리밸런싱별 (편입종목수, 실효N=1/HHI)의 중앙값.

    실효N은 top_n이 20이어도 유동시총가중이라 실질 분산이 훨씬 작다는 것을 보여주는 지표다.
    비중 0(최소비중 룰로 탈락)은 편입이 아니므로 제외한다.
    """
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
    mid = len(counts) // 2
    return float(counts[mid]), float(effn[mid])


def rebase(nav_series, start: date):
    """성과 구간을 start부터로 자르고 100으로 리베이스. 형성일은 그 이전부터라 잘라도 안전하다."""
    pts = [(d, v) for d, v in nav_series if d >= start]
    base = pts[0][1]
    return [(d, v / base * 100) for d, v in pts]


def index_series(db, tickers: list[str], days: list[date]) -> list[float]:
    """days 첫날 100 기준. 여러 티커를 주면 일간수익률 동일가중 constant-mix로 합성한다."""
    paths = {}
    for t in tickers:
        rows = (db.query(Price.date, Price.close)
                .join(Instrument, Instrument.id == Price.instrument_id)
                .filter(Instrument.ticker == t, Price.period == "D").all())
        by_date = {r.date: float(r.close) for r in rows}
        series, last = [], None
        for d in days:
            last = by_date.get(d, last)
            series.append(last)
        paths[t] = series
    w = 1.0 / len(tickers)
    out = [100.0]
    for i in range(1, len(days)):
        out.append(out[-1] * (1.0 + sum(w * (paths[t][i] / paths[t][i - 1] - 1.0) for t in tickers)))
    return out


def summarize(name, universe, gross, net, bm_label, bm_metrics, extra):
    g, n = gross["metrics"], net["metrics"]
    return dict(
        전략=name, 유니버스=universe, 벤치마크=bm_label,
        CAGR_비용전=g["cagr"], CAGR_비용후=n["cagr"], CAGR_차이=n["cagr"] - g["cagr"],
        샤프_비용전=g["sharpe"], 샤프_비용후=n["sharpe"],
        MDD_비용전=g["mdd"], MDD_비용후=n["mdd"],
        변동성_비용전=g["annualized_volatility"], 변동성_비용후=n["annualized_volatility"],
        누적_비용전=g["cumulative_return"], 누적_비용후=n["cumulative_return"],
        BM_CAGR=bm_metrics["cagr"], BM_샤프=bm_metrics["sharpe"], BM_MDD=bm_metrics["mdd"],
        **extra,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="시각화용 JSON 출력 경로")
    ap.add_argument("--only", choices=["a1", "a2"], default=None)
    args = ap.parse_args()

    db = SessionLocal()
    rows, payload = [], {}

    if args.only != "a2":
        for ticker, label in A1_UNIVERSES:
            print(f"알고리즘 #1 · {label} 실행 중...", flush=True)
            gross_r = run_algo1(db, ticker, cost=None)
            net_r = run_algo1(db, ticker, cost=COST)
            gross = rebase(gross_r.nav_series, HOLDOUT_START)
            net = rebase(net_r.nav_series, HOLDOUT_START)
            days = [d for d, _ in net]
            bm = index_series(db, [ticker], days)
            cs = net_r.cost_stats
            n_hold, eff_n = holding_stats(net_r)
            rows.append(summarize(
                "알고리즘 #1", label, dict(metrics=_compute_metrics(gross)),
                dict(metrics=_compute_metrics(net)), f"{ticker}(BM)",
                _compute_metrics(list(zip(days, bm))),
                dict(평균회전율=cs["avg_turnover"], 리밸런싱=cs["rebalances"],
                     편입종목_중앙=n_hold, 실효N=eff_n,
                     손절발동비율=(net_r.stop_loss_stats or {}).get("trigger_rate"),
                     비용중_손절비중=cs["stop_cost"] / max(cs["stop_cost"] + cs["rebalance_cost"], 1e-9)),
            ))
            payload[f"알고리즘 #1 · {label}"] = dict(
                dates=[str(d) for d in days],
                gross=[round(v, 4) for _, v in gross], net=[round(v, 4) for _, v in net],
                bm=[round(v, 4) for v in bm], bm_label=f"{ticker}(BM)",
                cost=dict(avg_turnover=cs["avg_turnover"], rebalances=cs["rebalances"],
                          rebalance_cost=cs["rebalance_cost"], stop_cost=cs["stop_cost"]),
            )

    if args.only != "a1":
        print("알고리즘 #2 실행 중...", flush=True)
        sys.path.insert(0, str(BACKEND_DIR / "scripts"))
        import export_algorithm2_backtest_excel as a2

        nav_net, _, turnover, hit, _, n_rebal = a2.run_backtest(db, END)
        a2_saved, a2.COST = a2.COST, TransactionCost(sell_tax=0.0, commission=0.0)
        nav_gross, *_ = a2.run_backtest(db, END)
        a2.COST = a2_saved

        net = rebase([(d.date(), float(v)) for d, v in nav_net.items()], HOLDOUT_START)
        gross = rebase([(d.date(), float(v)) for d, v in nav_gross.items()], HOLDOUT_START)
        days = [d for d, _ in net]
        bm = index_series(db, ["KOSPI200", "KOSDAQ150"], days)
        rows.append(summarize(
            "알고리즘 #2", "코스피200+코스닥150", dict(metrics=_compute_metrics(gross)),
            dict(metrics=_compute_metrics(net)), "KOSPI200:KOSDAQ150 50:50(BM)",
            _compute_metrics(list(zip(days, bm))),
            dict(평균회전율=turnover, 리밸런싱=n_rebal, 편입종목_중앙=20.0, 실효N=20.0,
                 손절발동비율=hit, 비용중_손절비중=float("nan")),
        ))
        payload["알고리즘 #2 · 코스피200+코스닥150"] = dict(
            dates=[str(d) for d in days],
            gross=[round(v, 4) for _, v in gross], net=[round(v, 4) for _, v in net],
            bm=[round(v, 4) for v in bm], bm_label="KOSPI200:KOSDAQ150 50:50(BM)",
            cost=dict(avg_turnover=turnover, rebalances=n_rebal, hit_rate=hit),
        )

    db.close()

    import pandas as pd
    res = pd.DataFrame(rows)
    print("\n" + "=" * 104)
    print(f"거래비용 반영 (매도 거래세 {COST.sell_tax:.2%} + 수수료 {COST.commission:.3%} 양방향 "
          f"= 왕복 {COST.round_trip:.3%})")
    print(f"성과구간 {HOLDOUT_START} ~ {END}\n")
    print(f"{'전략':12s} {'유니버스':10s} {'CAGR(전)':>9s} {'CAGR(후)':>9s} {'차이':>8s} "
          f"{'변동성':>7s} {'MDD':>8s} {'샤프(전)':>8s} {'샤프(후)':>8s} {'회전':>5s} "
          f"{'종목':>4s} {'실효N':>5s}")
    for _, r in res.iterrows():
        print(f"{r.전략:12s} {r.유니버스:10s} {r.CAGR_비용전:>+8.2%} {r.CAGR_비용후:>+8.2%} "
              f"{r.CAGR_차이:>+7.2%}p {r.변동성_비용후:>7.1%} {r.MDD_비용후:>8.1%} "
              f"{r.샤프_비용전:>8.3f} {r.샤프_비용후:>8.3f} {r.평균회전율:>4.0%} "
              f"{r.편입종목_중앙:>4.0f} {r.실효N:>5.1f}")

    out_csv = REPO_DIR / "reference" / "거래비용반영_요약.csv"
    res.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out_csv}")
    if args.json:
        Path(args.json).write_text(json.dumps(
            dict(cost=dict(sell_tax=COST.sell_tax, commission=COST.commission,
                           round_trip=COST.round_trip),
                 start=str(HOLDOUT_START), end=str(END),
                 summary=res.to_dict("records"), series=payload),
            ensure_ascii=False), encoding="utf-8")
        print(f"저장: {args.json}")


if __name__ == "__main__":
    main()
