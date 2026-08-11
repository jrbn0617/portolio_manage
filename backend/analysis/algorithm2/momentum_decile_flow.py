"""모멘텀 분위별 (a) 수급 구성 (b) 다음달 수익률 — lookback 3/6/12개월 비교.

지금까지 모멘텀은 12개월짜리를 FM 회귀 통제변수와 백테스트 단독전략(CAGR 4.05%로 부진)
으로만 썼고, 분위별로 뜯어본 적이 없다. 여기서 세 가지를 한 번에 본다:
  1) 모멘텀 lookback 3/6/12개월 각각의 다음달 예측력 (분위별 초과수익)
  2) 각 모멘텀 분위에 어떤 주체의 수급이 실려 있는지 (기관/외국인/개인)
  3) 2024년 전후 구조변화 여부

모멘텀: 최근 1개월 제외(skip=21거래일) 후 N개월 수익률 — 단기반전 오염을 피하는 표준 정의
수급  : 형성일 기준 과거 60거래일 누적 순매수 / 같은 기간 거래대금
대상  : 이후 20거래일 수익률, 형성일 20거래일 간격(비중복)
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
MOM_WINDOWS = {"3개월": 63, "6개월": 126, "12개월": 252}
FLOW_WINDOW = 60
FORWARD = 20
FORM_EVERY = 20
NQ = 5
MIN_STOCKS = 100
INVESTORS = ["기관합계", "외국인", "개인"]
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
flow = {inv: pivot(UNIV + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type=:i""", {"s": START, "i": inv}).reindex_like(adj)
        for inv in INVESTORS}

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

denom = amt.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
flow_sig = {inv: flow[inv].rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / denom
            for inv in INVESTORS}
mom = {k: adj.shift(SKIP) / adj.shift(SKIP + n) - 1 for k, n in MOM_WINDOWS.items()}
fwd = adj.shift(-FORWARD) / adj - 1

days = list(adj.index)
form_dates = days[max(MOM_WINDOWS.values()) + SKIP + 60 :: FORM_EVERY]
print(f"형성일 {len(form_dates)}개 (비중복, {form_dates[0].date()} ~ {form_dates[-1].date()})\n")


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


rec: dict[tuple, list] = {}
for d in form_dates:
    cols = [c for c in adj.columns if c in universe_at(d)]
    if len(cols) < MIN_STOCKS:
        continue
    f = fwd.loc[d, cols]
    if f.notna().sum() < MIN_STOCKS:
        continue
    bench = f.mean()
    period = "~2023" if d.year <= 2023 else "2024~"
    fl = {inv: flow_sig[inv].loc[d, cols] for inv in INVESTORS}

    for mlabel, mmat in mom.items():
        m = mmat.loc[d, cols]
        ok = m.notna() & f.notna()
        if ok.sum() < MIN_STOCKS:
            continue
        mv, fv = m[ok].values, f[ok].values
        flv = {inv: fl[inv][ok].values for inv in INVESTORS}
        r = pd.Series(mv).rank(pct=True).values
        for q in range(NQ):
            lo, hi = q / NQ, (q + 1) / NQ
            msk = (r > lo) & (r <= hi) if q > 0 else (r <= hi)
            if msk.sum() < 5:
                continue
            entry = dict(ret=fv[msk].mean(), bench=bench, period=period,
                         mom=mv[msk].mean())
            for inv in INVESTORS:
                v = flv[inv][msk]
                entry[inv] = np.nanmean(v) if np.sum(~np.isnan(v)) else np.nan
            rec.setdefault((mlabel, q), []).append(entry)

print("=" * 108)
print(f"모멘텀 분위별 다음달 초과수익 + 그 분위의 수급 (형성일 {len(form_dates)}회 비중복)")
bench_all = np.mean([e["bench"] for v in rec.values() for e in v]) * 100
print(f"  벤치마크(유니버스 동일가중) {bench_all:+.2f}% / 20거래일\n")

rows = []
for (mlabel, q), vals in rec.items():
    ret = np.array([e["ret"] for e in vals])
    bch = np.array([e["bench"] for e in vals])
    ex = (ret - bch) * 100
    per = np.array([e["period"] for e in vals])
    t = ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))
    row = dict(모멘텀=mlabel, 분위=q + 1, n=len(ex), 평균모멘텀=np.mean([e["mom"] for e in vals]) * 100,
               초과=ex.mean(), t=t, 승률=(ex > 0).mean() * 100,
               초과_2023이전=ex[per == "~2023"].mean(), 초과_2024이후=ex[per == "2024~"].mean())
    for inv in INVESTORS:
        row[inv] = np.nanmean([e[inv] for e in vals]) * 100
    rows.append(row)
df = pd.DataFrame(rows)
pd.set_option("display.width", 240)

for mlabel in MOM_WINDOWS:
    sub = df[df.모멘텀 == mlabel].sort_values("분위")
    print(f"[모멘텀 {mlabel}]  Q1=최하위 ... Q5=최상위")
    print(f"  {'분위':5s} {'평균모멘텀':>9s} {'익월초과':>9s} {'t':>6s} {'승률':>6s} | "
          f"{'기관':>7s} {'외국인':>7s} {'개인':>7s} | {'~2023':>7s} {'2024~':>7s}")
    for _, r in sub.iterrows():
        mark = " ***" if abs(r.t) >= 2 else ""
        print(f"  Q{int(r.분위):<4d} {r.평균모멘텀:+8.1f}% {r.초과:+8.3f}%p {r.t:+6.2f} {r.승률:5.1f}% | "
              f"{r['기관합계']:+6.2f}% {r['외국인']:+6.2f}% {r['개인']:+6.2f}% | "
              f"{r.초과_2023이전:+6.2f} {r.초과_2024이후:+6.2f}{mark}")
    q1, q5 = sub[sub.분위 == 1].iloc[0], sub[sub.분위 == 5].iloc[0]
    print(f"  -> Q5-Q1 {q5.초과 - q1.초과:+.3f}%p "
          f"(~2023 {q5.초과_2023이전 - q1.초과_2023이전:+.2f} / 2024~ {q5.초과_2024이후 - q1.초과_2024이후:+.2f})\n")

df.to_csv(OUT_DIR / "모멘텀분위_수급_익월수익률.csv", index=False, encoding="utf-8-sig")
print(f"저장: {OUT_DIR}/모멘텀분위_수급_익월수익률.csv")
db.close()
