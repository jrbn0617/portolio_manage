"""직전 형성일 포트폴리오의 월중(MTD) 성과 — 오늘 종가까지. 읽기 전용.

리밸런싱 사이의 경과 성과를 보는 용도다. 엔진과 같은 규칙을 적용한다.
  - 비중: 형성일 산출값(유동시총 연동 + 종목 25%/업종 50%/업종당 2종목 한도)
  - 손실 제한: 형성가 대비 -10% 도달 시 **익영업일 시가** 청산, 이후 현금 보유
  - 시장 국면: 형성일 판정 노출을 월중 유지 (엔진의 월중 재판정은 별도)

주의 — 벤치마크(코스피 총수익)는 `prices.close`(ticker='KOSPI')이고 이 값은
평일 18:30 `refresh_benchmark_indices_bbg` 배치가 채운다. 장중이나 18:30 이전에
돌리면 **종목 시세보다 하루 짧다.** 그 경우 두 구간을 맞춘 비교를 함께 출력한다.

사용법:  python analysis/algorithm1/mtd_performance.py [YYYY-MM-DD(형성일)]

시각화는 `mtd_viz.py`가 이 모듈의 `build()`·`stock_paths()`를 그대로 써서 만든다 —
표와 그림이 어긋나지 않도록 재현 로직은 여기 한 곳에만 둔다.
"""
import sys
from datetime import date
from types import SimpleNamespace

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
STOP_LOSS = 0.10


def build(form: date) -> SimpleNamespace:
    """형성일 포트폴리오를 재현한다 — 편입 종목·비중·노출·거래일."""
    db = SessionLocal()
    last_px = db.execute(text("SELECT MAX(date) FROM prices WHERE period='D'")).scalar()
    last_bm = db.execute(text("""SELECT MAX(p.date) FROM prices p JOIN instruments i ON i.id=p.instrument_id
                                 WHERE i.ticker='KOSPI' AND p.period='D'""")).scalar()
    end = last_px

    uni = resolve_universe(db, "KOSPI", form)
    tradable = [i for i in uni if i not in find_halted_instruments(db, uni, form, 10)]
    passed = screen_by_ebitda_peg(db, tradable, form, config=CFG,
                                  warn=lambda m: None, info=lambda m: None)
    mom = compute_momentum(db, passed, form)
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
    w = compute_free_float_weights(db, picks, form, lambda m: None, mom, max_weight=0.25,
                                   group_field="krx_sector", max_group_weight=0.50, min_weight=0.01)
    exposure = compute_regime_exposure(db, form, benchmark_ticker="KOSPI", ma_window_days=200,
                                       bull_exposure=1.0, bear_exposure=0.5)
    return SimpleNamespace(db=db, form=form, end=end, last_px=last_px, last_bm=last_bm,
                           picks=picks, w=w, insts=insts, exposure=exposure,
                           tdays=get_trading_days(db, form, end))


def stock_paths(ctx, end: date) -> list[dict]:
    """종목별 **일별 가치배수 경로**(손실 제한 반영)와 손절 이벤트.

    표(합계)와 그림(곡선)이 같은 배열에서 나오도록 경로를 유일한 원천으로 둔다.
    동결 이후 값은 `compute_stop_point`가 준 (인덱스, 가치배수)로 덮어쓴다 —
    `apply_stop_loss`와 같은 규칙이다."""
    days = [d for d in ctx.tdays if d <= end]
    out = []
    for iid in ctx.picks:
        wt = ctx.w.get(iid, 0.0)
        if not wt:
            continue
        q = ctx.db.query(DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close).filter(
            DividendAdjustedPrice.instrument_id == iid, DividendAdjustedPrice.period == "D",
            DividendAdjustedPrice.date.between(ctx.form, end)).all()
        m = {r.date: float(r.adj_close) for r in q}
        base = m.get(ctx.form)
        if not base:
            continue
        last, raw = base, {}
        for d in days:
            last = m.get(d, last)
            raw[d] = last / base
        # compute_stop_point → (동결 시작 **인덱스**, 동결 가치배수). 인덱스를 날짜로 되돌린다.
        st = compute_stop_point(ctx.db, iid, raw, days, STOP_LOSS, execution="next_open")
        path = dict(raw)
        if st:
            for d in days[st[0]:]:
                path[d] = st[1]
        inst = ctx.insts[iid]
        out.append(dict(id=iid, name=DISPLAY.get(inst.ticker, inst.name), ticker=inst.ticker,
                        sector=inst.krx_sector or "미분류", w=wt,
                        path=path, path_nostop=raw,
                        r=path[days[-1]] - 1.0, r_nostop=raw[days[-1]] - 1.0,
                        stopped=bool(st), stop_date=days[st[0]] if st else None,
                        contrib=wt * (path[days[-1]] - 1.0)))
    return out


def portfolio_return(ctx, end: date):
    """end일까지의 종목 기여도 — 손실 제한 반영."""
    rows = stock_paths(ctx, end)
    return sum(r["contrib"] for r in rows) * ctx.exposure, rows


def bm_return(ctx, end: date):
    q = text("""SELECT p.date,p.close FROM prices p JOIN instruments i ON i.id=p.instrument_id
                WHERE i.ticker='KOSPI' AND p.period='D' AND p.date IN (:a,:b) ORDER BY p.date""")
    v = {r.date: float(r.close) for r in ctx.db.execute(q, {"a": ctx.form, "b": end})}
    return (v[end] / v[ctx.form] - 1.0) if len(v) == 2 else None


def main():
    form = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 7, 31)
    ctx = build(form)
    END = ctx.end

    print(f"형성일 {form}  →  {END}  ({len([d for d in ctx.tdays if d > form])}거래일)")
    print(f"편입 {len([i for i in ctx.picks if ctx.w.get(i)])}종목 · 주식 노출 {ctx.exposure:.0%}"
          f" · 시세 최신 {ctx.last_px} · 벤치마크 최신 {ctx.last_bm}\n")

    mtd, rows = portfolio_return(ctx, END)
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
        nostop = sum(r["w"] * r["r_nostop"] for r in rows) * ctx.exposure
        print(f"\n손실 제한 발동 {len(stopped)}건 — 청산 후 현금 보유(다음 정기 리밸런싱까지)")
        for r in stopped:
            print(f"  {r['name']} · {r['stop_date']} 익일시가 청산 {r['r']:+.1%}"
                  f" (미청산 시 {r['r_nostop']:+.1%}, 차이 {(r['r']-r['r_nostop'])*100:+.1f}%p)")
        print(f"  → 포트폴리오 영향 {(mtd-nostop)*100:+.2f}%p (손절 없었다면 {nostop:+.2%})")

    print(f"\n■ 포트폴리오 MTD  {mtd:+.2%}   ({form} → {END})")
    b_end = bm_return(ctx, END)
    if b_end is not None:
        print(f"■ 코스피 총수익   {b_end:+.2%}   → 초과 {(mtd-b_end)*100:+.2f}%p")
    else:
        print(f"■ 코스피 총수익   {END} 미수신 (배치 18:30) — 아래 동일구간 비교 참조")
        mtd2, _ = portfolio_return(ctx, ctx.last_bm)
        b2 = bm_return(ctx, ctx.last_bm)
        print(f"\n[구간 일치 비교] {form} → {ctx.last_bm}")
        print(f"  포트폴리오 {mtd2:+.2%} · 코스피 총수익 {b2:+.2%} → 초과 {(mtd2-b2)*100:+.2f}%p")

    ctx.db.close()


if __name__ == "__main__":
    main()
