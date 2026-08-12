"""알고리즘 #3 부수예측 P1~P4 검증 — 선등록 문서 2절.

확정안 백테스트가 유니버스별로 갈렸기 때문에(코스닥 통과·코스피 실패) **믿음 자체가 맞는지**를
포트폴리오 구성과 분리해서 본다. IVOL 5분위 스프레드(Q1 최저변동 − Q5 최고변동)는 롱숏이라
시장수익률이 상쇄된다 — 선등록 2절에서 P1의 교란요인으로 미리 적어둔 "저변동은 하락장에
원래 유리하다"가 이 지표에는 개입하지 않는다.

| | 예측 | 검증 |
|---|---|---|
| P1 | 공매도 금지구간에 스프레드 확대 | 제도 구간별 분리 |
| P2 | 코스닥 > 코스피 | 유니버스별 비교 |
| P3 | 개인 순매수와 복권성이 양의 상관 | 형성일 횡단면 스피어만 (2018-12~) |
| P4 | 소형주에서 강함 | 시총 3분위별 분리 |

형성일은 확정안과 같은 분기말, forward도 다음 분기말까지로 **비중복**이다.
시가총액은 raw_close × shares_outstanding_monthly 근사(월간 상장주식수라 월중 분할·증자
미반영). P4는 이 근사 위에서 읽는다.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402

IVOL_WINDOW, IVOL_MIN_OBS = 60, 45
LIQ_WINDOW, LIQ_DROP = 60, 0.20
N_Q = 5
START = "2018-12-01"          # 신호 계산용 로딩 시작
HOLDOUT_START = "2020-01-01"  # 이 날짜 이전 **성과**는 홀드아웃 — CLAUDE.md "홀드아웃" 절 참고
REBAL_MONTHS = (3, 6, 9, 12)
UNIVERSES = [("코스닥150", "KOSDAQ150"), ("코스피200", "KOSPI200"),
             ("코스피전체", "KOSPI"), ("코스닥전체", "KOSDAQ")]
REGIMES = [("정상1", "2018-12-28", "2020-03-13"), ("금지1", "2020-03-16", "2021-05-02"),
           ("부분허용", "2021-05-03", "2023-11-05"), ("금지2", "2023-11-06", "2025-03-30"),
           ("정상2", "2025-03-31", "2099-12-31")]
BAN = {"금지1", "금지2"}
OUT_DIR = REPO_DIR / "reference"

db = SessionLocal()


def pivot(sql, params):
    df = pd.read_sql(text(sql), db.bind, params=params)
    p = df.pivot(index="date", columns="instrument_id", values="value").sort_index()
    p.index = pd.to_datetime(p.index)
    return p


print("데이터 로딩 중 ...")
adj = pivot("""select d.date, d.instrument_id, d.adj_close::float value
 from dividend_adjusted_prices d join instruments i on i.id = d.instrument_id
 where d.period='D' and d.date >= :s and coalesce(i.asset_type,'') <> 'index'""", {"s": START})
amt = pivot("""select s.date, s.instrument_id, s.total_value::float value
 from short_selling s where s.date >= :s and s.total_value is not null""",
            {"s": START}).reindex(index=adj.index, columns=adj.columns)
raw = pivot("""select p.date, p.instrument_id, p.raw_close::float value from prices p
 where p.period='D' and p.date >= :s and p.raw_close is not null""",
            {"s": START}).reindex(index=adj.index, columns=adj.columns)
shr = pivot("""select m.date, m.instrument_id, m.value::float value
 from monthly_fundamentals m
 where m.date >= :s and m.metric='shares_outstanding_monthly' and m.value is not null""",
            {"s": START}).reindex(index=adj.index, columns=adj.columns).ffill()
ind = pivot("""select it.date, it.instrument_id, it.net_value::float value
 from investor_trading it where it.date >= :s and it.investor_type='개인'""",
            {"s": START}).reindex(index=adj.index, columns=adj.columns)

memb = pd.read_sql(text("select index_name, as_of_date, instrument_id from index_memberships"), db.bind)
memb["as_of_date"] = pd.to_datetime(memb["as_of_date"])

rets = adj.pct_change(fill_method=None)
ivol = rets.rolling(IVOL_WINDOW, min_periods=IVOL_MIN_OBS).std() * np.sqrt(252)
liq = amt.rolling(LIQ_WINDOW, min_periods=LIQ_WINDOW // 2).mean()
mcap = raw * shr
pers = ind.rolling(60, min_periods=45).sum() / amt.rolling(60, min_periods=45).sum()
fwd_src = adj.ffill()

days = list(adj.index)
month_last = {}
for i, d in enumerate(days):
    month_last[(d.year, d.month)] = i
# 홀드아웃은 **수익률을 재는 구간**에 걸린다. 형성일이 2019-12-30이어도 forward 구간이
# 2020년 안에 있으면 소비되는 것은 2020년뿐이다. 형성일까지 2020년 이후로 밀면 첫 관측이
# 2020-03-31(코로나 저점 직후)이 되어 표본이 유리한 쪽으로 잘린다.
_all = sorted(i for (y, m), i in month_last.items()
              if m in REBAL_MONTHS and i >= IVOL_WINDOW + 5 and i < len(days) - 1)
_seed = [i for i in _all if days[i] < pd.Timestamp(HOLDOUT_START)]
form = ([_seed[-1]] if _seed else []) + [i for i in _all if days[i] >= pd.Timestamp(HOLDOUT_START)]


def regime_of(d):
    for name, a, b in REGIMES:
        if pd.Timestamp(a) <= d <= pd.Timestamp(b):
            return name
    return "정상1"


def spearman(a, b):
    """스피어만 상관 — scipy 없이 랭크 후 피어슨."""
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    ra, rb = ra - ra.mean(), rb - rb.mean()
    d = np.sqrt((ra @ ra) * (rb @ rb))
    return float(ra @ rb / d) if d else np.nan


def nw_t(x, lag=1):
    """Newey-West 보정 t (평균이 0인지). 비중복 분기 관측이라 lag=1이면 충분."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 3:
        return np.nan
    m = x.mean()
    e = x - m
    g0 = (e @ e) / n
    var = g0
    for L in range(1, lag + 1):
        gl = (e[L:] @ e[:-L]) / n
        var += 2 * (1 - L / (lag + 1)) * gl
    return m / np.sqrt(max(var, 1e-18) / n)


records = []
for label, index_name in UNIVERSES:
    sub = memb[memb.index_name == index_name]
    snaps = sorted(sub["as_of_date"].unique())
    members = {s: set(sub.loc[sub.as_of_date == s, "instrument_id"]) for s in snaps}

    for k, ri in enumerate(form[:-1]):
        d, nd = days[ri], days[form[k + 1]]
        el = [s for s in snaps if s <= d]
        if not el:
            continue
        cols = [c for c in adj.columns if c in members[max(el)]]

        lq = liq.loc[d, cols].dropna()
        if len(lq) < N_Q * 8:
            continue
        kept = lq[lq >= lq.quantile(LIQ_DROP)].index
        iv = ivol.loc[d, kept].dropna()
        iv = iv[iv > 0]
        if len(iv) < N_Q * 6:
            continue

        f = (fwd_src.loc[nd, iv.index] / fwd_src.loc[d, iv.index] - 1).dropna()
        iv = iv[f.index]
        if len(iv) < N_Q * 6:
            continue

        q = pd.qcut(iv.rank(method="first"), N_Q, labels=False)
        mc = mcap.loc[d, iv.index]
        pf = pers.loc[d, iv.index]

        rec = dict(유니버스=label, 형성일=d, 제도=regime_of(d), n=len(iv))
        for qq in range(N_Q):
            rec[f"Q{qq+1}"] = f[q == qq].mean()
        rec["spread"] = rec["Q1"] - rec[f"Q{N_Q}"]
        # P3: 개인 순매수 vs 변동성 (양수면 개인이 고변동 종목을 산다)
        ok = pf.notna()
        rec["p3_rho"] = spearman(iv[ok], pf[ok]) if ok.sum() > 30 else np.nan
        # P4: 시총 3분위별 스프레드
        okm = mc.notna()
        if okm.sum() > N_Q * 6:
            t3 = pd.qcut(mc[okm].rank(method="first"), 3, labels=["소형", "중형", "대형"])
            for g in ["소형", "중형", "대형"]:
                sel = t3[t3 == g].index
                ivg, fg = iv[sel], f[sel]
                if len(ivg) >= 10:
                    lo = fg[ivg <= ivg.quantile(0.3)].mean()
                    hi = fg[ivg >= ivg.quantile(0.7)].mean()
                    rec[f"p4_{g}"] = lo - hi
        records.append(rec)

res = pd.DataFrame(records)
pd.set_option("display.width", 220)

print("\n" + "=" * 96)
print(f"IVOL 5분위 분기 스프레드 (Q1 최저변동 − Q5 최고변동), 형성일 비중복 "
      f"{res.형성일.min().date()} ~ {res.형성일.max().date()}")
print("=" * 96)

print("\n[P2] 유니버스별 — 예측: 코스닥 > 코스피")
print(f"{'유니버스':10s} {'관측':>4s} {'Q1':>7s} {'Q2':>7s} {'Q3':>7s} {'Q4':>7s} {'Q5':>7s} "
      f"{'스프레드':>8s} {'NW t':>6s} {'승률':>6s}")
for label, _ in UNIVERSES:
    r = res[res.유니버스 == label]
    print(f"{label:10s} {len(r):>4d} " + " ".join(f"{r[f'Q{i}'].mean():>+6.2%}" for i in range(1, 6)) +
          f" {r.spread.mean():>+7.2%} {nw_t(r.spread):>6.2f} {(r.spread > 0).mean():>5.0%}")

print("\n[P1] 공매도 제도 구간별 — 예측: 금지구간에 스프레드 확대")
print(f"{'제도':10s} {'관측':>4s} {'스프레드':>8s} {'NW t':>6s} {'승률':>6s}   (4개 유니버스 통합)")
for name, _, _ in REGIMES:
    r = res[res.제도 == name]
    if len(r):
        mark = " ←금지" if name in BAN else ""
        print(f"{name:10s} {len(r):>4d} {r.spread.mean():>+7.2%} {nw_t(r.spread):>6.2f} "
              f"{(r.spread > 0).mean():>5.0%}{mark}")
ban = res[res.제도.isin(BAN)].spread
nor = res[~res.제도.isin(BAN)].spread
print(f"  금지 {ban.mean():+.2%} vs 정상·부분허용 {nor.mean():+.2%} "
      f"→ 차이 {ban.mean()-nor.mean():+.2%}p, 예측 방향 {'일치' if ban.mean() > nor.mean() else '불일치'}")

print("\n[P3] 개인 순매수(60일/거래대금) vs IVOL 횡단면 상관 — 예측: 양(+)")
print(f"{'유니버스':10s} {'평균 rho':>9s} {'NW t':>6s} {'양수비율':>8s}")
for label, _ in UNIVERSES:
    r = res[res.유니버스 == label].p3_rho.dropna()
    if len(r):
        print(f"{label:10s} {r.mean():>+8.3f} {nw_t(r):>6.2f} {(r > 0).mean():>7.0%}")

print("\n[P4] 시총 3분위별 스프레드(하위30% − 상위30% IVOL) — 예측: 소형 > 대형")
print(f"{'유니버스':10s} {'소형':>8s} {'중형':>8s} {'대형':>8s}   (t: 소형/대형)")
for label, _ in UNIVERSES:
    r = res[res.유니버스 == label]
    cells = [r[f"p4_{g}"].dropna() for g in ["소형", "중형", "대형"]]
    if all(len(c) for c in cells):
        print(f"{label:10s} " + " ".join(f"{c.mean():>+7.2%}" for c in cells) +
              f"   ({nw_t(cells[0]):>5.2f} / {nw_t(cells[2]):>5.2f})")

print("\n연도별 스프레드 (%, 4개 유니버스 평균)")
yr = res.assign(연=res.형성일.dt.year).pivot_table(index="연", columns="유니버스",
                                                 values="spread", aggfunc="mean")
print((yr * 100).round(2).to_string())

res.to_csv(OUT_DIR / "알고리즘3_부수예측.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/알고리즘3_부수예측.csv")
db.close()
