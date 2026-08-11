"""개인·기관 수급 신호 결합 검증 — 스프레드를 키울 수 있는가.

배경: 단독 검증에서 개인=순지표(t=+3.08), 기관=역지표(t=-4.26)로 나왔고 둘의 상관은
-0.49(완전중복 아님), 기관과 외국인은 -0.04(사실상 독립)였다. 부호가 반대이고 중복이
아니므로 결합이 의미 있을 수 있다.

주의: 이전 세션에서 "같은 방향 성분 둘을 FM계수 비율로 선형결합"했다가 최강 단일성분보다
못한 전례가 있다. 그래서 선형결합만이 아니라 **더블소트 교집합**(평균이 아니라 집중)을
같이 본다.

비교 대상:
  단독   : 개인 / -기관 / -외국인
  선형   : z(개인) - z(기관), 랭크평균, 3주체
  더블소트: 개인 상위 ∩ 기관 하위 (long) vs 개인 하위 ∩ 기관 상위 (short), 분위 10/20/30%
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
WINDOW = 60          # 단독검증에서 가장 강했던 누적창
FORWARDS = [20, 60]
FORM_EVERY = 5
MIN_STOCKS = 50
QUANTILES = [0.10, 0.20, 0.30]
OUT_DIR = REPO_DIR / "reference"

db = SessionLocal()
UNIV = """
with u as (select distinct instrument_id iid from index_memberships
           where index_name in ('KOSDAQ150','KOSPI200'))
"""


def pivot(sql, params, col="value"):
    df = pd.read_sql(text(sql), db.bind, params=params)
    p = df.pivot(index="date", columns="instrument_id", values=col).sort_index()
    p.index = pd.to_datetime(p.index)
    return p


print("데이터 로딩 중...")
adj = pivot(UNIV + """select d.date,d.instrument_id,d.adj_close::float value
 from dividend_adjusted_prices d join u on u.iid=d.instrument_id
 where d.period='D' and d.date>=:s""", {"s": START})
raw = pivot(UNIV + """select p.date,p.instrument_id,p.raw_close::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s""", {"s": START})
amt = pivot(UNIV + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)

flow = {}
for inv in ["개인", "외국인", "기관합계"]:
    flow[inv] = pivot(UNIV + """select it.date,it.instrument_id,it.net_value::float value
     from investor_trading it join u on u.iid=it.instrument_id
     where it.date>=:s and it.investor_type=:inv""", {"s": START, "inv": inv}).reindex_like(adj)

sh = pd.read_sql(text(UNIV + """select m.date,m.instrument_id,m.value::float value
 from monthly_fundamentals m join u on u.iid=m.instrument_id
 where m.metric='shares_outstanding_monthly'"""), db.bind)
sh = sh.pivot(index="date", columns="instrument_id", values="value").sort_index()
sh.index = pd.to_datetime(sh.index)
mcap = raw.reindex_like(adj) * sh.reindex(index=adj.index, columns=adj.columns).ffill()

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

# 거래대금 대비 누적 순매수 (단독검증에서 유일하게 견고했던 정규화)
denom = amt.rolling(WINDOW, min_periods=WINDOW).sum()
sig = {inv: flow[inv].rolling(WINDOW, min_periods=WINDOW).sum() / denom for inv in flow}
print(f"  신호 준비 완료 (거래대금대비 {WINDOW}일 누적)")

forwards = {h: adj.shift(-h) / adj - 1 for h in FORWARDS}
momentum = adj.shift(21) / adj.shift(273) - 1
logsize = np.log(mcap.replace(0, np.nan))
past_ret = adj / adj.shift(WINDOW) - 1


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


days = list(adj.index)
form_dates = days[WINDOW + 280 :: FORM_EVERY]
print(f"형성일 {len(form_dates)}개 ({form_dates[0].date()} ~ {form_dates[-1].date()})")

ic_rec, sp_rec, fm_rec = {}, {}, {}
ds_rec = {}   # 더블소트: (q, forward) -> [(spread, n_long, n_short)]

for d in form_dates:
    univ = universe_at(d)
    cols = [c for c in adj.columns if c in univ]
    if len(cols) < MIN_STOCKS:
        continue

    ind = sig["개인"].loc[d, cols]
    ins = sig["기관합계"].loc[d, cols]
    frn = sig["외국인"].loc[d, cols]
    base_ok = ind.notna() & ins.notna() & frn.notna()
    if base_ok.sum() < MIN_STOCKS:
        continue

    # 결합 신호 정의 (모두 "높을수록 좋다" 방향으로 통일)
    zi, zs, zf = z(ind[base_ok]), z(ins[base_ok]), z(frn[base_ok])
    ri = pd.Series(ind[base_ok]).rank(pct=True).values
    rs = pd.Series(-ins[base_ok]).rank(pct=True).values
    rf = pd.Series(-frn[base_ok]).rank(pct=True).values
    combos = {
        "개인 단독": zi,
        "-기관 단독": -zs,
        "-외국인 단독": -zf,
        "z(개인)-z(기관)": zi - zs,
        "랭크평균(개인,-기관)": (ri + rs) / 2,
        "랭크평균(3주체)": (ri + rs + rf) / 3,
    }
    mom_d = momentum.loc[d, cols][base_ok].values
    size_d = logsize.loc[d, cols][base_ok].values
    rev_d = past_ret.loc[d, cols][base_ok].values

    for h in FORWARDS:
        fwd = forwards[h].loc[d, cols][base_ok].values
        valid = ~np.isnan(fwd)
        if valid.sum() < MIN_STOCKS:
            continue
        f_ = fwd[valid]

        for name, s_all in combos.items():
            s_ = np.asarray(s_all)[valid]
            if np.isnan(s_).any():
                m = ~np.isnan(s_)
                if m.sum() < MIN_STOCKS:
                    continue
                s_, ff = s_[m], f_[m]
            else:
                ff = f_
            ic = spearman(s_, ff)
            if np.isnan(ic):
                continue
            k = max(1, round(len(s_) * 0.2))
            order = np.argsort(-s_)
            ic_rec.setdefault((name, h), []).append(ic)
            sp_rec.setdefault((name, h), []).append(ff[order[:k]].mean() - ff[order[-k:]].mean())

            ctrl_ok = valid.copy()
            cm, cs, cr = mom_d[valid], size_d[valid], rev_d[valid]
            good = ~(np.isnan(cm) | np.isnan(cs) | np.isnan(cr)) & ~np.isnan(np.asarray(s_all)[valid])
            if good.sum() >= MIN_STOCKS:
                X = np.column_stack([np.ones(good.sum()), z(np.asarray(s_all)[valid][good]),
                                     z(cm[good]), z(cs[good]), z(cr[good])])
                try:
                    c, *_ = np.linalg.lstsq(X, f_[good], rcond=None)
                    fm_rec.setdefault((name, h), []).append(c[1])
                except np.linalg.LinAlgError:
                    pass

        # 더블소트 교집합: 개인 상위 ∩ 기관 하위 vs 개인 하위 ∩ 기관 상위
        pi = pd.Series(ind[base_ok].values[valid]).rank(pct=True).values
        ps = pd.Series(ins[base_ok].values[valid]).rank(pct=True).values
        for q in QUANTILES:
            long_m = (pi >= 1 - q) & (ps <= q)
            short_m = (pi <= q) & (ps >= 1 - q)
            if long_m.sum() >= 3 and short_m.sum() >= 3:
                ds_rec.setdefault((q, h), []).append(
                    (f_[long_m].mean() - f_[short_m].mean(), int(long_m.sum()), int(short_m.sum())))

print("집계 중...\n")
rows = []
for (name, h), ics in ic_rec.items():
    lag = max(1, h // FORM_EVERY)
    sps = sp_rec[(name, h)]
    fms = fm_rec.get((name, h), [])
    rows.append(dict(signal=name, forward=h, n=len(ics),
                     mean_ic=np.mean(ics), ic_t=nw_t(ics, lag),
                     spread_pct=np.mean(sps) * 100, spread_t=nw_t(sps, lag),
                     fm_t=nw_t(fms, lag) if fms else np.nan))
df = pd.DataFrame(rows)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 200)

for h in FORWARDS:
    print(f"=== forward {h}거래일 — 단독 vs 결합 (상하위 20% 스프레드)")
    print(df[df.forward == h].sort_values("spread_pct", ascending=False).to_string(index=False))
    print()

ds_rows = []
for (q, h), vals in sorted(ds_rec.items()):
    lag = max(1, h // FORM_EVERY)
    sp = [v[0] for v in vals]
    ds_rows.append(dict(quantile=f"{int(q*100)}%", forward=h, n_periods=len(sp),
                        spread_pct=np.mean(sp) * 100, spread_t=nw_t(sp, lag),
                        avg_n_long=np.mean([v[1] for v in vals]),
                        avg_n_short=np.mean([v[2] for v in vals])))
ds = pd.DataFrame(ds_rows)
print("=== 더블소트 교집합 (개인 상위 ∩ 기관 하위  vs  개인 하위 ∩ 기관 상위)")
print(ds.to_string(index=False))

df.to_csv(OUT_DIR / "수급결합_선형_요약.csv", index=False, encoding="utf-8-sig")
ds.to_csv(OUT_DIR / "수급결합_더블소트_요약.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/수급결합_선형_요약.csv, 수급결합_더블소트_요약.csv")
db.close()
