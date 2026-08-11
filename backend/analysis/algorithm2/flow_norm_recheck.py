"""정규화 방식 재검증 — 시가총액 백필 이후.

이전 검증에서 mcap 정규화(순매수/시가총액)는 IC는 음수인데 FM 통제 시 부호가 양수로
뒤집혀 "신뢰할 수 없다"고 판단했다. 그때 시가총액은 raw_close x 월간상장주식수 근사치라
월중 분할·증자가 반영되지 않았다. 2026-08-11에 DataGuide 시가총액을 백필해 정확한 값이
생겼으므로(유니버스 커버리지 100%, pykrx와 원 단위까지 일치 확인), 근사치가 원인이었는지
확인한다.

세 정규화를 나란히 비교:
  mcap_exact  = 누적 순매수 / prices.market_cap        (신규, 정확)
  mcap_approx = 누적 순매수 / (raw_close x 월간상장주식수)  (기존, 근사)
  amt         = 누적 순매수 / 누적 거래대금               (기존, 유일하게 견고했던 것)
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
INVESTORS = ["개인", "외국인", "기관합계"]
WINDOWS = [1, 5, 20, 60]
FORWARDS = [20, 60]
FORM_EVERY = 5
MIN_STOCKS = 50
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
raw = pivot(UNIV + """select p.date,p.instrument_id,p.raw_close::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s""", {"s": START})
mcap_exact = pivot(UNIV + """select p.date,p.instrument_id,p.market_cap::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s and p.market_cap is not null""",
                   {"s": START}).reindex_like(adj)
amt = pivot(UNIV + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)
flow = {inv: pivot(UNIV + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type=:inv""", {"s": START, "inv": inv}).reindex_like(adj)
        for inv in INVESTORS}

sh = pd.read_sql(text(UNIV + """select m.date,m.instrument_id,m.value::float value
 from monthly_fundamentals m join u on u.iid=m.instrument_id
 where m.metric='shares_outstanding_monthly'"""), db.bind)
sh = sh.pivot(index="date", columns="instrument_id", values="value").sort_index()
sh.index = pd.to_datetime(sh.index)
mcap_approx = raw.reindex_like(adj) * sh.reindex(index=adj.index, columns=adj.columns).ffill()

print(f"  mcap_exact  {mcap_exact.notna().sum().sum():,}셀")
print(f"  mcap_approx {mcap_approx.notna().sum().sum():,}셀")
both = mcap_exact.notna() & mcap_approx.notna()
rel = ((mcap_approx - mcap_exact).abs() / mcap_exact)[both]
print(f"  두 값 상대오차: 중앙값 {np.nanmedian(rel.values):.4%}, "
      f"95%p {np.nanpercentile(rel.values[~np.isnan(rel.values)], 95):.4%}, "
      f"1%초과 셀 {(rel > 0.01).sum().sum():,}")

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

forwards = {h: adj.shift(-h) / adj - 1 for h in FORWARDS}
momentum = adj.shift(21) / adj.shift(273) - 1
logsize = np.log(mcap_exact.replace(0, np.nan))     # 통제변수도 정확한 값으로
past_ret = {n: adj / adj.shift(n) - 1 for n in WINDOWS}


def spearman(a, b):
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    if np.std(ra) == 0 or np.std(rb) == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


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


def z(v):
    v = np.asarray(v, dtype=float)
    s = np.nanstd(v)
    return (v - np.nanmean(v)) / s if s else v * 0


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


print("신호 계산 중...")
signals = {}
for inv in INVESTORS:
    for n in WINDOWS:
        cum = flow[inv].rolling(n, min_periods=n).sum()
        signals[(inv, "mcap_exact", n)] = cum / mcap_exact
        signals[(inv, "mcap_approx", n)] = cum / mcap_approx
        signals[(inv, "amt", n)] = cum / amt.rolling(n, min_periods=n).sum()

days = list(adj.index)
form_dates = days[60 + 280 :: FORM_EVERY]
print(f"형성일 {len(form_dates)}개\n계산 중...")

ic_rec, sp_rec, fm_rec = {}, {}, {}
for d in form_dates:
    univ = universe_at(d)
    cols = [c for c in adj.columns if c in univ]
    if len(cols) < MIN_STOCKS:
        continue
    mom_d, size_d = momentum.loc[d, cols].values, logsize.loc[d, cols].values
    for h in FORWARDS:
        fwd = forwards[h].loc[d, cols].values
        if np.sum(~np.isnan(fwd)) < MIN_STOCKS:
            continue
        for key, mat in signals.items():
            inv, norm, n = key
            s = mat.loc[d, cols].values
            rev = past_ret[n].loc[d, cols].values
            ok = ~np.isnan(s) & ~np.isnan(fwd)
            if ok.sum() < MIN_STOCKS:
                continue
            s_, f_ = s[ok], fwd[ok]
            ic = spearman(s_, f_)
            if np.isnan(ic):
                continue
            k = max(1, round(len(s_) * 0.2))
            order = np.argsort(-s_)
            ic_rec.setdefault((key, h), []).append(ic)
            sp_rec.setdefault((key, h), []).append(f_[order[:k]].mean() - f_[order[-k:]].mean())

            g = ok & ~np.isnan(mom_d) & ~np.isnan(size_d) & ~np.isnan(rev)
            if g.sum() >= MIN_STOCKS:
                X = np.column_stack([np.ones(g.sum()), z(s[g]), z(mom_d[g]), z(size_d[g]), z(rev[g])])
                try:
                    c, *_ = np.linalg.lstsq(X, fwd[g], rcond=None)
                    fm_rec.setdefault((key, h), []).append(c[1])
                except np.linalg.LinAlgError:
                    pass

rows = []
for (key, h), ics in ic_rec.items():
    inv, norm, n = key
    lag = max(1, h // FORM_EVERY)
    fms = fm_rec.get((key, h), [])
    rows.append(dict(investor=inv, norm=norm, window=n, forward=h, n=len(ics),
                     mean_ic=np.mean(ics), ic_t=nw_t(ics, lag),
                     spread_pct=np.mean(sp_rec[(key, h)]) * 100,
                     fm_t=nw_t(fms, lag) if fms else np.nan))
df = pd.DataFrame(rows)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 200)

print("\n" + "=" * 96)
print("정규화 3종 비교 — 부호 안정성이 핵심 (IC와 FM 부호가 같아야 신뢰 가능)")
for inv in ["기관합계", "개인"]:
    for h in FORWARDS:
        sub = df[(df.investor == inv) & (df.forward == h)]
        if sub.empty:
            continue
        print(f"\n[{inv}] forward {h}일")
        piv = sub.pivot(index="window", columns="norm", values=["ic_t", "fm_t"])
        print(piv.to_string())
        # 부호 불일치 탐지
        for _, r in sub.iterrows():
            if not np.isnan(r.fm_t) and np.sign(r.ic_t) != np.sign(r.fm_t) and abs(r.ic_t) > 1.5:
                print(f"    !! 부호뒤집힘: {r['norm']} {int(r['window'])}일창 "
                      f"IC t={r.ic_t:+.2f} vs FM t={r.fm_t:+.2f}")

df.to_csv(OUT_DIR / "수급_정규화_재검증.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/수급_정규화_재검증.csv")
db.close()
