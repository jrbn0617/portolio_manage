"""종목 단위 3개월 수급모멘텀 -> 다음달 수익률, 10분위 실제 수익률.

앞선 검증은 IC/t-stat 위주였는데 여기서는 "수급이 좋은 종목의 실제 수익률이 얼마인가"를
분위별 실측치로 본다. 그리고 아직 안 본 조합인 **기관+외국인 합산**을 추가한다
(개인 + 외국인 + 기관 ~ 0인 제로섬이라 합산은 사실상 -개인에 가깝지만, 실무에서 흔히
쓰는 구도라 직접 확인).

앞서 2024년 전후로 개인·외국인 신호의 부호가 뒤집힌 게 확인됐으므로 구간을 쪼개서도 본다.

신호: 60거래일 누적 순매수 / 같은 기간 거래대금
대상: 이후 20거래일 수익률, 형성일 20거래일 간격(비중복)
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
WINDOW = 60          # 3개월 누적
FORWARD = 20         # 다음달
FORM_EVERY = 20      # 비중복 형성일
NQ = 10
MIN_STOCKS = 100
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

denom = amt.rolling(WINDOW, min_periods=WINDOW).sum()
sig = {k: v.rolling(WINDOW, min_periods=WINDOW).sum() / denom for k, v in raw.items()}
fwd = adj.shift(-FORWARD) / adj - 1

days = list(adj.index)
form_dates = days[WINDOW + 280 :: FORM_EVERY]
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
    period = "2020-2023" if d.year <= 2023 else "2024-2026"
    for name, mat in sig.items():
        s = mat.loc[d, cols]
        ok = s.notna() & f.notna()
        if ok.sum() < MIN_STOCKS:
            continue
        sv, fv = s[ok].values, f[ok].values
        r = pd.Series(sv).rank(pct=True).values
        for q in range(NQ):
            lo, hi = q / NQ, (q + 1) / NQ
            m = (r > lo) & (r <= hi) if q > 0 else (r <= hi)
            if m.sum() >= 5:
                rec.setdefault((name, q), []).append((fv[m].mean(), bench, period))

print("=" * 96)
print(f"3개월 수급모멘텀 -> 다음달 수익률 (10분위, 형성일 {len(form_dates)}회 비중복)")
rows = []
for (name, q), vals in rec.items():
    ret = np.array([v[0] for v in vals])
    bch = np.array([v[1] for v in vals])
    ex = (ret - bch) * 100
    t = ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))
    per = np.array([v[2] for v in vals])
    exA, exB = ex[per == "2020-2023"], ex[per == "2024-2026"]
    rows.append(dict(신호=name, 분위=q + 1, n=len(ex),
                     실제수익률=ret.mean() * 100, 초과=ex.mean(), t=t,
                     승률=(ex > 0).mean() * 100,
                     초과_2023이전=exA.mean() if len(exA) else np.nan,
                     초과_2024이후=exB.mean() if len(exB) else np.nan))
df = pd.DataFrame(rows)
pd.set_option("display.width", 220)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")

bench_avg = np.mean([v[1] for vals in rec.values() for v in vals]) * 100
print(f"  (벤치마크: 유니버스 동일가중 평균 {bench_avg:+.2f}% / 20거래일)\n")
for name in ["기관+외국인", "기관합계", "외국인", "개인"]:
    sub = df[df.신호 == name].sort_values("분위")
    if sub.empty:
        continue
    print(f"[{name}]  D1=순매수 최하위 ... D10=최상위")
    print(f"  {'분위':5s} {'실제수익':>8s} {'초과':>8s} {'t':>6s} {'승률':>6s} | {'~2023':>8s} {'2024~':>8s}")
    for _, r in sub.iterrows():
        mark = " ***" if abs(r.t) >= 2 else ""
        print(f"  D{int(r.분위):<4d} {r.실제수익률:+7.2f}% {r.초과:+7.3f}%p {r.t:+6.2f} {r.승률:5.1f}% | "
              f"{r.초과_2023이전:+7.3f} {r.초과_2024이후:+7.3f}{mark}")
    d1, d10 = sub[sub.분위 == 1].iloc[0], sub[sub.분위 == 10].iloc[0]
    print(f"  -> D10-D1 스프레드 {d10.초과 - d1.초과:+.3f}%p "
          f"(~2023 {d10.초과_2023이전 - d1.초과_2023이전:+.3f} / 2024~ {d10.초과_2024이후 - d1.초과_2024이후:+.3f})\n")

df.to_csv(OUT_DIR / "종목수급모멘텀_10분위.csv", index=False, encoding="utf-8-sig")
print(f"저장: {OUT_DIR}/종목수급모멘텀_10분위.csv")
db.close()
