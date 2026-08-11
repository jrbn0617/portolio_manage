"""공매도 신호의 예측력 검증 — H3(공매도는 정보거래).

flow_signal_ic.py와 동일한 프레임(IC + 스프레드 + Newey-West + FM 통제)에,
공매도 제도 구간을 분리해 결과가 구간별로 일관되는지까지 본다.
금지구간(2020-03-16~2021-05-02, 2023-11-06~2025-03-30)은 제외한다.
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
WINDOWS = [1, 5, 20, 60]
FORWARDS = [5, 20, 60]
FORM_EVERY = 5
ZSCORE_LOOKBACK = 250
MIN_STOCKS = 50
OUT_DIR = REPO_DIR / "reference"

# 공매도 제도 구간 (실측 확인: 금지기간 월 거래대금이 정상기 대비 1/10 이하)
REGIMES = [
    ("정상1", "2019-01-01", "2020-03-13"),
    ("부분허용", "2021-05-03", "2023-11-05"),   # 코스피200·코스닥150만 허용 = 우리 유니버스와 일치
    ("정상2", "2025-03-31", "2026-08-10"),
]

db = SessionLocal()
UNIV_CTE = """
with u as (select distinct instrument_id iid from index_memberships
           where index_name in ('KOSDAQ150','KOSPI200'))
"""


def pivot(sql, params, col):
    df = pd.read_sql(text(sql), db.bind, params=params)
    p = df.pivot(index="date", columns="instrument_id", values=col).sort_index()
    p.index = pd.to_datetime(p.index)
    return p


print("데이터 로딩 중...")
adj = pivot(UNIV_CTE + """
select d.date,d.instrument_id,d.adj_close::float value from dividend_adjusted_prices d
 join u on u.iid=d.instrument_id where d.period='D' and d.date>=:s""", {"s": START}, "value")
raw = pivot(UNIV_CTE + """
select p.date,p.instrument_id,p.raw_close::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s""", {"s": START}, "value")
sv = pivot(UNIV_CTE + """
select s.date,s.instrument_id,s.short_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}, "value").reindex_like(adj)
tv = pivot(UNIV_CTE + """
select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}, "value").reindex_like(adj)
print(f"  가격 {adj.shape}, 공매도금액 {sv.notna().sum().sum():,}셀, 거래대금 {tv.notna().sum().sum():,}셀")

sh = pd.read_sql(text(UNIV_CTE + """
select m.date,m.instrument_id,m.value::float value from monthly_fundamentals m
 join u on u.iid=m.instrument_id where m.metric='shares_outstanding_monthly'"""), db.bind)
sh = sh.pivot(index="date", columns="instrument_id", values="value").sort_index()
sh.index = pd.to_datetime(sh.index)
mcap = raw.reindex_like(adj) * sh.reindex(index=adj.index, columns=adj.columns).ffill()

mem = pd.read_sql(text("""
select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

# ---- 신호 ----
print("신호 계산 중...")
signals = {}
for n in WINDOWS:
    ratio = sv.rolling(n, min_periods=n).sum() / tv.rolling(n, min_periods=n).sum()
    signals[f"ratio_{n}"] = ratio
    mu = ratio.rolling(ZSCORE_LOOKBACK, min_periods=ZSCORE_LOOKBACK // 2).mean()
    sd = ratio.rolling(ZSCORE_LOOKBACK, min_periods=ZSCORE_LOOKBACK // 2).std()
    signals[f"z_{n}"] = (ratio - mu) / sd.replace(0, np.nan)
ratio5 = sv.rolling(5, min_periods=5).sum() / tv.rolling(5, min_periods=5).sum()
signals["chg_5v60"] = ratio5 - ratio5.rolling(60, min_periods=30).mean()
print(f"  신호 {len(signals)}종")

forwards = {h: adj.shift(-h) / adj - 1 for h in FORWARDS}
momentum = adj.shift(21) / adj.shift(273) - 1
logsize = np.log(mcap.replace(0, np.nan))
past_ret = {n: adj / adj.shift(n) - 1 for n in WINDOWS}


def spearman(a, b):
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    if np.std(ra) == 0 or np.std(rb) == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def nw_t(x, lag):
    x = np.asarray(x)[~np.isnan(x)]
    T = len(x)
    if T < 5:
        return np.nan
    mu = x.mean()
    e = x - mu
    var = (e @ e) / T
    for j in range(1, min(lag, T - 1) + 1):
        var += 2 * (1 - j / (lag + 1)) * ((e[j:] @ e[:-j]) / T)
    return float(mu / np.sqrt(var / T)) if var > 0 else np.nan


def z(v):
    v = v.astype(float)
    s = v.std()
    return (v - v.mean()) / s if s else v * 0


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


trading_days = list(adj.index)
all_form = trading_days[max(WINDOWS) + ZSCORE_LOOKBACK :: FORM_EVERY]

results = []
for regime, r_start, r_end in REGIMES + [("전체(금지제외)", None, None)]:
    if r_start is None:
        form_dates = [d for d in all_form
                      if any(pd.Timestamp(s) <= d <= pd.Timestamp(e) for _, s, e in REGIMES)]
    else:
        form_dates = [d for d in all_form if pd.Timestamp(r_start) <= d <= pd.Timestamp(r_end)]
    if len(form_dates) < 10:
        continue

    rec, fm_rec = {}, {}
    for d in form_dates:
        univ = universe_at(d)
        cols = [c for c in adj.columns if c in univ]
        if len(cols) < MIN_STOCKS:
            continue
        mom_d, size_d = momentum.loc[d, cols], logsize.loc[d, cols]
        for h in FORWARDS:
            fwd = forwards[h].loc[d, cols]
            for name, mat in signals.items():
                sig = mat.loc[d, cols]
                ok = sig.notna() & fwd.notna()
                if ok.sum() < MIN_STOCKS:
                    continue
                s_, f_ = sig[ok].values, fwd[ok].values
                ic = spearman(s_, f_)
                if np.isnan(ic):
                    continue
                k = max(1, round(ok.sum() * 0.2))
                order = np.argsort(-s_)
                rec.setdefault((name, h), []).append(
                    (ic, f_[order[:k]].mean() - f_[order[-k:]].mean()))

                win_n = int(name.split("_")[1]) if name.startswith(("ratio", "z")) else 5
                rev_d = past_ret[win_n].loc[d, cols]
                ok3 = ok & mom_d.notna() & size_d.notna() & rev_d.notna()
                if ok3.sum() >= MIN_STOCKS:
                    X = np.column_stack([np.ones(ok3.sum()), z(sig[ok3]).values,
                                         z(mom_d[ok3]).values, z(size_d[ok3]).values,
                                         z(rev_d[ok3]).values])
                    try:
                        c, *_ = np.linalg.lstsq(X, fwd[ok3].values, rcond=None)
                        fm_rec.setdefault((name, h), []).append(c[1])
                    except np.linalg.LinAlgError:
                        pass

    for (name, h), vals in rec.items():
        ics = np.array([v[0] for v in vals])
        sprs = np.array([v[1] for v in vals])
        lag = max(1, h // FORM_EVERY)
        results.append(dict(
            regime=regime, signal=name, forward=h, n_periods=len(ics),
            mean_ic=ics.mean(), ic_t_nw=nw_t(ics, lag), ic_pos_pct=(ics > 0).mean(),
            mean_spread_pct=sprs.mean() * 100, spread_t_nw=nw_t(sprs, lag),
            fm_rev_t_nw=nw_t(fm_rec.get((name, h), [np.nan]), lag)))
    print(f"  {regime}: 형성일 {len(form_dates)}개 완료")

df = pd.DataFrame(results)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 200)

whole = df[df.regime == "전체(금지제외)"].sort_values("ic_t_nw")
print("\n" + "=" * 100)
print("전체(금지구간 제외) — IC t-stat 하위 8 (H3 예측: 공매도↑ → 이후 부진 = 음수)")
print(whole.head(8).to_string(index=False))
print("\n전체(금지구간 제외) — IC t-stat 상위 5")
print(whole.tail(5).iloc[::-1].to_string(index=False))

best = whole.iloc[0]["signal"], int(whole.iloc[0]["forward"])
print(f"\n=== 최강 신호({best[0]}, fwd{best[1]})의 제도 구간별 안정성")
print(df[(df.signal == best[0]) & (df.forward == best[1])][
    ["regime", "n_periods", "mean_ic", "ic_t_nw", "mean_spread_pct", "fm_rev_t_nw"]].to_string(index=False))

df.to_csv(OUT_DIR / "공매도신호_IC_요약.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/공매도신호_IC_요약.csv ({len(df)}행)")
db.close()
