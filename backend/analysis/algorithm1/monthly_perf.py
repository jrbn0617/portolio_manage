"""알고리즘 #1 월별 성과 이력 — **마감된 달만**. 읽기 전용.

월중(MTD) 리포트가 "이번 달 지금까지"라면 이쪽은 "지난 달들이 어떻게 끝났나"다.
달마다 그 직전 월말에 형성한 포트폴리오를 그 달 마지막 거래일까지 따라간 결과이며,
재현 로직은 `mtd_performance.py`를 그대로 쓴다 — 원천을 한 곳에 둔다.

**진행 중인 달은 넣지 않는다.** 넣으면 며칠 뒤 같은 달의 숫자가 달라져서, 이력이라는
말이 성립하지 않는다. 이번 달은 월중 리포트(`mtd_viz.py`)가 담당한다.

**한 달 계산에 8초쯤 걸린다**(유니버스 → 스크리닝 → 모멘텀). 그래서 결과를 캐시에
쌓고 새로 마감된 달만 계산한다. 매달 한 번씩만 실제 계산이 일어난다.

홀드아웃 — 2020-01-01 이전 성과는 산출하지 않는다(CLAUDE.md). 첫 형성일은 그 직전
월말인 2019-12월 말이다. 성과 구간을 자르되 형성일은 직전 리밸런싱부터 잡는 규칙이다.

사용법:
  python analysis/algorithm1/monthly_perf.py            # 새로 마감된 달만 계산
  python analysis/algorithm1/monthly_perf.py --rebuild  # 캐시를 버리고 전부 다시
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtd_performance import bm_return, build, portfolio_return  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest_service import get_trading_days  # noqa: E402

SP = Path(os.environ.get("ALGO_OUT",
                         Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)
CACHE = SP / "monthly_perf.json"

# 홀드아웃 경계 — 코드로 강제한다. 이 날짜 **이후로 끝나는 달**만 성과를 낸다.
HOLDOUT_START = date(2020, 1, 1)


def month_ends(db, first: date, last: date) -> list[date]:
    """구간 안 각 달의 마지막 거래일. market_holidays 기준이라 엔진의 리밸런싱 날짜와 같다."""
    out, y, m = [], first.year, first.month
    while date(y, m, 1) <= last:
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        days = get_trading_days(db, date(y, m, 1), nxt - timedelta(days=1))
        if days:
            out.append(days[-1])
        y, m = (y + (m == 12), m % 12 + 1)
    return out


def closed_months(db) -> list[tuple[date, date]]:
    """(형성일, 월말) 쌍. **마지막 원소는 이미 마감된 달**이다.

    마감 판정은 "그 달 마지막 거래일까지 주식 시세가 들어왔나"로 한다. 달력으로
    보면 월말 이후여도 적재가 밀렸을 때 빈 달이 섞인다.
    """
    last_px = db.execute(text("""
        SELECT MAX(s.m) FROM instruments i CROSS JOIN LATERAL (
            SELECT MAX(p.date) m FROM prices p
            WHERE p.instrument_id = i.id AND p.period = 'D') s
        WHERE i.asset_type = 'stock'""")).scalar()
    # 첫 형성일은 홀드아웃 시작 직전 월말이라 한 달 앞에서 시작한다.
    ends = month_ends(db, (HOLDOUT_START.replace(day=1) - timedelta(days=1)), last_px)
    pairs = [(ends[i - 1], ends[i]) for i in range(1, len(ends))]
    # 진행 중인 달 제외 — 월말 거래일이 마지막 시세일과 같으면 아직 안 끝났을 수 있다.
    return [(f, e) for f, e in pairs if e < last_px.replace(day=1)]


def compute(form: date, end: date) -> dict:
    ctx = build(form, end)
    mp, rows = portfolio_return(ctx, end)
    bm = bm_return(ctx, end)
    ns = sum(r["w"] * r["r_nostop"] for r in rows) * ctx.exposure
    stopped = [r for r in rows if r["stopped"]]
    ctx.db.close()
    return dict(
        ym=f"{end:%Y-%m}", form=form.isoformat(), end=end.isoformat(),
        n_days=len([d for d in ctx.tdays if d <= end]) - 1,
        n_stocks=len(rows), exposure=ctx.exposure,
        mp=mp, bm=bm, excess=(mp - bm) if bm is not None else None, mp_nostop=ns,
        n_stopped=len(stopped),
        stopped=[dict(name=r["name"], r=r["r"], r_nostop=r["r_nostop"]) for r in stopped],
        best=max(rows, key=lambda r: r["r"])["name"] if rows else None,
        worst=min(rows, key=lambda r: r["r"])["name"] if rows else None,
    )


def load(rebuild: bool = False) -> list[dict]:
    """캐시를 채워서 돌려준다. 이미 있는 달은 다시 계산하지 않는다."""
    cached = {} if rebuild or not CACHE.is_file() else {
        m["ym"]: m for m in json.loads(CACHE.read_text())}
    db = SessionLocal()
    try:
        pairs = closed_months(db)
    finally:
        db.close()

    out, fresh = [], 0
    for form, end in pairs:
        ym = f"{end:%Y-%m}"
        hit = cached.get(ym)
        # 캐시가 다른 형성일·월말로 만들어졌으면(휴장일 정정 등) 다시 계산한다.
        if hit and hit["form"] == form.isoformat() and hit["end"] == end.isoformat():
            out.append(hit)
            continue
        print(f"  계산 {ym} ({form} → {end})", flush=True)
        out.append(compute(form, end))
        fresh += 1
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{len(out)}개월 (신규 계산 {fresh}개월) → {CACHE}")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="알고리즘 #1 월별 성과 이력 (마감된 달만)")
    p.add_argument("--rebuild", action="store_true", help="캐시를 버리고 전부 다시 계산")
    a = p.parse_args()
    months = load(a.rebuild)
    for m in months[-12:]:
        bm = "—" if m["bm"] is None else f"{m['bm']*100:+7.2f}%"
        print(f"  {m['ym']}  전략 {m['mp']*100:+7.2f}%  코스피 {bm}  "
              f"{m['n_stocks']:2d}종목  손절 {m['n_stopped']}건")
