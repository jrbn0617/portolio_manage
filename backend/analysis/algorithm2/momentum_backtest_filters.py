"""모멘텀 6개월 + 수급필터 주체별 비교 백테스트.

momentum_backtest_v2.py는 필터로 '기관합계'만 썼다. 여기서 주체 조합을 넓혀 확인한다:
  기관합계 하위40% / 외국인 하위40% / 기관+외국인 합산 하위40% / 개인 상위40%
(방향은 각 주체 신호의 부호를 따름 — 기관·외국인은 역지표라 하위, 개인은 순지표라 상위)

나머지 조건은 v2와 동일: 6개월 모멘텀(skip 1개월), 월간 리밸런싱, 동일가중, 왕복 0.30%.
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
SKIP, MOM_N = 21, 126     # 6개월
HOLD = 20
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
raw = {inv: pivot(UNIV + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type=:i""", {"s": START, "i": inv}).reindex_like(adj)
       for inv in ["기관합계", "외국인", "개인"]}
raw["기관+외국인"] = raw["기관합계"].add(raw["외국인"], fill_value=0)

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

den = amt.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
fsig = {k: v.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / den for k, v in raw.items()}
mom = adj.shift(SKIP) / adj.shift(SKIP + MOM_N) - 1
dret = adj.pct_change(fill_method=None)

days = list(adj.index)
rebal = list(range(MOM_N + SKIP + 60, len(days) - 1, HOLD))
print(f"리밸런싱 {len(rebal)}회 ({days[rebal[0]].date()} ~ {days[rebal[-1]].date()})\n")

# 필터 정의: (신호키, 하위를 고를지 여부)
FILTERS = {"기관합계": ("기관합계", True), "외국인": ("외국인", True),
           "기관+외국인": ("기관+외국인", True), "개인": ("개인", False)}


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


def mk(fkey, low, n):
    def f(d, cols):
        if fkey is None:
            m = mom.loc[d, cols]
            return list(m[m.notna()].nlargest(n).index)
        fl = fsig[fkey].loc[d, cols]
        fl = fl[fl.notna()]
        if len(fl) < n * 2:
            return []
        k = max(n, int(len(fl) * FLOW_KEEP))
        cand = fl.nsmallest(k).index if low else fl.nlargest(k).index
        m = mom.loc[d, cand]
        m = m[m.notna()]
        return list(m.nlargest(n).index) if len(m) >= n else []
    return f


strats = {"벤치마크": lambda d, c: [x for x in c if not np.isnan(adj.loc[d, x])]}
for n in TOPNS:
    strats[f"모멘텀만·{n}"] = mk(None, True, n)
for fname, (fkey, low) in FILTERS.items():
    tag = "하위" if low else "상위"
    for n in TOPNS:
        strats[f"{fname}{tag}40%+모멘텀·{n}"] = mk(fkey, low, n)

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
rows = [dict(전략=n, CAGR=met(curves[n])[0], 변동성=met(curves[n])[1], MDD=met(curves[n])[2],
             Sharpe=met(curves[n])[3], 초과=met(curves[n])[0] - bc, 회전율=turns[n]) for n in strats]
res = pd.DataFrame(rows).sort_values("Sharpe", ascending=False)
pd.set_option("display.width", 220)
print("=" * 96)
print(f"모멘텀6개월 + 수급필터 주체별 비교 (월간 리밸런싱, 비용 {COST:.2%})")
print(res.to_string(index=False, formatters={
    "CAGR": "{:+.2%}".format, "변동성": "{:.1%}".format, "MDD": "{:.1%}".format,
    "Sharpe": "{:.3f}".format, "초과": "{:+.2%}p".format, "회전율": "{:.0%}".format}))

navdf = pd.DataFrame(curves)
yr = pd.concat([navdf.iloc[[0]], navdf.resample("YE").last()])
rets = yr.pct_change().dropna()
rets.index = rets.index.year
ex = rets.sub(rets["벤치마크"], axis=0).drop(columns=["벤치마크"]) * 100
print("\n연도별 초과(%p) — 30종목 버전만")
cols30 = [c for c in ex.columns if c.endswith("·30")]
print(ex[cols30].round(1).to_string())
print("\n  초과연수 / 2025제외 연평균 (전체)")
for c in ex.columns:
    print(f"    {c:26s} {(ex[c] > 0).sum()}/{len(ex)}년   "
          f"{ex[c].drop(index=2025, errors='ignore').mean():+6.2f}%p")

navdf.to_csv(OUT_DIR / "모멘텀필터비교_NAV.csv", encoding="utf-8-sig")
res.to_csv(OUT_DIR / "모멘텀필터비교_요약.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/모멘텀필터비교_요약.csv, _NAV.csv")
db.close()
