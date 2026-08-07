"""EBITDA PEG 스크리닝 + 모멘텀 백테스트 실행 스크립트 (검증용, 1회성).

app/services/factor_screen_service.screen_by_ebitda_peg 로 반기 편입종목을 먼저
좁히고(EBITDA PEG>0, 섹터별 하위 top-pct), 그 안에서 기존 모멘텀 엔진
(app/services/backtest_service.run_momentum_backtest)을 그대로 재사용해 상위 N종목을
매월 리밸런싱한다. 기본값: 코스닥150, 섹터별 EBITDA PEG 하위 50%, 상위10 동일가중,
TTM lag 90일(사업보고서 법정 공시기한 기준 보수값), 컨센서스 lag 0일.

사용법: python scripts/run_screened_momentum_backtest.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
        [--index KOSDAQ150] [--top-n 10] [--screen-top-pct 0.5] [--min-sector-size 5]
        [--ttm-lag-days 90] [--consensus-lag-days 0] [--json out.json]
"""
import argparse
import json
import sys
from datetime import date
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.services.backtest_service import BacktestConfig, run_momentum_backtest
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg

YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def warn(msg: str) -> None:
    print(f"{YELLOW}{msg}{RESET}")


def info(msg: str) -> None:
    print(f"{CYAN}{msg}{RESET}")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2020, 4, 30))
    p.add_argument("--index", default="KOSDAQ150")
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--lookback-months", type=int, default=12)
    p.add_argument("--skip-months", type=int, default=1)
    p.add_argument("--screen-top-pct", type=float, default=0.5)
    p.add_argument("--min-sector-size", type=int, default=5)
    p.add_argument("--ttm-lag-days", type=int, default=90)
    p.add_argument("--consensus-lag-days", type=int, default=0)
    p.add_argument("--peg-min", type=float, default=0.0)
    p.add_argument("--json", default=None, help="결과를 JSON으로 저장할 경로")
    return p.parse_args()


def main():
    args = parse_args()
    config = BacktestConfig(
        index_name=args.index,
        lookback_months=args.lookback_months,
        skip_months=args.skip_months,
        top_n=args.top_n,
        start_date=args.start,
        end_date=args.end,
    )
    screen_config = ScreenConfig(
        top_pct=args.screen_top_pct,
        min_sector_size=args.min_sector_size,
        ttm_lag_days=args.ttm_lag_days,
        consensus_lag_days=args.consensus_lag_days,
        peg_min=args.peg_min,
    )

    db = SessionLocal()
    screen_fn = partial(screen_by_ebitda_peg, config=screen_config, warn=warn, info=info)
    result = run_momentum_backtest(db, config, on_warning=warn, on_info=info, screen_fn=screen_fn)
    db.close()

    print("\n=== 리밸런싱 이력 ===")
    for r in result.rebalances:
        print(f"  {r['date']}: {', '.join(r['holdings'])}")

    print("\n=== 성과지표 ===")
    m = result.metrics
    print(f"  누적수익률: {m['cumulative_return']:+.2%}")
    print(f"  CAGR: {m['cagr']:+.2%}" if m["cagr"] is not None else "  CAGR: -")
    print(
        f"  연율화 변동성: {m['annualized_volatility']:.2%}"
        if m["annualized_volatility"] is not None
        else "  연율화 변동성: -"
    )
    print(f"  MDD: {m['mdd']:.2%}")
    print(f"  샤프비율(rf=0%): {m['sharpe']:.2f}" if m["sharpe"] is not None else "  샤프비율: -")

    if result.warnings:
        print(f"\n{YELLOW}=== 미분류 시계열 단절 ({len(result.warnings)}건) — resolve_backtest_event.py로 사유 입력 필요 ==={RESET}")
        for w in result.warnings:
            print(f"  {YELLOW}- {w}{RESET}")

    if args.json:
        payload = {
            "config": {
                "index_name": config.index_name,
                "start_date": config.start_date.isoformat(),
                "end_date": config.end_date.isoformat(),
                "top_n": config.top_n,
            },
            "screen_config": {
                "top_pct": screen_config.top_pct,
                "min_sector_size": screen_config.min_sector_size,
                "ttm_lag_days": screen_config.ttm_lag_days,
                "consensus_lag_days": screen_config.consensus_lag_days,
                "peg_min": screen_config.peg_min,
            },
            "nav_series": [[d.isoformat(), v] for d, v in result.nav_series],
            "rebalances": [
                {"date": r["date"].isoformat(), "holdings": r["holdings"], "momentum": r["momentum"]}
                for r in result.rebalances
            ],
            "metrics": result.metrics,
            "warnings": result.warnings,
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"\nJSON 저장: {args.json}")


if __name__ == "__main__":
    main()
