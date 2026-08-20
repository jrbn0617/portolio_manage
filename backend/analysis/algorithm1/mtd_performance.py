"""직전 형성일 포트폴리오의 월중(MTD) 성과 — 전일 종가 기준. 읽기 전용.

리밸런싱 사이의 경과 성과를 보는 용도다. 엔진과 같은 규칙을 적용한다.
  - 비중: 형성일 산출값(유동시총 연동 + 종목 25%/업종 50%/업종당 2종목 한도)
  - 손실 제한: 형성가 대비 -10% 도달 시 **익영업일 시가** 청산, 이후 현금 보유
  - 시장 국면: 형성일 판정 노출을 월중 유지 (엔진의 월중 재판정은 별도)

**기준일은 기본값이 전 영업일이다** — 매일 돌려도 같은 날 두 번 돌리면 같은 결과가
나와야 하기 때문이다. 당일 종가를 쓰면 언제 돌렸느냐에 따라 값이 달라진다: 장중에는
미완성 종가가 섞이고, 코스피 총수익은 평일 18:30 `refresh_benchmark_indices_bbg` 배치가
채우므로 그 전에는 아예 없다. 하루 물리면 양쪽이 모두 확정돼 있다.

형성일도 기본값이 자동이다 — 기준일 직전의 월말 거래일을 찾는다. 리밸런싱이 월 1회라
달이 바뀌면 형성일도 따라 바뀌어야 하는데, 고정해 두면 매달 손으로 고쳐야 한다.

사용법:
  python analysis/algorithm1/mtd_performance.py                    # 전 영업일 기준
  python analysis/algorithm1/mtd_performance.py --as-of 2026-08-18 # 기준일 지정
  python analysis/algorithm1/mtd_performance.py --form 2026-07-31  # 형성일 지정
  python analysis/algorithm1/mtd_performance.py --today            # 당일 종가까지 (확정 전일 수 있음)

시각화는 `mtd_viz.py`가 이 모듈의 `build()`·`stock_paths()`를 그대로 써서 만든다 —
표와 그림이 어긋나지 않도록 재현 로직은 여기 한 곳에만 둔다.
"""
import argparse
import sys
from datetime import date, timedelta
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


def resolve_as_of(db, as_of: date | None = None, include_today: bool = False) -> date:
    """기준일 = 요청일 이하의 마지막 거래일. 기본은 **오늘을 뺀** 마지막 거래일이다.

    달력으로 어제를 계산하지 않는 이유 — 주말·휴장일을 직접 다뤄야 하고, 거래일이어도
    시세 적재가 실패했으면 빈 날이 된다. 실제 시세가 있는 날 중에서 고르면 둘 다 걸리지 않는다.
    """
    # **주식만 본다.** prices 에는 해외지수·금도 들어 있어 전체 MAX 를 잡으면 한국
    # 휴장일이 딸려 온다(2026-08-17 대체휴일에 LEGATRUU·XAU 등 8종이 값을 갖는다).
    # 종목별 MAX 를 LATERAL 로 구해 합치는 이유는 속도다 — 큰 테이블 인덱스가 전부
    # (instrument_id, date, ...) 라 date 선행 조건은 풀스캔이 된다. 실측 426ms → 17ms.
    op = "<=" if (as_of is not None or include_today) else "<"
    if as_of is None:
        as_of = date.today()
    d = db.execute(text(f"""
        SELECT MAX(s.m) FROM instruments i CROSS JOIN LATERAL (
            SELECT MAX(p.date) m FROM prices p
            WHERE p.instrument_id = i.id AND p.period = 'D' AND p.date {op} :d) s
        WHERE i.asset_type = 'stock'"""), {"d": as_of}).scalar()
    if d is None:
        raise RuntimeError(f"{as_of} 이전 시세가 없습니다.")
    return d


def resolve_formation(db, as_of: date) -> date:
    """형성일 = 기준일 직전의 월말 거래일. 전략이 월 1회 리밸런싱이라 이게 직전 형성일이다.

    기준일이 그달 말일이면 그날 형성한 포트폴리오는 아직 하루도 안 지났으므로 한 달 더
    거슬러 간다(경과 0거래일짜리 리포트를 만들지 않는다).
    """
    # **진행 중인 달은 빼야 한다** — 그러지 않으면 이번 달의 마지막 거래일(=며칠 전)이
    # 월말로 잡혀서 형성일이 며칠 전이 된다. 실제로 그렇게 나왔다: 8/19 기준에 8/18.
    #
    # 거래일 판정은 prices 가 아니라 market_holidays 로 한다 — 엔진의 리밸런싱 날짜와
    # 같은 원천이어야 형성일이 어긋나지 않고, prices 에 섞인 해외 거래일도 안 걸린다.
    prev_month_end = as_of.replace(day=1) - timedelta(days=1)
    days = get_trading_days(db, prev_month_end.replace(day=1), prev_month_end)
    if not days:
        raise RuntimeError(f"{prev_month_end:%Y-%m} 에 거래일이 없습니다.")
    return days[-1]


def build(form: date, as_of: date | None = None) -> SimpleNamespace:
    """형성일 포트폴리오를 재현한다 — 편입 종목·비중·노출·거래일.

    as_of 를 주면 그날까지만 본다. 안 주면 시세가 있는 마지막 날(=당일 포함)이라
    호출부에서 resolve_as_of() 로 먼저 정해 넘기는 것을 기본으로 한다.
    """
    db = SessionLocal()
    last_px = db.execute(text("SELECT MAX(date) FROM prices WHERE period='D'")).scalar()
    last_bm = db.execute(text("""SELECT MAX(p.date) FROM prices p JOIN instruments i ON i.id=p.instrument_id
                                 WHERE i.ticker='KOSPI' AND p.period='D'""")).scalar()
    end = as_of or last_px
    # 기준일까지 실제로 받은 벤치마크 — 기준일을 물려도 지수 배치가 밀렸으면 여전히 짧다
    last_bm = min(last_bm, end) if last_bm else last_bm

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


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="알고리즘 #1 월중 성과 (기본: 전 영업일 종가 기준)")
    p.add_argument("form_pos", nargs="?", metavar="YYYY-MM-DD",
                   help="형성일 (구버전 호환 — --form 과 같다)")
    p.add_argument("--form", type=date.fromisoformat, default=None,
                   help="형성일. 생략하면 기준일 직전 월말 거래일")
    p.add_argument("--as-of", dest="as_of", type=date.fromisoformat, default=None,
                   help="기준일. 생략하면 전 영업일")
    p.add_argument("--today", action="store_true",
                   help="당일 종가까지 본다 (장중이면 미확정, 벤치마크는 18:30 이후)")
    a = p.parse_args(argv)
    if a.form is None and a.form_pos:
        a.form = date.fromisoformat(a.form_pos)
    return a


def resolve_window(args) -> tuple[date, date]:
    """(형성일, 기준일). 스크립트마다 같은 규칙을 쓰도록 여기 한 곳에 둔다."""
    db = SessionLocal()
    try:
        as_of = resolve_as_of(db, args.as_of, include_today=args.today)
        return (args.form or resolve_formation(db, as_of)), as_of
    finally:
        db.close()


def main():
    args = parse_args()
    form, as_of = resolve_window(args)
    ctx = build(form, as_of)
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
