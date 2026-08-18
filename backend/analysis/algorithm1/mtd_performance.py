"""직전 형성일 포트폴리오의 월중(MTD) 성과 — 오늘 종가까지. 읽기 전용.

리밸런싱 사이의 경과 성과를 보는 용도다. 엔진과 같은 규칙을 적용한다.
  - 비중: 형성일 산출값(유동시총 연동 + 종목 25%/업종 50%/업종당 2종목 한도)
  - 손실 제한: 형성가 대비 -10% 도달 시 **익영업일 시가** 청산, 이후 현금 보유
  - 시장 국면: 형성일 판정 노출을 월중 유지 (엔진의 월중 재판정은 별도)

주의 — 벤치마크(코스피 총수익)는 `prices.close`(ticker='KOSPI')이고 이 값은
평일 18:30 `refresh_benchmark_indices_bbg` 배치가 채운다. 장중이나 18:30 이전에
돌리면 **종목 시세보다 하루 짧다.** 그 경우 두 구간을 맞춘 비교를 함께 출력한다.

사용법:  python analysis/algorithm1/mtd_performance.py [YYYY-MM-DD(형성일)]
"""
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
    compute_free_float_weights, compute_momentum, compute_regime_exposure,
    compute_stop_point, find_halted_instruments, get_trading_days, resolve_universe,
)
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg  # noqa: E402

CFG = ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                   consensus_lag_days=0, peg_min=0.0, max_age_days=100)
DISPLAY = {"000660": "SK하이닉스", "028050": "삼성E&A",
           "082740": "한화엔진", "267270": "HD현대건설기계"}

FORM = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 7, 31)
db = SessionLocal()

last_px = db.execute(text("SELECT MAX(date) FROM prices WHERE period='D'")).scalar()
last_bm = db.execute(text("""SELECT MAX(p.date) FROM prices p JOIN instruments i ON i.id=p.instrument_id
                             WHERE i.ticker='KOSPI' AND p.period='D'""")).scalar()
END = last_px

# ---- 형성일 포트폴리오 재현 -------------------------------------------------
uni = resolve_universe(db, "KOSPI", FORM)
tradable = [i for i in uni if i not in find_halted_instruments(db, uni, FORM, 10)]
passed = screen_by_ebitda_peg(db, tradable, FORM, config=CFG,
                              warn=lambda m: None, info=lambda m: None)
mom = compute_momentum(db, passed, FORM)
ranked = sorted([i for i in passed if mom.get(i) is not None], key=lambda i: mom[i], reverse=True)
insts = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(ranked)).all()}
picks, cnt = [], {}
for i in ranked:
    if len(picks) >= 20:
        break
    g = insts[i].krx_sector or insts[i].sector or "미분류"
    if cnt.get(g, 0) >= 2:
        continue
    picks.append(i)
    cnt[g] = cnt.get(g, 0) + 1
w = compute_free_float_weights(db, picks, FORM, lambda m: None, mom, max_weight=0.25,
                               group_field="krx_sector", max_group_weight=0.50, min_weight=0.01)
exposure = compute_regime_exposure(db, FORM, benchmark_ticker="KOSPI", ma_window_days=200,
                                   bull_exposure=1.0, bear_exposure=0.5)
tdays = get_trading_days(db, FORM, END)


def portfolio_return(end: date):
    """end일까지의 종목 기여도 — 손실 제한 반영."""
    days = [d for d in tdays if d <= end]
    rows, gross = [], 0.0
    for iid in picks:
        wt = w.get(iid, 0.0)
        if not wt:
            continue
        q = db.query(DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close).filter(
            DividendAdjustedPrice.instrument_id == iid, DividendAdjustedPrice.period == "D",
            DividendAdjustedPrice.date.between(FORM, end)).all()
        m = {r.date: float(r.adj_close) for r in q}
        base = m.get(FORM)
        if not base:
            continue
        last, path = base, {}
        for d in days:
            last = m.get(d, last)
            path[d] = last / base
        r_open = path[days[-1]] - 1.0
        # compute_stop_point → (동결 시작 **인덱스**, 동결 가치배수). 인덱스를 날짜로 되돌린다.
        st = compute_stop_point(db, iid, path, days, 0.10, execution="next_open")
        r = (st[1] - 1.0) if st else r_open
        gross += wt * r
        rows.append(dict(name=DISPLAY.get(insts[iid].ticker, insts[iid].name),
                         ticker=insts[iid].ticker, sector=insts[iid].krx_sector or "미분류",
                         w=wt, r=r, r_nostop=r_open, stopped=bool(st),
                         stop_date=days[st[0]] if st else None, contrib=wt * r))
    return gross * exposure, rows


def bm_return(end: date):
    q = text("""SELECT p.date,p.close FROM prices p JOIN instruments i ON i.id=p.instrument_id
                WHERE i.ticker='KOSPI' AND p.period='D' AND p.date IN (:a,:b) ORDER BY p.date""")
    v = {r.date: float(r.close) for r in db.execute(q, {"a": FORM, "b": end})}
    return (v[end] / v[FORM] - 1.0) if len(v) == 2 else None


print(f"형성일 {FORM}  →  {END}  ({len([d for d in tdays if d > FORM])}거래일)")
print(f"편입 {len([i for i in picks if w.get(i)])}종목 · 주식 노출 {exposure:.0%}"
      f" · 시세 최신 {last_px} · 벤치마크 최신 {last_bm}\n")

mtd, rows = portfolio_return(END)
rows.sort(key=lambda r: -r["contrib"])
print(f"{'종목':<14}{'업종':<12}{'비중':>7}{'수익률':>9}{'기여도':>9}  손절")
print("-" * 62)
for r in rows:
    tag = f"O {r['stop_date']}" if r["stopped"] else ""
    print(f"{r['name']:<14}{r['sector']:<12}{r['w']*100:>6.1f}%{r['r']*100:>+8.1f}%"
          f"{r['contrib']*100:>+8.2f}%p  {tag}")
print("-" * 62)
print(f"{'합계':<14}{'':<12}{sum(r['w'] for r in rows)*100:>6.1f}%{'':>9}{mtd*100:>+8.2f}%p")

stopped = [r for r in rows if r["stopped"]]
if stopped:
    nostop = sum(r["w"] * r["r_nostop"] for r in rows) * exposure
    print(f"\n손실 제한 발동 {len(stopped)}건 — 청산 후 현금 보유(다음 정기 리밸런싱까지)")
    for r in stopped:
        print(f"  {r['name']} · {r['stop_date']} 익일시가 청산 {r['r']:+.1%}"
              f" (미청산 시 {r['r_nostop']:+.1%}, 차이 {(r['r']-r['r_nostop'])*100:+.1f}%p)")
    print(f"  → 포트폴리오 영향 {(mtd-nostop)*100:+.2f}%p (손절 없었다면 {nostop:+.2%})")

print(f"\n■ 포트폴리오 MTD  {mtd:+.2%}   ({FORM} → {END})")
b_end = bm_return(END)
if b_end is not None:
    print(f"■ 코스피 총수익   {b_end:+.2%}   → 초과 {(mtd-b_end)*100:+.2f}%p")
else:
    print(f"■ 코스피 총수익   {END} 미수신 (배치 18:30) — 아래 동일구간 비교 참조")
    mtd2, _ = portfolio_return(last_bm)
    b2 = bm_return(last_bm)
    print(f"\n[구간 일치 비교] {FORM} → {last_bm}")
    print(f"  포트폴리오 {mtd2:+.2%} · 코스피 총수익 {b2:+.2%} → 초과 {(mtd2-b2)*100:+.2f}%p")

db.close()
