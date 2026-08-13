"""실험#19 — 미투자분(레짐 축소분 / 손절 청산분)을 실물 지수 ETF로 대체하면 어떻게 되나.

**왜 다시 도는가.** 실험 18이 손절 자금의 지수추종을 이미 기각했지만 세 가지가 달랐다:

  1. 손절 뭉치만 봤다. **레짐필터 축소분(약세 판정 시 50%)은 한 번도 시험한 적이 없다**
  2. ETF 대용치로 벤치마크 지수 레벨을 썼다 — 운용보수·추적오차가 없는 유리한 대용치다
  3. 2026-08-12 백필 / 주식수 교정 / 신선도 100일 도입 **이전** 데이터였다

이번엔 실물 KODEX ETF의 **배당조정 종가**(분배금 재투자 반영, 보수·추적오차는 가격에 이미
포함)를 쓰고 최신 데이터로 돈다.

선등록: scratchpad/exp19_prereg.md — 판정기준은 데이터를 보기 전에 확정했다.
  채택 = 4개 유니버스 전부에서 샤프가 A 이상 & MDD가 A보다 악화되지 않을 것.
  CAGR 단독 상승은 채택 근거로 쓰지 않는다.

홀드아웃: 성과 구간은 2020-01-01부터. 형성일 lookback으로 그 이전 가격을 참조하는 건
소비가 아니다(CLAUDE.md "홀드아웃" 절).

사용법:
  python analysis/algorithm1/idle_etf_substitution.py
  python analysis/algorithm1/idle_etf_substitution.py --json /path/to/out.json
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
from app.models.dividend_adjusted_price import DividendAdjustedPrice  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
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
MAX_AGE_DAYS = 100

# (index_name, 한글명, 미투자분을 담을 ETF 티커)
# 코스닥전체는 전체를 추종하는 ETF가 없어 KODEX 코스닥150을 대용으로 쓴다.
UNIVERSES = [
    ("KOSPI", "코스피전체", "226490"),      # KODEX 코스피
    ("KOSPI200", "코스피200", "069500"),    # KODEX 200
    ("KOSDAQ", "코스닥전체", "229200"),     # KODEX 코스닥150 (대용)
    ("KOSDAQ150", "코스닥150", "229200"),   # KODEX 코스닥150
]

# (라벨, 손절 청산분 모드, 레짐 축소분 모드)
VARIANTS = [
    ("A 현행(둘 다 현금)", "cash", "cash"),
    ("B 레짐만 ETF", "cash", "index"),
    ("C 손절만 ETF", "index", "cash"),
    ("D 둘 다 ETF", "index", "index"),
]

# 선등록에 적어 둔 회귀 확인 기준값 (2026-08-13 재실행판, 비용 후)
REGRESSION = {
    "코스피전체": (0.3834, 1.513, -0.249),
    "코스피200": (0.2639, 1.143, -0.326),
    "코스닥전체": (0.1880, 0.800, -0.307),
    "코스닥150": (0.1885, 0.801, -0.361),
}


def rebase(nav_series, start: date):
    """성과 구간을 start부터로 자르고 100으로 리베이스.

    **엔진의 nav_series는 첫 형성일(2019-12-30)부터 시작한다.** config.start_date를
    2020-01-01로 줘도 그 직전 리밸런싱이 시작점이라 그렇다 — 형성일 참조는 홀드아웃
    소비가 아니지만 **그 날 성과를 재는 건 소비**다(CLAUDE.md "홀드아웃" 절).
    `result.metrics`를 그대로 쓰면 2019-12-30 하루가 성과에 섞여 들어가고,
    rerun_with_costs.py의 공식 수치와도 어긋난다.
    """
    pts = [(d, v) for d, v in nav_series if d >= start]
    base = pts[0][1]
    return [(d, v / base * 100) for d, v in pts]


def etf_series(db, ticker: str) -> dict[date, float]:
    """ETF 배당조정 종가. 분배금 재투자가 반영돼 있고 운용보수·추적오차는 실제 가격에 포함."""
    rows = (
        db.query(DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close)
        .join(Instrument, Instrument.id == DividendAdjustedPrice.instrument_id)
        .filter(Instrument.ticker == ticker, DividendAdjustedPrice.period == "D")
        .all()
    )
    return {r.date: float(r.adj_close) for r in rows}


def run(db, index_name: str, stop_mode: str, idle_mode: str, series: dict[date, float]):
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
        stop_loss_pct=0.10, stop_loss_execution="next_open",
        stop_loss_mode=stop_mode,
        post_stop_series=series if stop_mode == "index" else None,
        idle_mode=idle_mode,
        idle_series=series if idle_mode == "index" else None,
        cost=COST,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    db = SessionLocal()
    rows = []
    try:
        for index_name, label, etf_ticker in UNIVERSES:
            series = etf_series(db, etf_ticker)
            first = min(series) if series else None
            print(f"\n=== {label} (ETF {etf_ticker}, 시세 {first}~{max(series)}, {len(series)}행) ===",
                  flush=True)
            base = None
            for vlabel, stop_mode, idle_mode in VARIANTS:
                r = run(db, index_name, stop_mode, idle_mode, series)
                m = _compute_metrics(rebase(r.nav_series, HOLDOUT_START))
                if base is None:
                    base = m
                    exp = REGRESSION[label]
                    ok = (abs(m["cagr"] - exp[0]) < 1e-3 and abs(m["sharpe"] - exp[1]) < 2e-3
                          and abs(m["mdd"] - exp[2]) < 1e-3)
                    print(f"  [회귀확인] {'일치' if ok else '★불일치★'} "
                          f"(기대 CAGR {exp[0]:.2%} 샤프 {exp[1]:.3f} MDD {exp[2]:.1%})")
                print(f"  {vlabel:<18} CAGR {m['cagr']:7.2%}  변동성 {m['annualized_volatility']:6.2%}  "
                      f"MDD {m['mdd']:7.2%}  샤프 {m['sharpe']:.3f}  "
                      f"Δ샤프 {m['sharpe'] - base['sharpe']:+.3f}  ΔMDD {m['mdd'] - base['mdd']:+.1%}",
                      flush=True)
                rows.append(dict(
                    유니버스=label, 변형=vlabel, ETF=etf_ticker,
                    손절모드=stop_mode, 레짐모드=idle_mode,
                    CAGR=m["cagr"], 변동성=m["annualized_volatility"],
                    MDD=m["mdd"], 샤프=m["sharpe"],
                    누적=m["cumulative_return"],
                    총비용=(r.cost_stats or {}).get("total_cost_pct"),
                    샤프차=m["sharpe"] - base["sharpe"], MDD차=m["mdd"] - base["mdd"],
                ))
    finally:
        db.close()

    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        print(f"\n저장: {args.json}")

    print("\n=== 판정 (선등록 기준: 4개 전부 샤프 ≥ A & MDD 악화 없음) ===")
    for vlabel, _, _ in VARIANTS[1:]:
        rs = [r for r in rows if r["변형"] == vlabel]
        pass_sharpe = all(r["샤프차"] >= 0 for r in rs)
        pass_mdd = all(r["MDD차"] >= 0 for r in rs)   # MDD는 음수라 클수록(0에 가까울수록) 낫다
        verdict = "채택" if (pass_sharpe and pass_mdd) else "기각"
        fails = [f"{r['유니버스']}(Δ샤프 {r['샤프차']:+.3f}, ΔMDD {r['MDD차']:+.1%})"
                 for r in rs if r["샤프차"] < 0 or r["MDD차"] < 0]
        print(f"  {vlabel:<18} → **{verdict}**" + ("  실패: " + ", ".join(fails) if fails else ""))


if __name__ == "__main__":
    main()
