"""수급(투자자별 순매수) 신호의 예측력 검증 — H1/H2/H4.

유니버스: 코스피200 + 코스닥150 (시점별 편입종목)
신호: 주체(개인/외국인/기관합계) x 정규화(시총대비/거래대금대비/자체z) x 누적창(1/5/20/60일)
forward: 5/20/60거래일, 형성일은 5거래일 간격(주간)
통계: 스피어만 IC + 상하위 20% 스프레드, t-stat은 Newey-West 보정
      (forward가 겹치는 구간은 관측치가 독립이 아니므로 lag=forward/5 로 보정)
"""
import sys
from datetime import date
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
FORWARDS = [5, 20, 60]
FORM_EVERY = 5           # 형성일 간격(거래일)
ZSCORE_LOOKBACK = 250    # 자체 z-score 기준기간
MIN_STOCKS = 50          # 횡단면 최소 종목수
OUT_DIR = REPO_DIR / "reference"

db = SessionLocal()


def pivot(sql: str, params: dict, value_col: str) -> pd.DataFrame:
    df = pd.read_sql(text(sql), db.bind, params=params)
    return df.pivot(index="date", columns="instrument_id", values=value_col).sort_index()


print("데이터 로딩 중...")
UNIV_CTE = """
with u as (select distinct instrument_id iid from index_memberships
           where index_name in ('KOSDAQ150','KOSPI200'))
"""

adj = pivot(UNIV_CTE + """
select d.date, d.instrument_id, d.adj_close::float value from dividend_adjusted_prices d
 join u on u.iid=d.instrument_id where d.period='D' and d.date>=:s""", {"s": START}, "value")
print(f"  배당조정가: {adj.shape[0]}일 x {adj.shape[1]}종목")

raw = pivot(UNIV_CTE + """
select p.date, p.instrument_id, p.raw_close::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s""", {"s": START}, "value")

amt = pivot(UNIV_CTE + """
select s.date, s.instrument_id, s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}, "value")
print(f"  거래대금: {amt.notna().sum().sum():,}셀")

flows = {}
for inv in INVESTORS:
    flows[inv] = pivot(UNIV_CTE + """
    select it.date, it.instrument_id, it.net_value::float value from investor_trading it
     join u on u.iid=it.instrument_id where it.date>=:s and it.investor_type=:inv""",
                       {"s": START, "inv": inv}, "value").reindex(index=adj.index, columns=adj.columns)
    print(f"  {inv}: {flows[inv].notna().sum().sum():,}셀")

# 상장주식수(월간) -> 일별 forward-fill 후 시가총액
sh = pd.read_sql(text(UNIV_CTE + """
select m.date, m.instrument_id, m.value::float value from monthly_fundamentals m
 join u on u.iid=m.instrument_id where m.metric='shares_outstanding_monthly'"""), db.bind)
sh = sh.pivot(index="date", columns="instrument_id", values="value").sort_index()
sh.index = pd.to_datetime(sh.index)
adj.index = pd.to_datetime(adj.index)
raw.index = pd.to_datetime(raw.index)
amt.index = pd.to_datetime(amt.index)
for inv in INVESTORS:
    flows[inv].index = pd.to_datetime(flows[inv].index)
shares = sh.reindex(index=adj.index, columns=adj.columns, method=None).ffill()
mcap = raw.reindex_like(adj) * shares
print(f"  시가총액: {mcap.notna().sum().sum():,}셀")

# 시점별 유니버스 마스크 (반기 스냅샷 -> 그 이후 형성일에 적용)
mem = pd.read_sql(text("""
select index_name, as_of_date, instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snapshots = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snapshots}

trading_days = list(adj.index)
form_dates = trading_days[max(WINDOWS) + ZSCORE_LOOKBACK :: FORM_EVERY]
print(f"형성일 {len(form_dates)}개 ({form_dates[0].date()} ~ {form_dates[-1].date()})")


def universe_at(d):
    elig = [s for s in snapshots if s <= d]
    return snap_members[max(elig)] if elig else set()


# ---- 신호 계산 (벡터화) ----
print("신호 계산 중...")
signals: dict[str, pd.DataFrame] = {}
for inv in INVESTORS:
    f = flows[inv]
    for n in WINDOWS:
        cum = f.rolling(n, min_periods=n).sum()
        signals[f"{inv}_mcap_{n}"] = cum / mcap
        signals[f"{inv}_amt_{n}"] = cum / amt.rolling(n, min_periods=n).sum()
        mu = cum.rolling(ZSCORE_LOOKBACK, min_periods=ZSCORE_LOOKBACK // 2).mean()
        sd = cum.rolling(ZSCORE_LOOKBACK, min_periods=ZSCORE_LOOKBACK // 2).std()
        signals[f"{inv}_z_{n}"] = (cum - mu) / sd.replace(0, np.nan)
print(f"  신호 {len(signals)}종")

forwards = {h: adj.shift(-h) / adj - 1 for h in FORWARDS}
# 통제변수: 12개월 모멘텀(최근 1개월 제외, 일별 근사) / 로그 시총 /
# 신호와 같은 구간의 과거수익률(단기반전) — "기관이 산 종목 = 그 구간 오른 종목"이라
# 신호가 사실은 반전효과의 대용물일 수 있어 반드시 통제해야 한다.
momentum = adj.shift(21) / adj.shift(273) - 1
logsize = np.log(mcap.replace(0, np.nan))
past_ret = {n: adj / adj.shift(n) - 1 for n in WINDOWS}


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = pd.Series(a).rank().values
    rb = pd.Series(b).rank().values
    if np.std(ra) == 0 or np.std(rb) == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def newey_west_t(x: np.ndarray, lag: int) -> float:
    """평균이 0인지에 대한 NW 보정 t값."""
    x = x[~np.isnan(x)]
    T = len(x)
    if T < 5:
        return np.nan
    mu = x.mean()
    e = x - mu
    gamma0 = (e @ e) / T
    var = gamma0
    for j in range(1, min(lag, T - 1) + 1):
        gj = (e[j:] @ e[:-j]) / T
        var += 2 * (1 - j / (lag + 1)) * gj
    if var <= 0:
        return np.nan
    return float(mu / np.sqrt(var / T))


# ---- 형성일별 IC / 스프레드 ----
print("IC 계산 중...")
records: dict[tuple[str, int], list] = {}
fm_records: dict[tuple[str, int], list] = {}
fm_rev_records: dict[tuple[str, int], list] = {}

for d in form_dates:
    univ = universe_at(d)
    if not univ:
        continue
    cols = [c for c in adj.columns if c in univ]
    if len(cols) < MIN_STOCKS:
        continue
    mom_d = momentum.loc[d, cols]
    size_d = logsize.loc[d, cols]

    for h in FORWARDS:
        fwd = forwards[h].loc[d, cols]
        if fwd.notna().sum() < MIN_STOCKS:
            continue
        for name, mat in signals.items():
            sig = mat.loc[d, cols]
            ok = sig.notna() & fwd.notna()
            if ok.sum() < MIN_STOCKS:
                continue
            s, f = sig[ok].values, fwd[ok].values
            ic = spearman(s, f)
            if np.isnan(ic):
                continue
            k = max(1, round(ok.sum() * 0.2))
            order = np.argsort(-s)
            spread = f[order[:k]].mean() - f[order[-k:]].mean()
            records.setdefault((name, h), []).append((d, ic, spread, int(ok.sum())))

            # Fama-MacBeth: forward ~ z(signal) + z(momentum) + z(logsize) [+ z(past_ret_N)]
            win_n = int(name.split("_")[2])
            rev_d = past_ret[win_n].loc[d, cols]
            ok2 = ok & mom_d.notna() & size_d.notna()
            ok3 = ok2 & rev_d.notna()

            def z(v):
                v = v.astype(float)
                sd = v.std()
                return (v - v.mean()) / sd if sd else v * 0

            if ok2.sum() >= MIN_STOCKS:
                X = np.column_stack([np.ones(ok2.sum()), z(sig[ok2]).values,
                                     z(mom_d[ok2]).values, z(size_d[ok2]).values])
                try:
                    coef, *_ = np.linalg.lstsq(X, fwd[ok2].values, rcond=None)
                    fm_records.setdefault((name, h), []).append(coef[1])
                except np.linalg.LinAlgError:
                    pass
            if ok3.sum() >= MIN_STOCKS:
                X = np.column_stack([np.ones(ok3.sum()), z(sig[ok3]).values,
                                     z(mom_d[ok3]).values, z(size_d[ok3]).values,
                                     z(rev_d[ok3]).values])
                try:
                    coef, *_ = np.linalg.lstsq(X, fwd[ok3].values, rcond=None)
                    fm_rev_records.setdefault((name, h), []).append(coef[1])
                except np.linalg.LinAlgError:
                    pass

print("집계 중...")
rows = []
for (name, h), series in records.items():
    ics = np.array([r[1] for r in series])
    sprs = np.array([r[2] for r in series])
    lag = max(1, h // FORM_EVERY)
    inv, norm, win = name.split("_")
    fm = np.array(fm_records.get((name, h), []))
    fmr = np.array(fm_rev_records.get((name, h), []))
    rows.append(dict(
        investor=inv, norm=norm, window=int(win), forward=h, n_periods=len(ics),
        mean_ic=ics.mean(), ic_t_nw=newey_west_t(ics, lag),
        ic_pos_pct=(ics > 0).mean(),
        mean_spread_pct=sprs.mean() * 100, spread_t_nw=newey_west_t(sprs, lag),
        fm_t_nw=newey_west_t(fm, lag) if len(fm) else np.nan,
        fm_rev_t_nw=newey_west_t(fmr, lag) if len(fmr) else np.nan,
        avg_n_stocks=np.mean([r[3] for r in series]),
    ))

df = pd.DataFrame(rows).sort_values("ic_t_nw")
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 200)

print("\n" + "=" * 110)
print("IC t-stat 하위 12 (음의 신호 = 역방향 예측력)")
print(df.head(12).to_string(index=False))
print("\nIC t-stat 상위 12 (양의 신호)")
print(df.tail(12).iloc[::-1].to_string(index=False))

OUT_DIR.mkdir(exist_ok=True)
df.to_csv(OUT_DIR / "수급신호_IC_요약.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/수급신호_IC_요약.csv  (총 {len(df)}조합)")
db.close()
