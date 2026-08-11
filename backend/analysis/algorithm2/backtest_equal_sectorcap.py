"""동일가중 + 섹터 카운트캡 튜닝 (섹터제약 필수 전제).

동일가중에서는 '섹터 합산 50% 상한'이 곧 '섹터당 10종목'이라 20종목 포트폴리오에선
거의 발동하지 않는다. 따라서 실질적인 섹터제약은 카운트캡이고, 앞선 검증에서 2종목은
모멘텀 상위 종목을 강제로 버리게 해 신호를 크게 희석시켰다(팩셋섹터 Sharpe 0.927->0.699).

여기서는 캡을 2/3/4/5로 풀어가며 '분산은 확보하되 신호 손실이 최소인' 지점을 찾는다.
세 분류필드(케이산업/팩셋섹터/팩셋산업) 모두 확인.

고정: 코스피200+코스닥150, 기관 순매수 하위40% -> 6개월 모멘텀 상위 20종목,
      동일가중, 월간 리밸런싱, 손절 -10%(익일시가), 왕복 0.30%.
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
CAPS = [2, 3, 4, 5, None]
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
amt = pivot(ALL + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)
inst = pivot(ALL + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type='기관합계'""", {"s": START}).reindex_like(adj)
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


def sector_of(iid, field):
    g = sec.at[iid, field] if iid in sec.index else None
    return "미분류" if g is None or (isinstance(g, float) and np.isnan(g)) else g


def run(field, cap):
    navs, dates, prev, tn, nsec, dropped = [1.0], [days[rebal[0]]], {}, [], [], []
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

        if cap is None:
            picks = list(m.index[:TOP_N])
        else:
            picks, cnt, skipped = [], {}, 0
            for iid in m.index:
                if len(picks) >= TOP_N:
                    break
                g = sector_of(iid, field)
                if cnt.get(g, 0) >= cap:
                    skipped += 1
                    continue
                picks.append(iid)
                cnt[g] = cnt.get(g, 0) + 1
            if len(picks) < TOP_N:
                continue
            dropped.append(skipped)
        nsec.append(len({sector_of(p, field) for p in picks}))

        w = {p: 1 / len(picks) for p in picks}
        turn = sum(abs(w.get(k, 0) - prev.get(k, 0)) for k in set(w) | set(prev)) / 2
        tn.append(turn)
        navs[-1] *= (1 - turn * COST)

        idx = [col_ix[p] for p in picks]
        wv = np.full(len(idx), 1 / len(idx))
        entry, base = A[ri, idx], navs[-1]
        end = min(ri + HOLD, len(days) - 1)
        frozen, ffrom = np.full(len(idx), np.nan), np.full(len(idx), 10 ** 9)
        for j in range(ri + 1, end + 1):
            v = np.where(np.isnan(A[j, idx] / entry), 1.0, A[j, idx] / entry)
            for k in np.where(np.isnan(frozen) & (v <= 1 - STOP))[0]:
                gap = 1.0
                if j < len(days) - 1:
                    o, c = OP[j + 1, idx[k]], CL[j, idx[k]]
                    if not np.isnan(o) and not np.isnan(c) and c:
                        gap = o / c
                frozen[k], ffrom[k] = v[k] * gap, j + 1
            cur = v.copy()
            for k in np.where(~np.isnan(frozen))[0]:
                if j >= ffrom[k]:
                    cur[k] = frozen[k]
            navs.append(base * float((wv * cur).sum()))
            dates.append(days[j])
        prev = w
    return (pd.Series(navs, index=dates), np.mean(tn), np.mean(nsec),
            np.mean(dropped) if dropped else 0.0)


def met(s):
    r = s.pct_change().dropna()
    y = (s.index[-1] - s.index[0]).days / 365.25
    c = s.iloc[-1] ** (1 / y) - 1
    v = r.std() * np.sqrt(252)
    return c, v, (s / s.cummax() - 1).min(), (c / v if v else np.nan)


rows, curves = [], {}
base_curve, _, _, _ = run("krx_sector", None)
curves["제약없음"] = base_curve
for f, flabel in FIELDS.items():
    for cap in CAPS:
        if cap is None:
            continue
        label = f"{flabel} {cap}종목"
        nav, t, ns, dr = run(f, cap)
        curves[label] = nav
        c, v, mdd, sh = met(nav)
        rows.append(dict(필드=flabel, 캡=cap, CAGR=c, 변동성=v, MDD=mdd, Sharpe=sh,
                         회전율=t, 평균섹터수=ns, 월평균제외종목=dr))
c, v, mdd, sh = met(base_curve)
rows.append(dict(필드="(제약없음)", 캡=np.nan, CAGR=c, 변동성=v, MDD=mdd, Sharpe=sh,
                 회전율=np.nan, 평균섹터수=np.nan, 월평균제외종목=0.0))
res = pd.DataFrame(rows)

pd.set_option("display.width", 200)
print("=" * 96)
print("동일가중 + 섹터 카운트캡 (코스피200+코스닥150, 손절10%)")
print(f"{'필드':10s} {'캡':>4s} {'CAGR':>9s} {'변동성':>8s} {'MDD':>8s} {'Sharpe':>7s} "
      f"{'회전':>5s} {'섹터수':>6s} {'제외종목':>7s}")
for _, r in res.iterrows():
    cap = "-" if np.isnan(r.캡) else f"{int(r.캡)}"
    tn = "-" if np.isnan(r.회전율) else f"{r.회전율*100:.0f}%"
    ns = "-" if np.isnan(r.평균섹터수) else f"{r.평균섹터수:.1f}"
    print(f"{r.필드:10s} {cap:>4s} {r.CAGR:>+8.2%} {r.변동성:>8.1%} {r.MDD:>8.1%} "
          f"{r.Sharpe:>7.3f} {tn:>5s} {ns:>6s} {r.월평균제외종목:>7.1f}")

best = res[res.캡.notna()].sort_values("Sharpe", ascending=False).iloc[0]
print(f"\n최선(섹터제약 포함): {best.필드} {int(best.캡)}종목 — "
      f"CAGR {best.CAGR:+.2%}, MDD {best.MDD:.1%}, Sharpe {best.Sharpe:.3f}")

res.to_csv(OUT_DIR / "동일가중_섹터캡튜닝.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(curves).to_csv(OUT_DIR / "동일가중_섹터캡튜닝_NAV.csv", encoding="utf-8-sig")
print(f"저장: {OUT_DIR}/동일가중_섹터캡튜닝.csv, _NAV.csv")
db.close()
