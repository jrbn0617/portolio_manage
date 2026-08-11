"""유동시총 30%캡 + 동일섹터 2종목 제한 + 동일섹터 합산 50%캡.

유니버스는 직전 비교에서 최선이었던 코스피200+코스닥150 고정.
전략: 기관 순매수 하위40% -> 6개월 모멘텀 상위, 월간 리밸런싱, 손절 -10%.

제약 적용 순서:
  1) 종목선정 — 모멘텀 순으로 채우되 같은 섹터가 이미 2종목이면 건너뛰고 다음 종목으로
                (카운트캡. 20종목을 채우려면 최소 10개 섹터가 필요)
  2) 가중치   — 유동시총 비례 -> 종목당 30% 상한 -> 섹터합산 50% 상한을 수렴할 때까지 반복

섹터 필드는 결과가 갈릴 수 있어 팩셋섹터/팩셋산업/케이산업 세 가지 모두 확인한다.
비교군으로 제약 없는 동일가중 / 유동시총20%캡도 함께 출력.
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
MAX_W, MAX_GROUP_W, MAX_PER_SECTOR = 0.30, 0.50, 2
FIELDS = {"krx_sector": "케이산업", "sector": "팩셋섹터", "industry": "팩셋산업"}
OUT_DIR = REPO_DIR / "reference"

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
float_cap = mcap * ff.reindex(index=adj.index, columns=adj.columns).ffill() / 100.0

sec = pd.read_sql(text(ALL + """select i.id, i.sector, i.industry, i.krx_sector
 from instruments i join u on u.iid=i.id"""), db.bind).set_index("id")

memb = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
memb["as_of_date"] = pd.to_datetime(memb["as_of_date"])
snaps = sorted(memb["as_of_date"].unique())
snap_members = {s: set(memb.loc[memb.as_of_date == s, "instrument_id"]) for s in snaps}

fsig = inst.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / \
    amt.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
mom = adj.shift(SKIP) / adj.shift(SKIP + MOM_N) - 1

days = list(adj.index)
rebal = list(range(MOM_N + SKIP + FLOW_WINDOW, len(days) - 1, HOLD))
A, OP, CL = adj.values, p_open.values, p_close.values
col_ix = {c: i for i, c in enumerate(adj.columns)}
print(f"리밸런싱 {len(rebal)}회\n")


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


def cap_weights(w: dict, groups: dict | None, max_w: float, max_gw: float | None) -> dict:
    """종목상한과 그룹합산상한을 함께 만족시킬 때까지 반복 조정."""
    w = {k: v for k, v in w.items()}
    for _ in range(200):
        changed = False
        over = {k for k, v in w.items() if v > max_w + 1e-9}
        if over:
            excess = sum(w[k] - max_w for k in over)
            for k in over:
                w[k] = max_w
            rest = {k: v for k, v in w.items() if k not in over}
            tot = sum(rest.values())
            if tot > 0:
                for k in rest:
                    w[k] += excess * rest[k] / tot
            changed = True
        if groups and max_gw is not None:
            gsum: dict[str, float] = {}
            for k, v in w.items():
                gsum[groups[k]] = gsum.get(groups[k], 0) + v
            bad = {g for g, s in gsum.items() if s > max_gw + 1e-9}
            if bad:
                excess = sum(gsum[g] - max_gw for g in bad)
                for k in list(w):
                    if groups[k] in bad:
                        w[k] *= max_gw / gsum[groups[k]]
                rest = {k: v for k, v in w.items() if groups[k] not in bad}
                tot = sum(rest.values())
                if tot > 0:
                    for k in rest:
                        w[k] += excess * rest[k] / tot
                changed = True
        if not changed:
            break
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()} if tot > 0 else w


def _sector_of(iid, field):
    g = sec.at[iid, field] if iid in sec.index else None
    return "미분류" if g is None or (isinstance(g, float) and np.isnan(g)) else g


def run(mode, count_field=None, group_field=None, max_w=0.20):
    """count_field: 섹터당 종목수 제한에 쓸 필드(None이면 제한 없음)
       group_field: 섹터 합산비중 상한에 쓸 필드(None이면 상한 없음)"""
    navs, dates, prev_w, tn, nsec = [1.0], [days[rebal[0]]], {}, [], []
    for ri in rebal:
        d = days[ri]
        cols = [c for c in adj.columns if c in universe_at(d)]
        if len(cols) < 100:
            continue
        fl = fsig.loc[d, cols].dropna()
        if len(fl) < TOP_N * 2:
            continue
        cand = fl.nsmallest(max(TOP_N, int(len(fl) * FLOW_KEEP))).index
        m = mom.loc[d, cand].dropna().sort_values(ascending=False)
        if len(m) < TOP_N:
            continue

        if count_field is None:
            picks = list(m.index[:TOP_N])
        else:
            picks, cnt = [], {}
            for iid in m.index:
                if len(picks) >= TOP_N:
                    break
                g = _sector_of(iid, count_field)
                if cnt.get(g, 0) >= MAX_PER_SECTOR:
                    continue
                picks.append(iid)
                cnt[g] = cnt.get(g, 0) + 1
            if len(picks) < TOP_N:
                continue

        groups = {p: _sector_of(p, group_field) for p in picks} if group_field else None
        if groups:
            nsec.append(len(set(groups.values())))

        if mode == "동일가중":
            w = {p: 1 / len(picks) for p in picks}
        else:
            fc = float_cap.loc[d, picks]
            if fc.notna().sum() < len(picks) * 0.8 or fc.sum() <= 0:
                w = {p: 1 / len(picks) for p in picks}
            else:
                fc = fc.fillna(fc.median())
                w = (fc / fc.sum()).to_dict()
                w = cap_weights(w, groups, max_w, MAX_GROUP_W if groups else None)

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
        for j in range(ri + 1, end + 1):
            v = np.where(np.isnan(A[j, idx] / entry), 1.0, A[j, idx] / entry)
            for k in np.where(np.isnan(frozen) & (v <= 1 - STOP))[0]:
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
    return pd.Series(navs, index=dates), np.mean(tn), (np.mean(nsec) if nsec else np.nan)


SPECS = [("동일가중(제약없음)", "동일가중", None, None, 0.20),
         ("유동시총20%캡(제약없음)", "유동시총", None, None, 0.20)]
# 이번 요청: 카운트캡 없이 가중치 상한만 (30% 종목 + 50% 섹터)
for f in FIELDS:
    SPECS.append((f"유동시총30%캡+{FIELDS[f]}50%캡", "유동시총", None, f, 0.30))
# 직전 실행분(카운트캡 포함)도 비교용으로 유지
for f in FIELDS:
    SPECS.append((f"[+2종목캡] 유동시총30%캡+{FIELDS[f]}50%캡", "유동시총", f, f, 0.30))

curves, turns, secs = {}, {}, {}
for label, mode, cf, gf, mw in SPECS:
    curves[label], turns[label], secs[label] = run(mode, cf, gf, mw)
    print(f"  {label} 완료")

idx_px = pd.read_sql(text("""select i.ticker,p.date,p.close::float c from prices p
 join instruments i on i.id=p.instrument_id
 where i.asset_type='index' and p.period='D' and i.ticker in ('KOSPI200','KOSDAQ150')"""), db.bind)
idx_px = idx_px.pivot(index="date", columns="ticker", values="c")
idx_px.index = pd.to_datetime(idx_px.index)
lo = max(max(c.index[0] for c in curves.values()), idx_px.index[0])
hi = min(min(c.index[-1] for c in curves.values()), idx_px.index[-1])
idx_px = idx_px.loc[(idx_px.index >= lo) & (idx_px.index <= hi)].ffill()
idx_px = idx_px / idx_px.iloc[0]

allc = {k: (v.loc[(v.index >= lo) & (v.index <= hi)] / v.loc[v.index >= lo].iloc[0])
        for k, v in curves.items()}
allc["[지수] KOSPI200"] = idx_px["KOSPI200"]
allc["[지수] 50:50"] = (idx_px["KOSPI200"] + idx_px["KOSDAQ150"]) / 2
allc = pd.DataFrame(allc).ffill().dropna()


def met(s):
    r = s.pct_change().dropna()
    y = (s.index[-1] - s.index[0]).days / 365.25
    c = s.iloc[-1] ** (1 / y) - 1
    v = r.std() * np.sqrt(252)
    return c, v, (s / s.cummax() - 1).min(), (c / v if v else np.nan)


print("\n" + "=" * 104)
print(f"섹터 제약 비교 ({allc.index[0].date()} ~ {allc.index[-1].date()}, 코스피200+코스닥150, 손절10%)")
print(f"{'':36s} {'CAGR':>9s} {'변동성':>8s} {'MDD':>8s} {'Sharpe':>7s} {'최종':>7s} {'회전':>5s} {'섹터수':>6s}")
rows = []
for k in allc.columns:
    c, v, mdd, sh = met(allc[k])
    t, ns = turns.get(k, np.nan), secs.get(k, np.nan)
    print(f"{k:36s} {c:>+8.2%} {v:>8.1%} {mdd:>8.1%} {sh:>7.3f} {allc[k].iloc[-1]:>6.2f}x "
          f"{('%.0f%%' % (t*100)) if not np.isnan(t) else '-':>5s} "
          f"{('%.1f' % ns) if not np.isnan(ns) else '-':>6s}")
    rows.append(dict(전략=k, CAGR=c, 변동성=v, MDD=mdd, Sharpe=sh, 회전율=t, 평균섹터수=ns))

pd.DataFrame(rows).to_csv(OUT_DIR / "섹터제약_비교_요약.csv", index=False, encoding="utf-8-sig")
allc.to_csv(OUT_DIR / "섹터제약_비교_NAV.csv", encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/섹터제약_비교_요약.csv, _NAV.csv")
db.close()
