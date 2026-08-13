"""섹터캡 x 손절 2x3 비교 — 손절이 각 섹터캡 설정에서 얼마나 기여하는지 분리.

섹터캡: 제약없음 / 케이산업 2종목 / 팩셋섹터 5종목
손절  : 없음 / -10%(익일시가, 현금동결)

고정: 코스피200+코스닥150, 기관 순매수 하위40% -> 6개월 모멘텀 상위 20종목,
      동일가중, 월간(20거래일) 리밸런싱.

거래비용은 증권거래세(매도 0.20%) + 위탁수수료(양방향 각 0.015%)로 계산한다
(app.services.backtest_service.TransactionCost). 손절 동결도 매도로 보고 비용을 문다.
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
from app.services.backtest_service import TransactionCost  # noqa: E402
from sqlalchemy import text  # noqa: E402

START = "2019-01-01"
HOLDOUT_START = "2020-01-01"  # 이 날짜 이전 성과는 봉인 — CLAUDE.md "홀드아웃" 절
SKIP, MOM_N = 21, 126
HOLD, TOP_N = 20, 20
FLOW_WINDOW, FLOW_KEEP = 60, 0.40
COST = TransactionCost(sell_tax=0.0020, commission=0.00015)
OUT_DIR = REPO_DIR / "reference"

CAPS = [("제약없음", None, None), ("케이산업2종목", "krx_sector", 2), ("팩셋섹터5종목", "sector", 5)]
STOPS = [("손절없음", None), ("손절10%", 0.10)]

db = SessionLocal()
ALL = """
with u as (select distinct instrument_id iid from index_memberships
           where index_name in ('KOSDAQ150','KOSPI200'))
"""


def pivot(sql, params):
    df = pd.read_sql(text(sql), db.bind, params=params)
    p = df.pivot(index="date", columns="instrument_id", values="value").sort_index()
    p.index = pd.to_datetime(p.index)
    return p


print("데이터 로딩 중...")
adj = pivot(ALL + """select d.date,d.instrument_id,d.adj_close::float value
 from dividend_adjusted_prices d join u on u.iid=d.instrument_id
 where d.period='D' and d.date>=:s""", {"s": START})
p_open = pivot(ALL + """select p.date,p.instrument_id,p.open::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s and p.open is not null""",
               {"s": START}).reindex_like(adj)
p_close = pivot(ALL + """select p.date,p.instrument_id,p.close::float value from prices p
 join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s""", {"s": START}).reindex_like(adj)
amt = pivot(ALL + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)
inst = pivot(ALL + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type='기관합계'""", {"s": START}).reindex_like(adj)
sec = pd.read_sql(text(ALL + """select i.id, i.sector, i.krx_sector
 from instruments i join u on u.iid=i.id"""), db.bind).set_index("id")

memb = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
memb["as_of_date"] = pd.to_datetime(memb["as_of_date"])
snaps = sorted(memb["as_of_date"].unique())
snap_members = {s: set(memb.loc[memb.as_of_date == s, "instrument_id"]) for s in snaps}

fsig = inst.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / \
    amt.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum()
mom = adj.shift(SKIP) / adj.shift(SKIP + MOM_N) - 1
days = list(adj.index)
rebal = list(range(MOM_N + SKIP + FLOW_WINDOW, len(days) - 1, HOLD))
A, OP, CL = adj.values, p_open.values, p_close.values
col_ix = {c: i for i, c in enumerate(adj.columns)}
print(f"리밸런싱 {len(rebal)}회 ({days[rebal[0]].date()} ~ {days[rebal[-1]].date()})\n")


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


def sector_of(i, f):
    g = sec.at[i, f] if i in sec.index else None
    return "미분류" if g is None or (isinstance(g, float) and np.isnan(g)) else g


def run(field, cap, stop):
    navs, dates, prev, tn, hits, pos = [1.0], [days[rebal[0]]], {}, [], 0, 0
    for ri in rebal:
        d = days[ri]
        cols = [c for c in adj.columns if c in universe_at(d)]
        if len(cols) < 100:
            continue
        fl = fsig.loc[d, cols].dropna()
        if len(fl) < TOP_N * 2:
            continue
        cand = fl.nsmallest(max(TOP_N, int(len(fl) * FLOW_KEEP))).index
        m = mom.loc[d, cand].dropna().sort_values(ascending=False)
        if len(m) < TOP_N:
            continue
        if cap is None:
            picks = list(m.index[:TOP_N])
        else:
            picks, cnt = [], {}
            for i in m.index:
                if len(picks) >= TOP_N:
                    break
                g = sector_of(i, field)
                if cnt.get(g, 0) >= cap:
                    continue
                picks.append(i)
                cnt[g] = cnt.get(g, 0) + 1
            if len(picks) < TOP_N:
                continue

        # prev = 직전 구간 종료 시점의 **실제** 비중 (드리프트·손절청산 반영)
        w = {p: 1 / len(picks) for p in picks}
        keys = set(w) | set(prev)
        sell_amt = sum(max(0.0, prev.get(k, 0) - w.get(k, 0)) for k in keys)
        buy_amt = sum(max(0.0, w.get(k, 0) - prev.get(k, 0)) for k in keys)
        tn.append((sell_amt + buy_amt) / 2)
        navs[-1] *= 1 - (sell_amt * COST.sell_rate + buy_amt * COST.buy_rate)

        idx = [col_ix[p] for p in picks]
        wv = np.full(len(idx), 1 / len(idx))
        entry, base = A[ri, idx], navs[-1]
        end = min(ri + HOLD, len(days) - 1)
        frozen, ffrom = np.full(len(idx), np.nan), np.full(len(idx), 10 ** 9)
        pos += len(idx)
        last_v = np.ones(len(idx))
        for j in range(ri + 1, end + 1):
            # 보유 중 상장폐지·거래정지로 가격이 끊기면 **마지막 관측치를 유지**한다.
            # 예전엔 np.where(isnan, 1.0, ...)로 채워서 40% 빠진 뒤 정지된 종목이 진입가로
            # 되돌아갔다 — 상방 편향이다.
            raw = A[j, idx] / entry
            v = np.where(np.isnan(raw), last_v, raw)
            last_v = v
            if stop is not None:
                for k in np.where(np.isnan(frozen) & (v <= 1 - stop))[0]:
                    gap = 1.0
                    if j < len(days) - 1:
                        o, c = OP[j + 1, idx[k]], CL[j, idx[k]]
                        if not np.isnan(o) and not np.isnan(c) and c:
                            gap = o / c
                    # 동결 = 매도이므로 청산가에 매도비용을 물린다
                    frozen[k], ffrom[k] = v[k] * gap * (1 - COST.sell_rate), j + 1
                    hits += 1
            cur = v.copy()
            for k in np.where(~np.isnan(frozen))[0]:
                if j >= ffrom[k]:
                    cur[k] = frozen[k]
            mult = float((wv * cur).sum())
            navs.append(base * mult)
            dates.append(days[j])
        held = ~(~np.isnan(frozen) & (end >= ffrom))
        prev = {picks[k]: wv[k] * cur[k] / mult for k in range(len(idx)) if held[k]}
    return pd.Series(navs, index=dates), np.mean(tn), hits / max(pos, 1)


def met(s):
    r = s.pct_change().dropna()
    y = (s.index[-1] - s.index[0]).days / 365.25
    c = s.iloc[-1] ** (1 / y) - 1
    v = r.std() * np.sqrt(252)
    return c, v, (s / s.cummax() - 1).min(), (c / v if v else np.nan)


def clip_holdout(s):
    """홀드아웃 경계 — 성과는 2020-01-01부터만 잰다 (CLAUDE.md "홀드아웃" 절).

    형성일은 그 직전 리밸런싱부터 시작하되(위 run이 2019년부터 돌린다), **성과 구간**을
    잘라 1.0으로 리베이스한다. 경계를 리밸런싱 날짜에 걸면 첫 형성일이 2020-03-31로
    밀려 코로나 폭락이 표본에서 통째로 빠진다 — 실측으로 판정이 뒤집힌 적이 있다.
    """
    cut = s.loc[s.index >= HOLDOUT_START]
    return cut / cut.iloc[0]


curves, rows = {}, []
for cname, fld, cap in CAPS:
    for sname, stop in STOPS:
        label = f"{cname} · {sname}"
        nav, t, hr = run(fld, cap, stop)
        nav = clip_holdout(nav)
        curves[label] = nav
        c, v, mdd, sh = met(nav)
        rows.append(dict(섹터캡=cname, 손절=sname, CAGR=c, 변동성=v, MDD=mdd,
                         Sharpe=sh, 회전율=t, 발동비율=hr, 최종=nav.iloc[-1]))

res = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print("=" * 92)
print(f"섹터캡 x 손절 ({curves[list(curves)[0]].index[0].date()} ~ "
      f"{curves[list(curves)[0]].index[-1].date()}, 동일가중)")
print(f"{'섹터캡':14s} {'손절':8s} {'CAGR':>9s} {'변동성':>8s} {'MDD':>8s} {'Sharpe':>7s} "
      f"{'회전':>5s} {'발동':>6s} {'최종':>7s}")
for _, r in res.iterrows():
    print(f"{r.섹터캡:14s} {r.손절:8s} {r.CAGR:>+8.2%} {r.변동성:>8.1%} {r.MDD:>8.1%} "
          f"{r.Sharpe:>7.3f} {r.회전율*100:>4.0f}% {r.발동비율*100:>5.1f}% {r.최종:>6.2f}x")

print("\n손절 도입 효과 (손절없음 -> 손절10%)")
for cname, _, _ in CAPS:
    a = res[(res.섹터캡 == cname) & (res.손절 == "손절없음")].iloc[0]
    b = res[(res.섹터캡 == cname) & (res.손절 == "손절10%")].iloc[0]
    print(f"  {cname:14s} CAGR {a.CAGR:+.2%} -> {b.CAGR:+.2%} ({b.CAGR-a.CAGR:+.2%}p) | "
          f"MDD {a.MDD:.1%} -> {b.MDD:.1%} ({b.MDD-a.MDD:+.1%}p) | "
          f"Sharpe {a.Sharpe:.3f} -> {b.Sharpe:.3f} ({b.Sharpe-a.Sharpe:+.3f})")

navdf = pd.DataFrame(curves)
yr = pd.concat([navdf.iloc[[0]], navdf.resample("YE").last()])
rr = yr.pct_change().dropna()
rr.index = rr.index.year
print("\n연도별 수익률 (%)")
print((rr * 100).round(1).to_string())

res.to_csv(OUT_DIR / "섹터캡x손절_요약.csv", index=False, encoding="utf-8-sig")
navdf.to_csv(OUT_DIR / "섹터캡x손절_NAV.csv", encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/섹터캡x손절_요약.csv, _NAV.csv")
db.close()
