"""수급 필터(주체별) + 모멘텀 백테스트 — 기관/외국인/개인 3주체 비교.

방향은 앞선 IC 검증 결과를 따른다:
  기관합계 = 역지표 -> 순매수 '하위'를 산다
  외국인   = 약한 역지표(비단조) -> 하위
  개인     = 순지표 -> 순매수 '상위'를 산다

각 주체에 대해 (a) 수급 단독 20종목, (b) 수급 하위/상위 40%로 좁힌 뒤 모멘텀 상위 20종목
두 가지를 돌리고, 모멘텀 단독·유니버스 동일가중과 비교한다.
동일가중, 60거래일(분기) 보유, 왕복 0.30% 거래비용.
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
FLOW_WINDOW = 60
HOLD_DAYS = 60
TOP_N = 20
FLOW_KEEP = 0.40
COST_ROUNDTRIP = 0.0030
OUT_DIR = REPO_DIR / "reference"

# 주체 -> 매수할 방향 ("low" = 순매수 하위를 산다, "high" = 상위를 산다)
INVESTOR_DIR = {"기관합계": "low", "외국인": "low", "개인": "high"}

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
flow_raw = {inv: pivot(UNIV + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type=:inv""", {"s": START, "inv": inv}).reindex_like(adj)
            for inv in INVESTOR_DIR}

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

denom = amt.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
flow_sig = {inv: flow_raw[inv].rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / denom
            for inv in INVESTOR_DIR}
momentum = adj.shift(21) / adj.shift(273) - 1
daily_ret = adj.pct_change(fill_method=None)

days = list(adj.index)
rebal_idx = list(range(FLOW_WINDOW + 280, len(days) - 1, HOLD_DAYS))
print(f"리밸런싱 {len(rebal_idx)}회 ({days[rebal_idx[0]].date()} ~ {days[rebal_idx[-1]].date()})")


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


def pick(d, cols, kind, inv=None):
    m = momentum.loc[d, cols]
    if kind == "bench":
        return [c for c in cols if not np.isnan(adj.loc[d, c])]
    if kind == "mom":
        return list(m[m.notna()].nlargest(TOP_N).index)

    f = flow_sig[inv].loc[d, cols]
    f = f[f.notna()]
    if len(f) < TOP_N * 2:
        return []
    low = INVESTOR_DIR[inv] == "low"
    if kind == "flow":
        return list(f.nsmallest(TOP_N).index if low else f.nlargest(TOP_N).index)
    if kind == "flow_mom":
        k = max(TOP_N, int(len(f) * FLOW_KEEP))
        cand = f.nsmallest(k).index if low else f.nlargest(k).index
        mm = m[cand]
        mm = mm[mm.notna()]
        return list(mm.nlargest(TOP_N).index) if len(mm) >= TOP_N else []
    return []


STRATS = [("벤치마크", "bench", None), ("모멘텀만", "mom", None)]
for inv in INVESTOR_DIR:
    tag = "하위" if INVESTOR_DIR[inv] == "low" else "상위"
    STRATS.append((f"{inv} {tag}20", "flow", inv))
    STRATS.append((f"{inv} {tag}40%+모멘텀", "flow_mom", inv))

curves, turnovers = {}, {}
for label, kind, inv in STRATS:
    nav, nav_dates, prev_w, tno = [1.0], [days[rebal_idx[0]]], {}, []
    for ri in rebal_idx:
        d = days[ri]
        cols = [c for c in adj.columns if c in universe_at(d)]
        if len(cols) < TOP_N * 2:
            continue
        picks = pick(d, cols, kind, inv)
        if not picks:
            continue
        w = {p: 1.0 / len(picks) for p in picks}
        turn = sum(abs(w.get(k, 0) - prev_w.get(k, 0)) for k in set(w) | set(prev_w)) / 2
        tno.append(turn)
        nav[-1] *= (1 - turn * COST_ROUNDTRIP)

        cur = dict(w)
        for j in range(ri + 1, min(ri + HOLD_DAYS, len(days) - 1) + 1):
            dj = days[j]
            r = daily_ret.loc[dj, list(cur)].fillna(0.0)
            nav.append(nav[-1] * (1 + float((pd.Series(cur) * r).sum())))
            nav_dates.append(dj)
            grown = {k: cur[k] * (1 + float(r.get(k, 0.0))) for k in cur}
            tot = sum(grown.values())
            cur = {k: v / tot for k, v in grown.items()} if tot > 0 else cur
        prev_w = cur
    curves[label] = pd.Series(nav, index=nav_dates)
    turnovers[label] = np.mean(tno) if tno else np.nan


def metrics(nav):
    r = nav.pct_change().dropna()
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(252)
    return dict(CAGR=cagr, vol=vol, MDD=(nav / nav.cummax() - 1).min(),
                Sharpe=cagr / vol if vol else np.nan)


bench_m = metrics(curves["벤치마크"])
rows = []
for label, _, _ in STRATS:
    m = metrics(curves[label])
    rows.append(dict(전략=label, CAGR=m["CAGR"], 변동성=m["vol"], MDD=m["MDD"],
                     Sharpe=m["Sharpe"], 초과CAGR=m["CAGR"] - bench_m["CAGR"],
                     회전율=turnovers[label]))
res = pd.DataFrame(rows)

pd.set_option("display.width", 220)
print("\n" + "=" * 100)
print(f"백테스트 ({curves['벤치마크'].index[0].date()} ~ {curves['벤치마크'].index[-1].date()}, "
      f"{len(rebal_idx)}회 리밸런싱, 비용 왕복 {COST_ROUNDTRIP:.2%})")
print(res.to_string(index=False, formatters={
    "CAGR": "{:+.2%}".format, "변동성": "{:.2%}".format, "MDD": "{:.2%}".format,
    "Sharpe": "{:.3f}".format, "초과CAGR": "{:+.2%}p".format, "회전율": "{:.0%}".format}))

# 연도별 초과 — 특정 연도 쏠림 탐지
navdf = pd.DataFrame(curves)
yr = pd.concat([navdf.iloc[[0]], navdf.resample("YE").last()])
rets = yr.pct_change().dropna()
rets.index = rets.index.year
ex = rets.sub(rets["벤치마크"], axis=0).drop(columns=["벤치마크"]) * 100

print("\n연도별 벤치마크 대비 초과 (%p) — 한 해 쏠림 여부가 핵심")
print(ex.round(1).to_string())
print("\n  2025 제외 연평균 초과 / 초과 연수")
for c in ex.columns:
    wo = ex[c].drop(index=2025, errors="ignore")
    print(f"    {c:22s} {wo.mean():+6.2f}%p   ({(ex[c] > 0).sum()}/{len(ex)}년)")

navdf.to_csv(OUT_DIR / "수급모멘텀_백테스트_NAV.csv", encoding="utf-8-sig")
res.to_csv(OUT_DIR / "수급모멘텀_백테스트_요약.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/수급모멘텀_백테스트_요약.csv, _NAV.csv")
db.close()
