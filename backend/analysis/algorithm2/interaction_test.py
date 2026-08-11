"""모멘텀 x 기관수급 상호작용 검증.

백테스트에서 모멘텀 단독(Sharpe 0.411)·수급 단독(0.403)이 합쳐지면 0.936이 나왔다.
이 도약이 진짜 상호작용인지, 아니면 튜닝 과정의 우연인지 확인한다.

가설: 기관 매수가 이미 들어온 종목은 모멘텀이 가격에 반영돼 소진된다.
      -> 저수급(기관이 안 산) 구간에서 모멘텀 스프레드가 크고,
         고수급(기관이 많이 산) 구간에서 작거나 사라져야 한다.

검증 두 가지:
  1) 5x5 이중정렬 — 수급분위별 모멘텀 Q5-Q1 스프레드
  2) Fama-MacBeth 교차항 회귀 — forward ~ z(mom) + z(flow) + z(mom)*z(flow)
     상호작용이 실재하면 교차항 계수가 유의하게 음수여야 한다.

형성일 20거래일 간격(비중복), forward 20거래일.
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
SKIP, MOM_N, FLOW_W = 21, 126, 60
FORWARD, FORM_EVERY = 20, 20
NQ = 5
MIN_STOCKS = 150
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
amt = pivot(ALL + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)
inst = pivot(ALL + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type='기관합계'""", {"s": START}).reindex_like(adj)
mcap = pivot(ALL + """select p.date,p.instrument_id,p.market_cap::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s and p.market_cap is not null""",
             {"s": START}).reindex_like(adj)

memb = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
memb["as_of_date"] = pd.to_datetime(memb["as_of_date"])
snaps = sorted(memb["as_of_date"].unique())
snap_members = {s: set(memb.loc[memb.as_of_date == s, "instrument_id"]) for s in snaps}

flow = inst.rolling(FLOW_W, min_periods=FLOW_W).sum() / amt.rolling(FLOW_W, min_periods=FLOW_W).sum()
mom = adj.shift(SKIP) / adj.shift(SKIP + MOM_N) - 1
fwd = adj.shift(-FORWARD) / adj - 1
logsize = np.log(mcap.replace(0, np.nan))
rev = adj / adj.shift(FLOW_W) - 1

days = list(adj.index)
form = days[MOM_N + SKIP + FLOW_W :: FORM_EVERY]
print(f"형성일 {len(form)}개 (비중복, {form[0].date()} ~ {form[-1].date()})\n")


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


def z(v):
    v = np.asarray(v, float)
    s = np.nanstd(v)
    return (v - np.nanmean(v)) / s if s else v * 0


cell = {(i, j): [] for i in range(NQ) for j in range(NQ)}
inter_coef, mom_coef, flow_coef = [], [], []

for d in form:
    cols = [c for c in adj.columns if c in universe_at(d)]
    if len(cols) < MIN_STOCKS:
        continue
    f = flow.loc[d, cols].values
    m = mom.loc[d, cols].values
    r = fwd.loc[d, cols].values
    sz = logsize.loc[d, cols].values
    rv = rev.loc[d, cols].values
    ok = ~np.isnan(f) & ~np.isnan(m) & ~np.isnan(r)
    if ok.sum() < MIN_STOCKS:
        continue
    f_, m_, r_ = f[ok], m[ok], r[ok]
    bench = r_.mean()

    fq = pd.qcut(pd.Series(f_).rank(method="first"), NQ, labels=False).values
    for i in range(NQ):
        sel = fq == i
        if sel.sum() < NQ * 3:
            continue
        mq = pd.qcut(pd.Series(m_[sel]).rank(method="first"), NQ, labels=False).values
        rr = r_[sel]
        for j in range(NQ):
            s2 = mq == j
            if s2.sum() >= 3:
                cell[(i, j)].append(rr[s2].mean() - bench)

    ok2 = ok & ~np.isnan(sz) & ~np.isnan(rv)
    if ok2.sum() >= MIN_STOCKS:
        zm, zf = z(mom.loc[d, cols].values[ok2]), z(flow.loc[d, cols].values[ok2])
        X = np.column_stack([np.ones(ok2.sum()), zm, zf, zm * zf,
                             z(logsize.loc[d, cols].values[ok2]), z(rev.loc[d, cols].values[ok2])])
        try:
            c, *_ = np.linalg.lstsq(X, fwd.loc[d, cols].values[ok2], rcond=None)
            mom_coef.append(c[1]); flow_coef.append(c[2]); inter_coef.append(c[3])
        except np.linalg.LinAlgError:
            pass


def tstat(v):
    v = np.asarray(v)
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))


print("=" * 84)
print("1) 5x5 이중정렬 — 수급분위 안에서의 모멘텀 초과수익 (%p, 20거래일)")
print(f"  {'':14s} " + " ".join(f"{'모멘텀Q'+str(j+1):>9s}" for j in range(NQ)) + f" {'Q5-Q1':>9s} {'t':>6s}")
rows = []
for i in range(NQ):
    vals = [np.mean(cell[(i, j)]) * 100 if cell[(i, j)] else np.nan for j in range(NQ)]
    sp = [a - b for a, b in zip(cell[(i, NQ - 1)], cell[(i, 0)])] if cell[(i, 0)] else []
    spread = np.mean(sp) * 100 if sp else np.nan
    t = tstat(sp) if len(sp) > 2 else np.nan
    lbl = f"수급Q{i+1}" + ("(안 산)" if i == 0 else "(많이 산)" if i == NQ - 1 else "")
    print(f"  {lbl:14s} " + " ".join(f"{v:>+9.2f}" for v in vals) + f" {spread:>+9.2f} {t:>+6.2f}")
    rows.append(dict(수급분위=i + 1, **{f"모멘텀Q{j+1}": vals[j] for j in range(NQ)},
                     모멘텀스프레드=spread, t=t))

print("\n2) Fama-MacBeth 교차항 회귀")
print("   forward ~ z(모멘텀) + z(수급) + z(모멘텀)xz(수급) + z(사이즈) + z(단기수익률)")
n = len(inter_coef)
for lbl, v in [("모멘텀", mom_coef), ("수급", flow_coef), ("교차항", inter_coef)]:
    print(f"   {lbl:8s} 계수 {np.mean(v):+.5f}  t = {tstat(v):+.2f}  (n={n})")
print("\n   * 교차항이 유의하게 음수면 '수급이 높을수록 모멘텀 효과가 약해진다' = 상호작용 실재")

pd.DataFrame(rows).to_csv(OUT_DIR / "상호작용_이중정렬.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/상호작용_이중정렬.csv")
db.close()
