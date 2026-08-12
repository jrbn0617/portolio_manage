"""모멘텀 전략 백테스트 실행 스크립트 (검증용, 1회성 — batch 등록 대상 아님).

기본값: 코스닥150, 최근1개월 제외 12개월 모멘텀, 상위10 동일가중, 매월말
리밸런싱, 2020-01-01~2020-04-30. 자세한 사양은 app/services/backtest_service.py 참고.

사용법: python scripts/run_backtest.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
        [--index KOSDAQ150] [--top-n 10] [--json out.json]
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.services.backtest_service import BacktestConfig, run_momentum_backtest

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
    p.add_argument(
        "--halt-lookback-days",
        type=int,
        default=10,
        help="리밸런싱일 직전 이 거래일수 동안 종가가 동일하면 거래정지로 보고 후보에서 제외 (0이면 필터 끔)",
    )
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
        halt_lookback_days=args.halt_lookback_days or None,
    )

    db = SessionLocal()
    result = run_momentum_backtest(db, config, on_warning=warn, on_info=info)
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
    print("  월별 수익률:")
    for month, ret in m["monthly_returns"].items():
        print(f"    {month}: {ret:+.2%}")

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
