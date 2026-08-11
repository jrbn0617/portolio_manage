"""모멘텀 백테스트 재설계 — 앞선 백테스트의 세 가지 불리한 선택을 모두 교정.

이전(CAGR 4.05%, Sharpe 0.107로 최악)에서 바꾼 것:
  보유기간 60일 -> 20일(월간)   : 분위분석에서 모멘텀은 20일 지평에서 작동
  lookback 12개월 -> 3/6/12 비교 : 분위분석 최적은 6개월(Q5-Q1 +1.57%p)
  종목수 20 고정 -> 20/30/50 비교 : 20종목은 극단이라 변동성 폭증(직전 MDD -57.6%)

월간 리밸런싱은 회전율이 분기 대비 3배 수준이라 거래비용이 결정적이므로 민감도까지 본다.
기관 순매수 하위 필터를 얹은 조합도 함께 검증.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

START = "2019-01-01"
SKIP = 21
MOM = {"3개월": 63, "6개월": 126, "12개월": 252}
HOLD = 20               # 월간 리밸런싱
TOPNS = [20, 30, 50]
FLOW_WINDOW = 60
FLOW_KEEP = 0.40
COST = 0.0030
OUT_DIR = REPO_DIR / "reference"

db = SessionLocal()
UNIV = """
with u as (select distinct instrument_id iid from index_memberships
           where index_name in ('KOSDAQ150','KOSPI200'))
"""


def pivot(sql, params):
    df = pd.read_sql(text(sql), db.bind, params=params)
    p = df.pivot(index="date", columns="instrument_id", values="value").sort_index()
    p.index = pd.to_datetime(p.index)
    return p


print("데이터 로딩 중...")
adj = pivot(UNIV + """select d.date,d.instrument_id,d.adj_close::float value
 from dividend_adjusted_prices d join u on u.iid=d.instrument_id
 where d.period='D' and d.date>=:s""", {"s": START})
amt = pivot(UNIV + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)
inst_flow = pivot(UNIV + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type='기관합계'""", {"s": START}).reindex_like(adj)

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

mom = {k: adj.shift(SKIP) / adj.shift(SKIP + n) - 1 for k, n in MOM.items()}
flow_sig = inst_flow.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / \
    amt.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
dret = adj.pct_change(fill_method=None)

days = list(adj.index)
rebal = list(range(max(MOM.values()) + SKIP + 60, len(days) - 1, HOLD))
print(f"리밸런싱 {len(rebal)}회 ({days[rebal[0]].date()} ~ {days[rebal[-1]].date()}), 보유 {HOLD}일\n")


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


def run(select_fn):
    nav, dates, prev, tn = [1.0], [days[rebal[0]]], {}, []
    for ri in rebal:
        d = days[ri]
        cols = [c for c in adj.columns if c in universe_at(d)]
        if len(cols) < 100:
            continue
        picks = select_fn(d, cols)
        if not picks:
            continue
        w = {p: 1 / len(picks) for p in picks}
        turn = sum(abs(w.get(k, 0) - prev.get(k, 0)) for k in set(w) | set(prev)) / 2
        tn.append(turn)
        nav[-1] *= (1 - turn * COST)
        cur = dict(w)
        for j in range(ri + 1, min(ri + HOLD, len(days) - 1) + 1):
            r = dret.loc[days[j], list(cur)].fillna(0.0)
            nav.append(nav[-1] * (1 + float((pd.Series(cur) * r).sum())))
            dates.append(days[j])
            g = {k: cur[k] * (1 + float(r.get(k, 0.0))) for k in cur}
            s = sum(g.values())
            cur = {k: v / s for k, v in g.items()} if s > 0 else cur
        prev = cur
    return pd.Series(nav, index=dates), np.mean(tn)


def mk_mom(mlabel, n):
    def f(d, cols):
        m = mom[mlabel].loc[d, cols]
        return list(m[m.notna()].nlargest(n).index)
    return f


def mk_flow_mom(mlabel, n):
    def f(d, cols):
        fl = flow_sig.loc[d, cols]
        fl = fl[fl.notna()]
        if len(fl) < n * 2:
            return []
        cand = fl.nsmallest(max(n, int(len(fl) * FLOW_KEEP))).index
        m = mom[mlabel][cand].loc[d] if False else mom[mlabel].loc[d, cand]
        m = m[m.notna()]
        return list(m.nlargest(n).index) if len(m) >= n else []
    return f


strats = {"벤치마크": lambda d, c: [x for x in c if not np.isnan(adj.loc[d, x])]}
for ml in MOM:
    for n in TOPNS:
        strats[f"모멘텀{ml}·{n}종목"] = mk_mom(ml, n)
for n in TOPNS:
    strats[f"기관필터+모멘텀6개월·{n}종목"] = mk_flow_mom("6개월", n)

curves, turns = {}, {}
for name, fn in strats.items():
    curves[name], turns[name] = run(fn)


def met(nav):
    r = nav.pct_change().dropna()
    y = (nav.index[-1] - nav.index[0]).days / 365.25
    c = nav.iloc[-1] ** (1 / y) - 1
    v = r.std() * np.sqrt(252)
    return c, v, (nav / nav.cummax() - 1).min(), (c / v if v else np.nan)


bc = met(curves["벤치마크"])[0]
rows = []
for name in strats:
    c, v, mdd, sh = met(curves[name])
    rows.append(dict(전략=name, CAGR=c, 변동성=v, MDD=mdd, Sharpe=sh, 초과=c - bc, 회전율=turns[name]))
res = pd.DataFrame(rows)
pd.set_option("display.width", 220)
print("=" * 96)
print(f"백테스트 ({curves['벤치마크'].index[0].date()} ~ {curves['벤치마크'].index[-1].date()}, "
      f"{len(rebal)}회 월간 리밸런싱, 비용 왕복 {COST:.2%})")
print(res.to_string(index=False, formatters={
    "CAGR": "{:+.2%}".format, "변동성": "{:.1%}".format, "MDD": "{:.1%}".format,
    "Sharpe": "{:.3f}".format, "초과": "{:+.2%}p".format, "회전율": "{:.0%}".format}))

navdf = pd.DataFrame(curves)
yr = pd.concat([navdf.iloc[[0]], navdf.resample("YE").last()])
rets = yr.pct_change().dropna()
rets.index = rets.index.year
ex = rets.sub(rets["벤치마크"], axis=0).drop(columns=["벤치마크"]) * 100
print("\n연도별 벤치마크 대비 초과 (%p)")
print(ex.round(1).to_string())
print("\n  초과 연수 / 2025제외 연평균")
for c in ex.columns:
    print(f"    {c:26s} {(ex[c] > 0).sum()}/{len(ex)}년   {ex[c].drop(index=2025, errors='ignore').mean():+6.2f}%p")

best = res[res.전략 != "벤치마크"].sort_values("Sharpe", ascending=False).iloc[0]
print(f"\n=== 거래비용 민감도 (Sharpe 최고: {best.전략})")
for cst in (0.0, 0.003, 0.006, 0.010):
    adj_c = best.CAGR + (COST - cst) * best.회전율 * (252 / HOLD)
    print(f"    왕복 {cst:.2%}: CAGR {adj_c:+.2%} (초과 {adj_c - bc:+.2%}p)")

navdf.to_csv(OUT_DIR / "모멘텀백테스트v2_NAV.csv", encoding="utf-8-sig")
res.to_csv(OUT_DIR / "모멘텀백테스트v2_요약.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/모멘텀백테스트v2_요약.csv, _NAV.csv")
db.close()
