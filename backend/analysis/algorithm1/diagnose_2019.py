"""2019년 부진 원인 분해 — 노출 / 손절 / 종목선택 3요인.

월별로 다음을 계산해 어디서 잃었는지 가른다.

  A = Σ w_i x r_i            (노출 100%, 손절 없음)  → 순수 종목선택 효과
  B = Σ w_i x r_i^stop       (노출 100%, 손절 있음)  → 손절 효과 = B - A
  C = exposure x B           (실제)                  → 노출 효과 = C - B

파라미터를 탐색하지 않는 사후 진단이다(홀드아웃 추가 소비 아님).
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.dividend_adjusted_price import DividendAdjustedPrice  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.backtest_service import (  # noqa: E402
    _last_trading_day_of_month, _shift_month, compute_free_float_weights, compute_momentum,
    compute_regime_exposure, compute_stop_point, find_halted_instruments, get_trading_days,
    resolve_universe,
)
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg  # noqa: E402

# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
SP = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)
CFG = ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                   consensus_lag_days=0, peg_min=0.0, max_age_days=100)
db = SessionLocal()

# 2019년 각 월의 성과는 직전 월말 형성 포트폴리오가 낸다
forms = [_last_trading_day_of_month(db, *_shift_month(2019, m, -1)) for m in range(1, 13)]
ends = [_last_trading_day_of_month(db, 2019, m) for m in range(1, 13)]

kospi = {r[0]: float(r[1]) for r in db.execute(text("""
    SELECT p.date, p.close FROM prices p JOIN instruments i ON i.id=p.instrument_id
    WHERE i.ticker='KOSPI' AND p.period='D' AND p.date BETWEEN '2018-11-01' AND '2020-01-31'
""")).fetchall()}

rows = []
print(f"{'월':8s}{'노출':>6s}{'종목':>5s}{'손절':>6s} | {'A선택':>8s}{'B손절후':>9s}{'C실제':>8s}"
      f" | {'손절효과':>9s}{'노출효과':>9s} | {'코스피':>8s}{'초과':>9s}")
for i, (F, E) in enumerate(zip(forms, ends), start=1):
    uni = resolve_universe(db, "KOSPI", F)
    tradable = [x for x in uni if x not in find_halted_instruments(db, uni, F, 10)]
    passed = screen_by_ebitda_peg(db, tradable, F, config=CFG, warn=lambda m: None, info=lambda m: None)
    mom = compute_momentum(db, passed, F)
    ranked = sorted([x for x in passed if mom.get(x) is not None], key=lambda x: mom[x], reverse=True)
    insts = {x.id: x for x in db.query(Instrument).filter(Instrument.id.in_(ranked)).all()}
    picks, cnt = [], {}
    for x in ranked:
        if len(picks) >= 20:
            break
        g = insts[x].krx_sector or insts[x].sector or "미분류"
        if cnt.get(g, 0) >= 2:
            continue
        picks.append(x); cnt[g] = cnt.get(g, 0) + 1
    w = compute_free_float_weights(db, picks, F, lambda m: None, mom, max_weight=0.25,
                                   group_field="krx_sector", max_group_weight=0.50, min_weight=0.01)
    exp = compute_regime_exposure(db, F, benchmark_ticker="KOSPI", ma_window_days=200,
                                  bull_exposure=1.0, bear_exposure=0.5)
    tdays = get_trading_days(db, F, E)

    A = B = 0.0
    n_stop = 0
    detail = []
    for iid in picks:
        wt = w.get(iid, 0.0)
        if not wt:
            continue
        q = db.query(DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close).filter(
            DividendAdjustedPrice.instrument_id == iid, DividendAdjustedPrice.period == "D",
            DividendAdjustedPrice.date.between(F, E)).all()
        m = {r.date: float(r.adj_close) for r in q}
        base = m.get(F)
        if not base:
            continue
        last, path = base, {}
        for d in tdays:
            last = m.get(d, last)
            path[d] = last / base
        r_nostop = path[tdays[-1]] - 1.0
        st = compute_stop_point(db, iid, path, tdays, 0.10, execution="next_open")
        r_stop = (st[1] - 1.0) if st else r_nostop
        if st:
            n_stop += 1
        A += wt * r_nostop
        B += wt * r_stop
        detail.append(dict(name=insts[iid].name, sector=insts[iid].krx_sector or "미분류",
                           w=wt, r=r_nostop, r_stop=r_stop, stopped=bool(st)))
    C = exp * B
    km = kospi.get(E, 0) / kospi.get(F, 1) - 1
    print(f"2019-{i:02d} {exp*100:>5.0f}%{len(detail):>5d}{n_stop:>4d}/{len(detail):<2d}"
          f" | {A*100:>+7.2f}%{B*100:>+8.2f}%{C*100:>+7.2f}%"
          f" | {(B-A)*100:>+8.2f}%{(C-B)*100:>+8.2f}% | {km*100:>+7.2f}%{(C-km)*100:>+8.2f}%p")
    rows.append(dict(month=i, exposure=exp, n=len(detail), n_stop=n_stop,
                     A=A, B=B, C=C, stop_eff=B - A, exp_eff=C - B, kospi=km,
                     excess=C - km, detail=detail))

db.close()
(SP / "diag2019.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str))

tot = lambda k: sum(r[k] for r in rows)  # noqa: E731
print(f"\n{'합계(단순합)':14s} A {tot('A')*100:+.1f}%  B {tot('B')*100:+.1f}%  C {tot('C')*100:+.1f}%"
      f"  | 손절효과 {tot('stop_eff')*100:+.1f}%p  노출효과 {tot('exp_eff')*100:+.1f}%p"
      f"  | 코스피 {tot('kospi')*100:+.1f}%")
print(f"평균 노출 {sum(r['exposure'] for r in rows)/12*100:.0f}%  "
      f"손절 발동 {sum(r['n_stop'] for r in rows)}/{sum(r['n'] for r in rows)}건")
