"""롱·숏 사이드 분리 검증 — 스프레드가 어느 쪽에서 나오는가.

배경: 수급 신호의 상하위 20% 스프레드는 유의했지만(기관 0.9%p/20일), 그게 롱 사이드의
초과수익인지 숏 사이드의 하락인지는 확인하지 않았다. 롱온리로 쓰려면 롱 사이드가 실제로
벤치마크를 이겨야 한다.

방법: 형성일마다 신호로 5분위를 나누고, 각 분위의 forward 수익률을 **유니버스 동일가중
평균 대비 초과수익**으로 환산한다. 벤치마크를 실제 지수가 아니라 유니버스 평균으로 잡는
이유는, 신호가 이 유니버스 안에서의 상대 랭킹이라 사이즈·시장 효과를 섞지 않기 위해서다.

주체(개인/외국인/기관합계) x forward(20/60일) x 5분위로 본다. 단조성(분위가 올라갈수록
수익률이 단조 증가/감소하는지)도 같이 확인 — 한쪽 극단만 튀는 신호는 신뢰도가 낮다.
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
WINDOW = 60
FORWARDS = [20, 60]
FORM_EVERY = 5
NQ = 5
MIN_STOCKS = 50
INVESTORS = ["개인", "외국인", "기관합계"]
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
 where it.date>=:s and it.investor_type=:inv""", {"s": START, "inv": inv}).reindex_like(adj)
        for inv in INVESTORS}

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

denom = amt.rolling(WINDOW, min_periods=WINDOW).sum()
sig = {inv: flow[inv].rolling(WINDOW, min_periods=WINDOW).sum() / denom for inv in INVESTORS}
forwards = {h: adj.shift(-h) / adj - 1 for h in FORWARDS}


def nw_t(x, lag):
    x = np.asarray([v for v in x if not np.isnan(v)])
    T = len(x)
    if T < 5:
        return np.nan
    e = x - x.mean()
    var = (e @ e) / T
    for j in range(1, min(lag, T - 1) + 1):
        var += 2 * (1 - j / (lag + 1)) * ((e[j:] @ e[:-j]) / T)
    return float(x.mean() / np.sqrt(var / T)) if var > 0 else np.nan


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


days = list(adj.index)
form_dates = days[WINDOW + 280 :: FORM_EVERY]
print(f"형성일 {len(form_dates)}개 ({form_dates[0].date()} ~ {form_dates[-1].date()})\n")

# (investor, forward, quintile) -> [초과수익, ...]
rec: dict[tuple, list] = {}
bench_rec: dict[int, list] = {h: [] for h in FORWARDS}

for d in form_dates:
    univ = universe_at(d)
    cols = [c for c in adj.columns if c in univ]
    if len(cols) < MIN_STOCKS:
        continue
    for h in FORWARDS:
        fwd_all = forwards[h].loc[d, cols]
        if fwd_all.notna().sum() < MIN_STOCKS:
            continue
        bench = fwd_all.mean()          # 유니버스 동일가중 평균 = 벤치마크
        bench_rec[h].append(bench)
        for inv in INVESTORS:
            s = sig[inv].loc[d, cols]
            ok = s.notna() & fwd_all.notna()
            if ok.sum() < MIN_STOCKS:
                continue
            sv, fv = s[ok].values, fwd_all[ok].values
            ranks = pd.Series(sv).rank(pct=True).values
            for q in range(NQ):
                lo, hi = q / NQ, (q + 1) / NQ
                m = (ranks > lo) & (ranks <= hi) if q > 0 else (ranks <= hi)
                if m.sum() >= 5:
                    rec.setdefault((inv, h, q), []).append(fv[m].mean() - bench)

print("집계 중...\n")
rows = []
for (inv, h, q), vals in rec.items():
    lag = max(1, h // FORM_EVERY)
    v = np.array(vals)
    rows.append(dict(investor=inv, forward=h, quintile=q + 1, n=len(v),
                     excess_pct=v.mean() * 100, t_nw=nw_t(v, lag),
                     win_rate=(v > 0).mean()))
df = pd.DataFrame(rows)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 200)

QLBL = {1: "Q1(최하위)", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5(최상위)"}
for h in FORWARDS:
    print(f"{'='*92}")
    print(f"forward {h}거래일 — 분위별 초과수익 (유니버스 동일가중 평균 대비)")
    bm = np.mean(bench_rec[h]) * 100
    print(f"  (참고) 벤치마크 자체 평균수익률: {bm:+.2f}%")
    for inv in INVESTORS:
        sub = df[(df.investor == inv) & (df.forward == h)].sort_values("quintile")
        if sub.empty:
            continue
        print(f"\n  [{inv}]  순매수 {WINDOW}일 누적 / 거래대금")
        print(f"    {'분위':12s} {'초과수익':>9s} {'t(NW)':>8s} {'승률':>7s}")
        for _, r in sub.iterrows():
            flag = ""
            if abs(r.t_nw) >= 2:
                flag = "  ***" if r.t_nw > 0 else "  ***(음)"
            print(f"    {QLBL[int(r.quintile)]:12s} {r.excess_pct:+8.3f}%p {r.t_nw:+8.2f} {r.win_rate*100:6.1f}%{flag}")
        q1 = sub[sub.quintile == 1].iloc[0]
        q5 = sub[sub.quintile == 5].iloc[0]
        print(f"    -> Q5-Q1 스프레드 {q5.excess_pct - q1.excess_pct:+.3f}%p "
              f"| 롱사이드 후보: {'Q1' if q1.excess_pct > q5.excess_pct else 'Q5'} "
              f"({max(q1.excess_pct, q5.excess_pct):+.3f}%p, t={q1.t_nw if q1.excess_pct>q5.excess_pct else q5.t_nw:+.2f})")
    print()

df.to_csv(OUT_DIR / "수급_롱숏사이드_분해.csv", index=False, encoding="utf-8-sig")
print(f"저장: {OUT_DIR}/수급_롱숏사이드_분해.csv")
db.close()
