"""알고리즘 #1 운용 통계 — 손절 **건수**와 거래비용의 연환산 부담. 읽기 전용.

제안서 [참고 1] 운용 통계 칸을 채운다. 두 가지를 명확히 하려고 만들었다.

1. **손절 건수** — 기존 표에는 발동'비율'(32.2%)만 있어 "몇 번 사고팔았나"에 답이 안 됐다.
   sl_stats.triggered 가 실제 청산 건수다.
2. **거래비용의 실질 부담** — 엔진이 쌓는 total_cost 는 NAV 100 기준 **포인트 누적합**이라
   말년의 같은 비율이 더 큰 포인트로 잡힌다. 원금 대비 %가 아니므로 그대로 쓰면 오해다.
   비용 없이 한 번 더 돌려 **CAGR 차이**를 내는 것이 정확한 표현이다.

같은 구간·같은 설정으로 비용 유/무 2회 실행한다. DB 쓰기 없음.
"""
import json
import os
import sys
from datetime import date
from functools import partial
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest_service import (  # noqa: E402
    BacktestConfig, TransactionCost, compute_free_float_weights,
    compute_regime_exposure, run_momentum_backtest,
)
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg  # noqa: E402

START, END = date(2017, 5, 22), date(2026, 7, 31)
# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
SP = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)

config = BacktestConfig(index_name="KOSPI", top_n=20, max_per_sector=2,
                        sector_count_field="krx_sector", start_date=START, end_date=END)
screen_fn = partial(screen_by_ebitda_peg,
                    config=ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                                        consensus_lag_days=0, peg_min=0.0, max_age_days=100),
                    warn=lambda m: None, info=lambda m: None)
weight_fn = partial(compute_free_float_weights, max_weight=0.25, group_field="krx_sector",
                    max_group_weight=0.50, min_weight=0.01)
exposure_fn = partial(compute_regime_exposure, benchmark_ticker="KOSPI",
                      ma_window_days=200, bull_exposure=1.0, bear_exposure=0.5)


def run(cost):
    db = SessionLocal()
    r = run_momentum_backtest(
        db, config, on_warning=lambda m: None, on_info=lambda m: None,
        screen_fn=screen_fn, weight_fn=weight_fn, exposure_fn=exposure_fn,
        stop_loss_pct=0.10, stop_loss_execution="next_open", stop_loss_mode="cash",
        cost=cost)
    db.close()
    return r


print(f"구간 {START} ~ {END}\n실행 1/2 — 비용 반영 …")
withc = run(TransactionCost(sell_tax=0.0020, commission=0.00015))
print("실행 2/2 — 비용 미반영 …")
noc = run(None)


def rebase(series, start):
    """엔진 nav_series는 **첫 형성일**부터 시작한다. 성과 구간 시작으로 다시 맞춘다."""
    pts = [(d, v) for d, v in series if d >= start]
    b = pts[0][1]
    return [(d, v / b) for d, v in pts]


def cagr(series):
    s = rebase(series, START)
    yrs = (s[-1][0] - s[0][0]).days / 365.25
    return s[-1][1] ** (1 / yrs) - 1, s[-1][1] - 1


sl, cs = withc.stop_loss_stats, withc.cost_stats
c_cagr, c_cum = cagr(withc.nav_series)
n_cagr, n_cum = cagr(noc.nav_series)

# 누적 편입 종목수 — 전 구간에 한 번이라도 편입된 **고유 종목** 수.
# 이전에는 별도 창 실행값(302)이 하드코딩돼 있었다.
names = {t for r in withc.rebalances for t in r["holdings"]}
names_total = len(names)

print("\n=== 손절(손실 제한) ===")
print(f"  발동 건수      {sl['triggered']:,}건")
print(f"  전체 포지션    {sl['positions']:,}건 (리밸런싱 x 편입종목)")
print(f"  발동 비율      {sl['trigger_rate']:.1%}")
print(f"  리밸런싱당     {sl['triggered']/cs['rebalances']:.1f}건/회")
print(f"  청산 후 현금   보유일 기준 {sl['idle_ratio']:.1%}")

print("\n=== 거래비용 ===")
print(f"  리밸런싱 횟수  {cs['rebalances']}회 (정기 월 1회)")
print(f"  누적 편입 종목  {names_total}개 (고유 종목 기준)")
print(f"  평균 회전율    {cs['avg_turnover']:.1%} / 회")
print(f"  누적 차감      {cs['total_cost']:.1f}pt "
      f"(정기 {cs['rebalance_cost']:.1f} + 손절 {cs['stop_cost']:.1f}) ※NAV 100 기준 포인트 누적합")
print(f"  손절이 차지    {cs['stop_cost']/cs['total_cost']:.0%}")
print("\n  --- 실질 부담 (비용 유/무 재실행 비교) ---")
print(f"  CAGR  비용전 {n_cagr:+.2%}  →  비용후 {c_cagr:+.2%}   차이 {(c_cagr-n_cagr)*100:+.2f}%p")
print(f"  누적  비용전 {n_cum:+.1%}  →  비용후 {c_cum:+.1%}   차이 {(c_cum-n_cum)*100:+.1f}%p")

out = dict(start=str(START), end=str(END),
           triggered=sl["triggered"], positions=sl["positions"],
           trigger_rate=sl["trigger_rate"], idle_ratio=sl["idle_ratio"],
           stops_per_rebal=sl["triggered"] / cs["rebalances"],
           rebalances=cs["rebalances"], avg_turnover=cs["avg_turnover"],
           total_cost_pt=cs["total_cost"], rebalance_cost_pt=cs["rebalance_cost"],
           stop_cost_pt=cs["stop_cost"],
           names_total=names_total,
           cagr_gross=n_cagr, cagr_net=c_cagr, cagr_drag=c_cagr - n_cagr,
           cum_gross=n_cum, cum_net=c_cum)
(SP / "ops_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(f"\n저장: {SP/'ops_stats.json'}")
