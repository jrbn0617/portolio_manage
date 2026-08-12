"""알고리즘 #3 확정안 백테스트 — 저변동(IVOL) + 역변동성 가중, 분기 리밸런싱.

선등록 문서 `docs/algorithms/algorithm3-prereg.md` 4절의 확정 파라미터를 그대로 구현한다.
**이 스크립트의 결과를 보고 파라미터를 바꾸면 선등록이 깨진다.**

파이프라인:
  유니버스(반기 스냅샷) -> 유동성 하한(60일 평균 거래대금 하위 20% 제외)
  -> IVOL(60거래일 실현변동성) 하위 20% 풀 -> 낮은 순으로 30종목, krx_sector당 2종목
  -> 역변동성 가중(상한 10%) -> 분기말 리밸런싱, 오버레이 없음, 왕복 0.30%

**선등록을 구현으로 옮기며 명시해두는 두 가지** (파라미터 변경이 아니라 미명세 부분의 확정):

1. **풀 크기 하한**: "IVOL 하위 20%"가 top_n보다 작아지는 유니버스가 있다(코스닥150은
   150종목 -> 유동성 제외 후 120 -> 20%는 24종목). 풀이 top_n 미만이면 선택 자체가
   불가능하므로 풀은 `max(top_n, 20%)`로 둔다. 코스피전체/코스닥전체에서는 20%가
   훨씬 크므로 이 하한이 걸리지 않는다.
2. **편입종목이 30개에 못 미칠 수 있다**: 풀에서 krx_sector당 2종목 제약으로 뽑다 보면
   풀이 소진된다. 부족해도 그대로 진행한다(알고리즘 #1도 top_n 20에 실제 편입 13~18이다).
   실제 편입종목수를 결과에 함께 보고한다.

가격 데이터가 2018-12-28부터라 첫 리밸런싱은 2019-03월말이다. 2015-07까지의 백필이
도착하면 선등록 5-1절의 1차 홀드아웃 구간을 같은 스크립트로 돌린다.
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

# --- 선등록 4절 확정 파라미터 (변경 금지) ---
IVOL_WINDOW = 60          # 실현변동성 거래일
IVOL_MIN_OBS = 45         # 유효 관측 하한
IVOL_KEEP = 0.20          # IVOL 하위 20%
TOP_N = 30
MAX_PER_SECTOR = 2
SECTOR_FIELD = "krx_sector"
MAX_WEIGHT = 0.10
LIQ_WINDOW = 60           # 거래대금 평균 거래일
LIQ_DROP = 0.20           # 하위 20% 제외
COST = 0.0030             # 왕복
REBAL_MONTHS = (3, 6, 9, 12)

START = "2018-12-01"          # 신호 계산용 로딩 시작 (백테스트 시작이 아님)
HOLDOUT_START = "2020-01-01"  # 이 날짜 이전 **성과**는 홀드아웃 — CLAUDE.md "홀드아웃" 절 참고
UNIVERSES = [("코스닥150", "KOSDAQ150"), ("코스피200", "KOSPI200"),
             ("코스피전체", "KOSPI"), ("코스닥전체", "KOSDAQ")]
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
sec = pd.read_sql(text(f"select id, {SECTOR_FIELD} as grp from instruments"), db.bind).set_index("id")

memb = pd.read_sql(text("select index_name, as_of_date, instrument_id from index_memberships"), db.bind)
memb["as_of_date"] = pd.to_datetime(memb["as_of_date"])

bm = pivot("""select p.date, p.instrument_id, p.close::float value
 from prices p join instruments i on i.id = p.instrument_id
 where p.period='D' and p.date >= :s and i.asset_type='index'""", {"s": START})
bm_id = {r.ticker: r.id for r in db.execute(text(
    "select ticker, id from instruments where asset_type='index'")).fetchall()}

# --- 신호 ---
rets = adj.pct_change()
ivol = rets.rolling(IVOL_WINDOW, min_periods=IVOL_MIN_OBS).std() * np.sqrt(252)
liq = amt.rolling(LIQ_WINDOW, min_periods=LIQ_WINDOW // 2).mean()

days = list(adj.index)
day_ix = {d: i for i, d in enumerate(days)}
A = adj.values
col_ix = {c: i for i, c in enumerate(adj.columns)}

# 분기말(3/6/9/12월 마지막 거래일) 중 IVOL 계산이 가능한 시점부터
month_last = {}
for i, d in enumerate(days):
    month_last[(d.year, d.month)] = i
# 홀드아웃 경계는 **성과 측정 구간**에 적용한다(NAV를 HOLDOUT_START 이후로 자른다).
# 포트폴리오 형성은 그 직전 분기말에서 시작해야 한다 — 형성일까지 2020년 이후로 밀면
# 첫 리밸런싱이 2020-03-31(코로나 저점 8일 뒤)이 되어 폭락 구간이 표본에서 빠지고,
# 그것만으로 MDD가 -39.5% -> -17.1%로 바뀌어 판정이 통째로 뒤집힌다.
# 신호 계산에 2020년 이전 가격을 쓰는 것은 홀드아웃 소비가 아니다(모든 백테스트가
# 첫 형성일에 lookback을 필요로 한다). 소비되는 것은 "성과를 잰 구간"뿐이다.
_all = sorted(i for (y, m), i in month_last.items()
              if m in REBAL_MONTHS and i >= IVOL_WINDOW + 5 and i < len(days) - 1)
_seed = [i for i in _all if days[i] < pd.Timestamp(HOLDOUT_START)]
rebal = ([_seed[-1]] if _seed else []) + [i for i in _all if days[i] >= pd.Timestamp(HOLDOUT_START)]
print(f"리밸런싱 {len(rebal)}회 ({days[rebal[0]].date()} ~ {days[rebal[-1]].date()})\n")


def group_of(i):
    g = sec.at[i, "grp"] if i in sec.index else None
    return "미분류" if g is None or (isinstance(g, float) and np.isnan(g)) else g


def cap_weights(w: dict, cap: float) -> dict:
    """cap을 넘는 종목을 cap으로 고정하고 잔여를 나머지에 비례 재분배 (반복)."""
    w = dict(w)
    for _ in range(100):
        over = {k: v for k, v in w.items() if v > cap + 1e-12}
        if not over:
            return w
        free = {k: v for k, v in w.items() if k not in over}
        budget = 1.0 - cap * len(over)
        tot = sum(free.values())
        if tot <= 0 or budget <= 0:
            return {k: 1.0 / len(w) for k in w}
        w = {**{k: cap for k in over}, **{k: budget * v / tot for k, v in free.items()}}
    return w


def snapshots_for(index_name):
    sub = memb[memb.index_name == index_name]
    snaps = sorted(sub["as_of_date"].unique())
    return snaps, {s: set(sub.loc[sub.as_of_date == s, "instrument_id"]) for s in snaps}


def run(index_name):
    snaps, members = snapshots_for(index_name)
    navs, dates, prev = [1.0], [days[rebal[0]]], {}
    turns, n_picks, hhis = [], [], []
    holdings = []

    for k, ri in enumerate(rebal):
        d = days[ri]
        el = [s for s in snaps if s <= d]
        if not el:
            continue
        cols = [c for c in adj.columns if c in members[max(el)]]
        if len(cols) < 50:
            continue

        # 1) 유동성 하한 — 60일 평균 거래대금 하위 20% 제외
        lq = liq.loc[d, cols].dropna()
        if len(lq) < TOP_N * 2:
            continue
        kept = lq[lq >= lq.quantile(LIQ_DROP)].index

        # 2) IVOL 하위 20% 풀 (풀 하한 = TOP_N)
        iv = ivol.loc[d, kept].dropna()
        iv = iv[iv > 0]
        if len(iv) < TOP_N:
            continue
        pool = iv.nsmallest(max(TOP_N, int(len(iv) * IVOL_KEEP))).sort_values()

        # 3) 낮은 순으로 30종목, 섹터당 2종목
        picks, cnt = [], {}
        for iid in pool.index:
            if len(picks) >= TOP_N:
                break
            g = group_of(iid)
            if cnt.get(g, 0) >= MAX_PER_SECTOR:
                continue
            picks.append(iid)
            cnt[g] = cnt.get(g, 0) + 1
        if len(picks) < 5:
            continue

        # 4) 역변동성 가중 + 상한 10%
        inv = {p: 1.0 / float(pool[p]) for p in picks}
        tot = sum(inv.values())
        w = cap_weights({p: v / tot for p, v in inv.items()}, MAX_WEIGHT)

        turn = sum(abs(w.get(x, 0) - prev.get(x, 0)) for x in set(w) | set(prev)) / 2
        turns.append(turn)
        n_picks.append(len(picks))
        hhis.append(1.0 / sum(v * v for v in w.values()))
        navs[-1] *= (1 - turn * COST)
        holdings.append(dict(date=d.date(), n=len(picks), turnover=turn,
                             ivol_mean=float(pool[picks].mean()),
                             sectors=len(cnt)))

        idx = [col_ix[p] for p in picks]
        wv = np.array([w[p] for p in picks])
        entry, base = A[ri, idx], navs[-1]
        end = rebal[k + 1] if k + 1 < len(rebal) else len(days) - 1

        # 보유구간 가치비율. 거래정지·상장폐지로 가격이 끊기면 **마지막 관측치를 유지**한다.
        # np.where(isnan, 1.0, ...)로 채우면 40% 빠진 뒤 정지된 종목이 진입가로 되돌아가
        # 상방으로 편향된다(알고리즘 #2 스크립트의 패턴이라 그대로 옮겼다가 여기서 고쳤다).
        ratio = pd.DataFrame(A[ri + 1:end + 1, idx] / entry).ffill().fillna(1.0).values
        for t, j in enumerate(range(ri + 1, end + 1)):
            navs.append(base * float((wv * ratio[t]).sum()))
            dates.append(days[j])
        prev = w

    nav = pd.Series(navs, index=dates)
    nav = nav[nav.index >= pd.Timestamp(HOLDOUT_START)]   # 성과 측정은 홀드아웃 이후만
    nav = nav / nav.iloc[0]
    return nav, np.mean(turns), np.median(n_picks), np.mean(hhis), pd.DataFrame(holdings)


def met(s):
    r = s.pct_change().dropna()
    y = (s.index[-1] - s.index[0]).days / 365.25
    c = (s.iloc[-1] / s.iloc[0]) ** (1 / y) - 1
    v = r.std() * np.sqrt(252)
    return c, v, (s / s.cummax() - 1).min(), (c / v if v else np.nan)


rows, curves, holds = [], {}, {}
for label, index_name in UNIVERSES:
    nav, turn, npick, effn, hold = run(index_name)
    curves[label] = nav
    holds[label] = hold
    c, v, mdd, sh = met(nav)

    b = bm[bm_id[index_name]].reindex(nav.index).ffill()
    b = b / b.iloc[0]
    bc, bv, bmdd, bsh = met(b)
    curves[f"{label}_BM"] = b

    rows.append(dict(유니버스=label, CAGR=c, 변동성=v, MDD=mdd, Sharpe=sh,
                     BM_CAGR=bc, BM_변동성=bv, BM_MDD=bmdd, BM_Sharpe=bsh,
                     회전율=turn, 편입중앙=npick, 실효N=effn))

res = pd.DataFrame(rows)
pd.set_option("display.width", 220)

first = curves[UNIVERSES[0][0]]
print("=" * 104)
print(f"알고리즘 #3 확정안 — 저변동 + 역변동성 가중, 분기 리밸런싱 "
      f"({first.index[0].date()} ~ {first.index[-1].date()})")
print("=" * 104)
print(f"{'유니버스':10s} {'CAGR':>8s} {'변동성':>8s} {'MDD':>8s} {'Sharpe':>7s} │ "
      f"{'BM CAGR':>8s} {'BM변동':>7s} {'BM MDD':>8s} {'BM Sh':>6s} │ {'회전':>5s} {'편입':>4s} {'실효N':>5s}")
for _, r in res.iterrows():
    print(f"{r.유니버스:10s} {r.CAGR:>+7.2%} {r.변동성:>8.1%} {r.MDD:>8.1%} {r.Sharpe:>7.3f} │ "
          f"{r.BM_CAGR:>+7.2%} {r.BM_변동성:>7.1%} {r.BM_MDD:>8.1%} {r.BM_Sharpe:>6.2f} │ "
          f"{r.회전율*100:>4.0f}% {r.편입중앙:>4.0f} {r.실효N:>5.1f}")

print("\n선등록 5-3절 채택 조건")
c1 = (res.Sharpe > res.BM_Sharpe)
c2 = (res.MDD > res.BM_MDD)   # MDD는 음수 — 큰 값이 더 얕은 낙폭
print(f"  C1 4개 전부 샤프 > BM : {'통과' if c1.all() else '실패'} "
      f"({', '.join(f'{r.유니버스} {r.Sharpe:.2f} vs {r.BM_Sharpe:.2f}' for _, r in res.iterrows())})")
print(f"  C2 4개 전부 MDD < BM  : {'통과' if c2.all() else '실패'} "
      f"({', '.join(f'{r.유니버스} {r.MDD:.1%} vs {r.BM_MDD:.1%}' for _, r in res.iterrows())})")

navdf = pd.DataFrame(curves)
yr = pd.concat([navdf.iloc[[0]], navdf.resample("YE").last()])
rr = yr.pct_change().dropna()
rr.index = rr.index.year
print("\n연도별 수익률 (%)")
print((rr * 100).round(1).to_string())

print("\n편입 현황 (리밸런싱별 중앙값)")
for label, _ in UNIVERSES:
    h = holds[label]
    print(f"  {label:10s} 종목 {h.n.median():.0f} (min {h.n.min()}) · 섹터 {h.sectors.median():.0f} · "
          f"편입종목 평균 IVOL {h.ivol_mean.median():.1%} · 회전 {h.turnover.median():.0%}")

res.to_csv(OUT_DIR / "알고리즘3_저변동_요약.csv", index=False, encoding="utf-8-sig")
navdf.to_csv(OUT_DIR / "알고리즘3_저변동_NAV.csv", encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/알고리즘3_저변동_요약.csv, _NAV.csv")
db.close()
