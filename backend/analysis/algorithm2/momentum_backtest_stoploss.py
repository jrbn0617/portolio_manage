"""기관필터+모멘텀6개월·20종목에 손절(로스컷) 10/15/20% 적용.

손절 규칙은 기존 코드(backtest_service.apply_stop_loss)의 관행을 그대로 따른다:
  - 판정 기준: 고점이 아니라 **진입가(리밸런싱일 종가) 대비** -X% 최초 도달
  - 발동 후 : 현금(수익 0%)으로 남은 보유기간 동결, 잔여 종목으로 재배분하지 않음
  - 체결    : 다음 거래일 시가 (오버나잇 갭 반영). 갭 데이터 없거나 구간 마지막날이면 당일 종가

전략 고정: 기관 순매수(거래대금대비 60일) 하위40% -> 6개월 모멘텀 상위 20종목,
동일가중, 월간(20거래일) 리밸런싱, 왕복 0.30%.
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
SKIP, MOM_N = 21, 126
HOLD, TOP_N = 20, 20
FLOW_WINDOW, FLOW_KEEP = 60, 0.40
COST = 0.0030
STOPS = [None, 0.10, 0.15, 0.20]
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
p_open = pivot(UNIV + """select p.date,p.instrument_id,p.open::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s and p.open is not null""",
               {"s": START}).reindex_like(adj)
p_close = pivot(UNIV + """select p.date,p.instrument_id,p.close::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s""", {"s": START}).reindex_like(adj)
amt = pivot(UNIV + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)
inst = pivot(UNIV + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type='기관합계'""", {"s": START}).reindex_like(adj)

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

fsig = inst.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / \
    amt.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
mom = adj.shift(SKIP) / adj.shift(SKIP + MOM_N) - 1

days = list(adj.index)
rebal = list(range(MOM_N + SKIP + FLOW_WINDOW, len(days) - 1, HOLD))
print(f"리밸런싱 {len(rebal)}회 ({days[rebal[0]].date()} ~ {days[rebal[-1]].date()})\n")

A = adj.values
OP, CL = p_open.values, p_close.values
col_ix = {c: i for i, c in enumerate(adj.columns)}


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


def select(d, cols):
    fl = fsig.loc[d, cols]
    fl = fl[fl.notna()]
    if len(fl) < TOP_N * 2:
        return []
    cand = fl.nsmallest(max(TOP_N, int(len(fl) * FLOW_KEEP))).index
    m = mom.loc[d, cand]
    m = m[m.notna()]
    return list(m.nlargest(TOP_N).index) if len(m) >= TOP_N else []


def run(stop_pct):
    navs, dates, prev_w, tn = [1.0], [days[rebal[0]]], {}, []
    stop_hits, pos_total = 0, 0
    for ri in rebal:
        d = days[ri]
        cols = [c for c in adj.columns if c in universe_at(d)]
        if len(cols) < 100:
            continue
        picks = select(d, cols)
        if not picks:
            continue
        w0 = {p: 1 / len(picks) for p in picks}
        turn = sum(abs(w0.get(k, 0) - prev_w.get(k, 0)) for k in set(w0) | set(prev_w)) / 2
        tn.append(turn)
        navs[-1] *= (1 - turn * COST)

        idx = [col_ix[p] for p in picks]
        entry = A[ri, idx]
        end = min(ri + HOLD, len(days) - 1)
        frozen = np.full(len(idx), np.nan)
        freeze_from = np.full(len(idx), 10 ** 9)
        base = navs[-1]
        pos_total += len(idx)

        last_v = np.ones(len(idx))
        for j in range(ri + 1, end + 1):
            # 보유 중 상장폐지·거래정지로 가격이 끊기면 **마지막 관측치를 유지**한다.
            # 예전엔 np.where(isnan, 1.0, ...)로 채워서 40% 빠진 뒤 정지된 종목이 진입가로
            # 되돌아갔다 — 상방 편향이다.
            raw = A[j, idx] / entry
            v = np.where(np.isnan(raw), last_v, raw)
            last_v = v
            if stop_pct is not None:
                hit = np.isnan(frozen) & (v <= 1 - stop_pct)
                for k in np.where(hit)[0]:
                    gap = 1.0
                    if j < len(days) - 1:
                        o, c = OP[j + 1, idx[k]], CL[j, idx[k]]
                        if not np.isnan(o) and not np.isnan(c) and c:
                            gap = o / c
                    frozen[k] = v[k] * gap
                    freeze_from[k] = j + 1
                    stop_hits += 1
            cur = v.copy()
            fk = np.where(~np.isnan(frozen))[0]
            for k in fk:
                if j >= freeze_from[k]:
                    cur[k] = frozen[k]
            navs.append(base * float(cur.mean()))
            dates.append(days[j])
        prev_w = w0
    return pd.Series(navs, index=dates), np.mean(tn), stop_hits / max(pos_total, 1)


def met(nav):
    r = nav.pct_change().dropna()
    y = (nav.index[-1] - nav.index[0]).days / 365.25
    c = nav.iloc[-1] ** (1 / y) - 1
    v = r.std() * np.sqrt(252)
    return c, v, (nav / nav.cummax() - 1).min(), (c / v if v else np.nan)


curves, rows = {}, []
for s in STOPS:
    label = "손절 없음" if s is None else f"손절 {int(s*100)}%"
    nav, turn, hitrate = run(s)
    curves[label] = nav
    c, v, mdd, sh = met(nav)
    rows.append(dict(전략=label, CAGR=c, 변동성=v, MDD=mdd, Sharpe=sh,
                     발동비율=hitrate, 회전율=turn, 최종배수=nav.iloc[-1]))

res = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print("=" * 88)
print(f"기관필터+모멘텀6개월·20종목 — 손절 비교 "
      f"({curves['손절 없음'].index[0].date()} ~ {curves['손절 없음'].index[-1].date()}, {len(rebal)}회)")
print(res.to_string(index=False, formatters={
    "CAGR": "{:+.2%}".format, "변동성": "{:.1%}".format, "MDD": "{:.1%}".format,
    "Sharpe": "{:.3f}".format, "발동비율": "{:.1%}".format, "회전율": "{:.0%}".format,
    "최종배수": "{:.2f}x".format}))

navdf = pd.DataFrame(curves)
yr = pd.concat([navdf.iloc[[0]], navdf.resample("YE").last()])
rets = yr.pct_change().dropna()
rets.index = rets.index.year
print("\n연도별 수익률 (%)")
print((rets * 100).round(1).to_string())

navdf.to_csv(OUT_DIR / "손절비교_NAV.csv", encoding="utf-8-sig")
res.to_csv(OUT_DIR / "손절비교_요약.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/손절비교_요약.csv, _NAV.csv")
db.close()
