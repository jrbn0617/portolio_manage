"""유니버스(코스피200 단독 vs +코스닥150) x 가중방식(동일 vs 유동시총) 비교.

전략 고정: 기관 순매수(거래대금대비 60일) 하위40% -> 6개월 모멘텀 상위 20종목,
월간(20거래일) 리밸런싱, 손절 -10%(익일시가, 현금동결), 왕복 0.30%.

유동시총 = prices.market_cap(백필로 정확값 확보) x free_float_ratio/100.
20종목에 유동시총가중을 그대로 쓰면 대형주 한두 종목이 절반을 먹을 수 있어
비중상한 20% 버전도 함께 본다(초과분은 나머지 종목에 시총비례 재분배).

벤치마크는 실제 지수(KOSPI200 / KOSDAQ150 / 50:50)를 쓴다 — 동일가중 유니버스는
이 기간 대형주 강세 탓에 지수 대비 크게 뒤처져 비교 기준으로 부적절했다.
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
SKIP, MOM_N = 21, 126
HOLD, TOP_N = 20, 20
FLOW_WINDOW, FLOW_KEEP = 60, 0.40
COST, STOP = 0.0030, 0.10
MAX_W = 0.20
OUT_DIR = REPO_DIR / "reference"

UNIVERSES = {"코스피200": ("KOSPI200",), "코스닥150": ("KOSDAQ150",),
             "코스피200+코스닥150": ("KOSPI200", "KOSDAQ150")}

db = SessionLocal()
ALL = """
with u as (select distinct instrument_id iid from index_memberships
           where index_name in ('KOSDAQ150','KOSPI200'))
"""


def pivot(sql, params):
    df = pd.read_sql(text(sql), db.bind, params=params)
    p = df.pivot(index="date", columns="instrument_id", values="value").sort_index()
    p.index = pd.to_datetime(p.index)
    return p


print("데이터 로딩 중...")
adj = pivot(ALL + """select d.date,d.instrument_id,d.adj_close::float value
 from dividend_adjusted_prices d join u on u.iid=d.instrument_id
 where d.period='D' and d.date>=:s""", {"s": START})
p_open = pivot(ALL + """select p.date,p.instrument_id,p.open::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s and p.open is not null""",
               {"s": START}).reindex_like(adj)
p_close = pivot(ALL + """select p.date,p.instrument_id,p.close::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s""", {"s": START}).reindex_like(adj)
mcap = pivot(ALL + """select p.date,p.instrument_id,p.market_cap::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s and p.market_cap is not null""",
             {"s": START}).reindex_like(adj)
amt = pivot(ALL + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)
inst = pivot(ALL + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type='기관합계'""", {"s": START}).reindex_like(adj)

ff = pd.read_sql(text(ALL + """select m.date,m.instrument_id,m.value::float value
 from monthly_fundamentals m join u on u.iid=m.instrument_id
 where m.metric='free_float_ratio'"""), db.bind)
ff = ff.pivot(index="date", columns="instrument_id", values="value").sort_index()
ff.index = pd.to_datetime(ff.index)
ff = ff.reindex(index=adj.index, columns=adj.columns).ffill()
float_cap = mcap * ff / 100.0
print(f"  유동시총 커버리지: {float_cap.notna().sum().sum():,} / {mcap.notna().sum().sum():,} "
      f"({float_cap.notna().sum().sum()/mcap.notna().sum().sum():.1%})")

memb = pd.read_sql(text("""select index_name,as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
memb["as_of_date"] = pd.to_datetime(memb["as_of_date"])
snaps = sorted(memb["as_of_date"].unique())
snap_by_idx = {(ix, s): set(memb.loc[(memb.index_name == ix) & (memb.as_of_date == s), "instrument_id"])
               for ix in ("KOSPI200", "KOSDAQ150") for s in snaps}

fsig = inst.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / \
    amt.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
mom = adj.shift(SKIP) / adj.shift(SKIP + MOM_N) - 1

days = list(adj.index)
rebal = list(range(MOM_N + SKIP + FLOW_WINDOW, len(days) - 1, HOLD))
A, OP, CL = adj.values, p_open.values, p_close.values
col_ix = {c: i for i, c in enumerate(adj.columns)}
print(f"리밸런싱 {len(rebal)}회 ({days[rebal[0]].date()} ~ {days[rebal[-1]].date()})\n")


def universe_at(d, idx_names):
    el = [s for s in snaps if s <= d]
    if not el:
        return set()
    s = max(el)
    out = set()
    for ix in idx_names:
        out |= snap_by_idx.get((ix, s), set())
    return out


def cap_weights(w: dict, mx: float) -> dict:
    w = dict(w)
    for _ in range(50):
        over = {k: v for k, v in w.items() if v > mx + 1e-12}
        if not over:
            break
        excess = sum(v - mx for v in over.values())
        for k in over:
            w[k] = mx
        rest = {k: v for k, v in w.items() if k not in over}
        tot = sum(rest.values())
        if tot <= 0:
            break
        for k in rest:
            w[k] += excess * rest[k] / tot
    return w


def run(idx_names, weight_mode):
    navs, dates, prev_w, tn = [1.0], [days[rebal[0]]], {}, []
    for ri in rebal:
        d = days[ri]
        cols = [c for c in adj.columns if c in universe_at(d, idx_names)]
        if len(cols) < 60:
            continue
        fl = fsig.loc[d, cols].dropna()
        if len(fl) < TOP_N * 2:
            continue
        cand = fl.nsmallest(max(TOP_N, int(len(fl) * FLOW_KEEP))).index
        m = mom.loc[d, cand].dropna()
        if len(m) < TOP_N:
            continue
        picks = list(m.nlargest(TOP_N).index)

        if weight_mode == "동일가중":
            w = {p: 1 / len(picks) for p in picks}
        else:
            fc = float_cap.loc[d, picks]
            if fc.notna().sum() < len(picks) * 0.8 or fc.sum() <= 0:
                w = {p: 1 / len(picks) for p in picks}
            else:
                fc = fc.fillna(fc.median())
                w = (fc / fc.sum()).to_dict()
                if weight_mode.endswith("캡)"):
                    w = cap_weights(w, MAX_W)

        turn = sum(abs(w.get(k, 0) - prev_w.get(k, 0)) for k in set(w) | set(prev_w)) / 2
        tn.append(turn)
        navs[-1] *= (1 - turn * COST)

        idx = [col_ix[p] for p in picks]
        wv = np.array([w[p] for p in picks])
        entry = A[ri, idx]
        end = min(ri + HOLD, len(days) - 1)
        frozen = np.full(len(idx), np.nan)
        ffrom = np.full(len(idx), 10 ** 9)
        base = navs[-1]

        last_v = np.ones(len(idx))
        for j in range(ri + 1, end + 1):
            # 보유 중 상장폐지·거래정지로 가격이 끊기면 **마지막 관측치를 유지**한다.
            # 예전엔 np.where(isnan, 1.0, ...)로 채워서 40% 빠진 뒤 정지된 종목이 진입가로
            # 되돌아갔다 — 상방 편향이다.
            raw = A[j, idx] / entry
            v = np.where(np.isnan(raw), last_v, raw)
            last_v = v
            hit = np.isnan(frozen) & (v <= 1 - STOP)
            for k in np.where(hit)[0]:
                gap = 1.0
                if j < len(days) - 1:
                    o, c = OP[j + 1, idx[k]], CL[j, idx[k]]
                    if not np.isnan(o) and not np.isnan(c) and c:
                        gap = o / c
                frozen[k] = v[k] * gap
                ffrom[k] = j + 1
            cur = v.copy()
            for k in np.where(~np.isnan(frozen))[0]:
                if j >= ffrom[k]:
                    cur[k] = frozen[k]
            navs.append(base * float((wv * cur).sum()))
            dates.append(days[j])
        prev_w = w
    return pd.Series(navs, index=dates), np.mean(tn)


curves, turns = {}, {}
for uname, ixs in UNIVERSES.items():
    for wm in ["동일가중", "유동시총가중", f"유동시총가중({int(MAX_W*100)}%캡)"]:
        label = f"{uname} · {wm}"
        curves[label], turns[label] = run(ixs, wm)
        print(f"  {label} 완료")

idx_px = pd.read_sql(text("""select i.ticker,p.date,p.close::float c from prices p
 join instruments i on i.id=p.instrument_id
 where i.asset_type='index' and p.period='D' and i.ticker in ('KOSPI200','KOSDAQ150')"""), db.bind)
idx_px = idx_px.pivot(index="date", columns="ticker", values="c")
idx_px.index = pd.to_datetime(idx_px.index)
lo = max(min(c.index[0] for c in curves.values()), idx_px.index[0])
hi = min(max(c.index[-1] for c in curves.values()), idx_px.index[-1])
idx_px = idx_px.loc[(idx_px.index >= lo) & (idx_px.index <= hi)].ffill()
idx_px = idx_px / idx_px.iloc[0]
idx_px["50:50 혼합"] = (idx_px["KOSPI200"] + idx_px["KOSDAQ150"]) / 2

allc = {}
for k, v in curves.items():
    s = v.loc[(v.index >= lo) & (v.index <= hi)]
    allc[k] = s / s.iloc[0]
for k in idx_px.columns:
    allc[f"[지수] {k}"] = idx_px[k]
allc = pd.DataFrame(allc).ffill().dropna()


def met(s):
    r = s.pct_change().dropna()
    y = (s.index[-1] - s.index[0]).days / 365.25
    c = s.iloc[-1] ** (1 / y) - 1
    v = r.std() * np.sqrt(252)
    return c, v, (s / s.cummax() - 1).min(), (c / v if v else np.nan)


print("\n" + "=" * 92)
print(f"유니버스 x 가중방식 ({allc.index[0].date()} ~ {allc.index[-1].date()}, 손절10%)")
print(f"{'':30s} {'CAGR':>9s} {'변동성':>8s} {'MDD':>8s} {'Sharpe':>7s} {'최종':>7s} {'회전율':>6s}")
rows = []
for k in allc.columns:
    c, v, mdd, sh = met(allc[k])
    t = turns.get(k, np.nan)
    print(f"{k:30s} {c:>+8.2%} {v:>8.1%} {mdd:>8.1%} {sh:>7.3f} {allc[k].iloc[-1]:>6.2f}x "
          f"{('%.0f%%' % (t*100)) if not np.isnan(t) else '-':>6s}")
    rows.append(dict(전략=k, CAGR=c, 변동성=v, MDD=mdd, Sharpe=sh, 최종배수=allc[k].iloc[-1], 회전율=t))

pd.DataFrame(rows).to_csv(OUT_DIR / "유니버스가중_비교_요약.csv", index=False, encoding="utf-8-sig")
allc.to_csv(OUT_DIR / "유니버스가중_비교_NAV.csv", encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/유니버스가중_비교_요약.csv, _NAV.csv")
db.close()
