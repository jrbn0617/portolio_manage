"""알고리즘 #2(기관 수급 필터 + 모멘텀) 백테스트를
reference/(별첨 2) 백테스팅자료_회사명(예시).xlsx 포맷으로 출력한다.

확정안(docs/algorithms/algorithm2-overview.md 3번):
  코스피200+코스닥150 -> 기관합계 60일 순매수/거래대금 하위 40% 통과
  -> 6개월(126거래일, 최근 21일 제외) 모멘텀 상위 20종목, 케이산업(krx_sector)당 2종목 제한
  -> 동일가중, 20거래일 리밸런싱, 월중 손절 -10%(익일시가 체결, 현금 동결, 재배분 없음)
  -> 왕복 거래비용 0.30%

계산 로직은 analysis/algorithm2/backtest_cap_x_stop.py 와 동일하다(그쪽은 섹터캡 x 손절
2x3 비교용, 이쪽은 확정안 1개만 돌려 제출 포맷으로 저장). 알고리즘 #1과 달리
app/services/backtest_service.py 엔진을 쓰지 않는데, 엔진이 20거래일 리밸런싱과
수급 필터를 지원하지 않기 때문이다(overview 3번의 "운용 스크립트는 아직 만들지 않았다").

시트 작성 함수는 export_weight_scheme_backtest_excel(알고리즘 #1)에서 그대로 가져다 쓴다
— 두 제출 파일의 포맷이 어긋나지 않게 하려는 것이다.

사용법: python scripts/export_algorithm2_backtest_excel.py
        [--start 2020-01-01] [--end 2026-04-30] [--benchmark KOSPI200] [--out ...]
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.backtest_service import _compute_metrics  # noqa: E402
from scripts.export_weight_scheme_backtest_excel import (  # noqa: E402
    build_guide_sheet,
    build_rebalance_sheet,
    build_timeseries_sheet,
)

# 확정 파라미터 (algorithm2-overview.md 3번)
DATA_START = "2019-01-01"  # 신호 워밍업용 — 출력 구간(--start)보다 앞서야 한다
SKIP, MOM_N = 21, 126  # 모멘텀: 최근 21거래일 제외 126거래일
HOLD, TOP_N = 20, 20  # 20거래일 리밸런싱, 20종목
FLOW_WINDOW, FLOW_KEEP = 60, 0.40  # 기관 순매수 60일 누적, 하위 40% 통과
CAP_FIELD, CAP_N = "krx_sector", 2  # 케이산업당 2종목
STOP_LOSS = 0.10  # -10%, 익일시가 체결
COST = 0.0030  # 왕복 0.30%

# 레짐필터(--regime). 알고리즘 #1 실험7/9에서 확정된 값을 그대로 쓴다 — 여기서 재튜닝하면
# overview 6번의 다중비교 문제만 키운다. 판정 기준은 유니버스와 같은 50:50 합성지수다
# (코스피200 단독으로 보면 코스닥 쪽 레짐을 놓친다).
MA_WINDOW, BEAR_EXPOSURE = 200, 0.5

BM_LABEL_MAP = {
    "BLEND": "KOSPI200:KOSDAQ150 50:50(BM)",
    "KOSPI200": "KOSPI200(BM)",
    "KOSDAQ150": "KOSDAQ150TR(BM)",
    "KOSPI": "KOSPI(BM)",
    "KOSDAQ": "KOSDAQ(BM)",
}
BLEND_TICKERS = ["KOSPI200", "KOSDAQ150"]  # 유니버스가 두 지수의 합집합이라 BM도 반반으로 합성
ASSET_LABEL = (
    "코스피200+코스닥150 기관수급필터+6개월모멘텀+월중손절10%(익일시가)"
    "_케이산업당2개이하(동일가중)"
)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 4, 30))
    p.add_argument("--benchmark", choices=list(BM_LABEL_MAP), default="BLEND")
    p.add_argument("--regime", action="store_true", help=f"레짐필터 적용({MA_WINDOW}일선, 약세시 {BEAR_EXPOSURE:.0%})")
    p.add_argument("--out", default=None)
    return p.parse_args()


UNIVERSE_CTE = """
with u as (select distinct instrument_id iid from index_memberships
           where index_name in ('KOSDAQ150','KOSPI200'))
"""


def load_frames(db, end: date):
    """유니버스 전체의 일별 패널을 피벗해서 반환. 모두 adj와 같은 (날짜 x 종목) 격자."""

    def pivot(sql, params):
        df = pd.read_sql(text(sql), db.bind, params=params)
        p = df.pivot(index="date", columns="instrument_id", values="value").sort_index()
        p.index = pd.to_datetime(p.index)
        return p

    params = {"s": DATA_START, "e": end}
    adj = pivot(
        UNIVERSE_CTE + """select d.date,d.instrument_id,d.adj_close::float value
         from dividend_adjusted_prices d join u on u.iid=d.instrument_id
         where d.period='D' and d.date>=:s and d.date<=:e""",
        params,
    )
    p_open = pivot(
        UNIVERSE_CTE + """select p.date,p.instrument_id,p.open::float value from prices p
         join u on u.iid=p.instrument_id
         where p.period='D' and p.date>=:s and p.date<=:e and p.open is not null""",
        params,
    ).reindex_like(adj)
    p_close = pivot(
        UNIVERSE_CTE + """select p.date,p.instrument_id,p.close::float value from prices p
         join u on u.iid=p.instrument_id where p.period='D' and p.date>=:s and p.date<=:e""",
        params,
    ).reindex_like(adj)
    amt = pivot(
        UNIVERSE_CTE + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
         join u on u.iid=s.instrument_id where s.date>=:s and s.date<=:e""",
        params,
    ).reindex_like(adj)
    flow = pivot(
        UNIVERSE_CTE + """select it.date,it.instrument_id,it.net_value::float value
         from investor_trading it join u on u.iid=it.instrument_id
         where it.date>=:s and it.date<=:e and it.investor_type='기관합계'""",
        params,
    ).reindex_like(adj)
    return adj, p_open, p_close, amt, flow


def blended_regime(db, end: date) -> pd.Series:
    """날짜 -> 노출비중(1.0 강세 / BEAR_EXPOSURE 약세).

    코스피200:코스닥150 50:50 합성지수(일간수익률 constant-mix)의 종가가 자기
    MA_WINDOW 거래일 이동평균 위/아래인지로 판정한다. 이평 계산에 필요한 거래일이
    모자라는 초반 구간은 강세로 간주(backtest_service.compute_regime_exposure와 동일).
    """
    df = pd.read_sql(
        text("""select i.ticker, p.date, p.close::float c
                from prices p join instruments i on i.id=p.instrument_id
                where i.ticker in ('KOSPI200','KOSDAQ150') and p.period='D' and p.date<=:e
                order by p.date"""),
        db.bind,
        params={"e": end},
    )
    df["date"] = pd.to_datetime(df["date"])
    piv = df.pivot(index="date", columns="ticker", values="c").dropna()
    level = (piv.pct_change().mean(axis=1) + 1.0).cumprod()  # 반반 constant-mix
    level.iloc[0] = 1.0
    ma = level.rolling(MA_WINDOW, min_periods=MA_WINDOW).mean()
    return pd.Series(
        np.where(ma.isna() | (level > ma), 1.0, BEAR_EXPOSURE), index=level.index
    )


def run_backtest(db, end: date, use_regime: bool = False):
    """확정안 1개를 돌려 (일별 NAV Series, 리밸런싱 로그, 회전율, 손절 발동비율, 약세회차) 반환."""
    adj, p_open, p_close, amt, flow = load_frames(db, end)
    regime = blended_regime(db, end) if use_regime else None

    sectors = pd.read_sql(
        text(UNIVERSE_CTE + "select i.id, i.krx_sector from instruments i join u on u.iid=i.id"),
        db.bind,
    ).set_index("id")["krx_sector"]

    memb = pd.read_sql(
        text("""select as_of_date,instrument_id from index_memberships
                where index_name in ('KOSDAQ150','KOSPI200')"""),
        db.bind,
    )
    memb["as_of_date"] = pd.to_datetime(memb["as_of_date"])
    snaps = sorted(memb["as_of_date"].unique())
    snap_members = {s: set(memb.loc[memb.as_of_date == s, "instrument_id"]) for s in snaps}

    fsig = flow.rolling(FLOW_WINDOW, min_periods=FLOW_WINDOW).sum() / amt.rolling(
        FLOW_WINDOW, min_periods=FLOW_WINDOW
    ).sum()
    mom = adj.shift(SKIP) / adj.shift(SKIP + MOM_N) - 1

    days = list(adj.index)
    rebal = list(range(MOM_N + SKIP + FLOW_WINDOW, len(days) - 1, HOLD))
    A, OP, CL = adj.values, p_open.values, p_close.values
    col_ix = {c: i for i, c in enumerate(adj.columns)}

    def universe_at(d):
        el = [s for s in snaps if s <= d]
        return snap_members[max(el)] if el else set()

    def sector_of(i):
        g = sectors.get(i)
        return "미분류" if g is None or (isinstance(g, float) and np.isnan(g)) else g

    navs, dates = [1.0], [days[rebal[0]]]
    prev, turnovers, hits, positions, bear = {}, [], 0, 0, 0
    log = []

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

        picks, cnt = [], {}
        for i in m.index:
            if len(picks) >= TOP_N:
                break
            g = sector_of(i)
            if cnt.get(g, 0) >= CAP_N:
                continue
            picks.append(i)
            cnt[g] = cnt.get(g, 0) + 1
        if len(picks) < TOP_N:
            continue

        # 레짐 약세면 종목별 비중 전체에 노출비중을 곱하고 나머지는 현금(무수익)으로 둔다.
        exposure = 1.0 if regime is None else float(regime.loc[:d].iloc[-1])
        bear += exposure < 1.0
        w = {p: exposure / len(picks) for p in picks}
        # 회전율은 노출비중 반영 후 기준 — 레짐 전환만으로도 실제 매매가 발생한다.
        turn = sum(abs(w.get(k, 0) - prev.get(k, 0)) for k in set(w) | set(prev)) / 2
        turnovers.append(turn)
        navs[-1] *= 1 - turn * COST  # 리밸런싱 비용은 직전 구간 종료 NAV에서 차감

        log.append({"date": d.date(), "instrument_ids": list(picks), "weights": dict(w)})

        idx = [col_ix[p] for p in picks]
        wv = np.full(len(idx), exposure / len(idx))
        cash = 1.0 - exposure
        entry, base = A[ri, idx], navs[-1]
        period_end = min(ri + HOLD, len(days) - 1)
        frozen, freeze_from = np.full(len(idx), np.nan), np.full(len(idx), 10**9)
        positions += len(idx)

        for j in range(ri + 1, period_end + 1):
            v = np.where(np.isnan(A[j, idx] / entry), 1.0, A[j, idx] / entry)
            for k in np.where(np.isnan(frozen) & (v <= 1 - STOP_LOSS))[0]:
                gap = 1.0
                if j < len(days) - 1:
                    o, c = OP[j + 1, idx[k]], CL[j, idx[k]]
                    if not np.isnan(o) and not np.isnan(c) and c:
                        gap = o / c
                frozen[k], freeze_from[k] = v[k] * gap, j + 1
                hits += 1
            cur = v.copy()
            for k in np.where(~np.isnan(frozen))[0]:
                if j >= freeze_from[k]:
                    cur[k] = frozen[k]
            navs.append(base * (cash + float((wv * cur).sum())))
            dates.append(days[j])
        prev = w

    nav = pd.Series(navs, index=dates)
    return nav, log, float(np.mean(turnovers)), hits / max(positions, 1), bear, len(turnovers)


def _index_closes(db, ticker: str, official: list[date]) -> dict[date, float]:
    rows = (
        db.query(Price.date, Price.close)
        .join(Instrument, Instrument.id == Price.instrument_id)
        .filter(Instrument.ticker == ticker, Price.period == "D", Price.date.in_(official))
        .all()
    )
    closes = {r.date: float(r.close) for r in rows}
    missing = [d for d in official if d not in closes]
    if missing:
        print(f"경고: {ticker}에 없는 날짜 {len(missing)}건 (예: {missing[:5]}) — 직전값으로 이월")
    return closes


def benchmark_series(db, benchmark: str, official: list[date]) -> list[float]:
    """official 첫날을 100으로 리베이스한 BM 시계열.

    "BLEND"는 코스피200:코스닥150 = 50:50 합성지수다. 유니버스가 두 지수의 합집합이라
    한쪽 지수만 BM으로 쓰면 비교가 한쪽에 치우친다. 지수 레벨끼리 더하면 두 지수의
    절대 포인트 차이(코스피200 ~수백 vs 코스닥150 ~천 단위)에 비중이 끌려가므로,
    **일간수익률을 50:50으로 섞어 매일 반반으로 되돌리는 constant-mix** 방식으로 합성한다
    (합성 벤치마크의 표준). 결측일은 직전 종가로 이월해 그날 수익률 0으로 취급한다.
    """
    tickers = BLEND_TICKERS if benchmark == "BLEND" else [benchmark]
    closes = {t: _index_closes(db, t, official) for t in tickers}

    paths: dict[str, list[float]] = {}
    for t in tickers:
        series, last = [], None
        for d in official:
            last = closes[t].get(d, last)
            if last is None:  # 첫날부터 결측이면 그 지수는 못 쓴다
                raise SystemExit(f"{t}: {d} 종가가 없어 BM을 만들 수 없습니다.")
            series.append(last)
        paths[t] = series

    w = 1.0 / len(tickers)
    values = [100.0]
    for i in range(1, len(official)):
        blended = sum(w * (paths[t][i] / paths[t][i - 1] - 1.0) for t in tickers)
        values.append(values[-1] * (1.0 + blended))
    return values


def main():
    args = parse_args()
    db = SessionLocal()

    nav, log, turnover, hit_rate, bear, n_rebal = run_backtest(db, args.end, use_regime=args.regime)

    official = [d.date() for d in nav.index if args.start <= d.date() <= args.end]
    if not official:
        raise SystemExit("출력 구간에 NAV가 없습니다. --start/--end를 확인하세요.")
    anchor = official[0]
    nav_by_date = {d.date(): float(v) for d, v in nav.items()}
    anchor_nav = nav_by_date[anchor]

    mp_values = [nav_by_date[d] / anchor_nav * 100 for d in official]
    bm_values = benchmark_series(db, args.benchmark, official)

    # 출력 시작일 시점에 실제로 보유 중이던 리밸런싱(직전 회차)부터 싣는다.
    first_ix = max([i for i, r in enumerate(log) if r["date"] <= anchor] or [0])
    shown = [r for r in log[first_ix:] if r["date"] <= args.end]

    all_ids = {i for r in shown for i in r["instrument_ids"]}
    inst_by_id = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(all_ids)).all()}
    # build_rebalance_sheet는 ticker 기준이라 로그를 ticker로 변환해서 넘긴다.
    rebalances = [
        {
            "date": r["date"],
            "holdings": [inst_by_id[i].ticker for i in r["instrument_ids"]],
            "weights": {inst_by_id[i].ticker: w for i, w in r["weights"].items()},
        }
        for r in shown
    ]
    instruments_by_ticker = {i.ticker: i for i in inst_by_id.values()}

    wb = Workbook()
    wb.remove(wb.active)
    build_guide_sheet(wb)
    build_timeseries_sheet(wb, official, mp_values, bm_values, bm_label=BM_LABEL_MAP[args.benchmark])
    build_rebalance_sheet(wb, rebalances, instruments_by_ticker, asset_label=ASSET_LABEL)

    out_path = Path(args.out or "../reference/(별첨 2) 백테스팅자료_알고리즘2_코스피200코스닥150.xlsx")
    wb.save(out_path)

    m = _compute_metrics(list(zip(official, mp_values)))
    bm = _compute_metrics(list(zip(official, bm_values)))
    db.close()

    print(f"\n저장 완료: {out_path}")
    print(f"  시계열: {len(official)}행 ({official[0]} ~ {official[-1]})")
    print(f"  리밸런싱: {len(rebalances)}회, 누적종목수: {len(all_ids)}개")
    print(f"  평균 회전율: {turnover:.1%}/회, 손절 발동비율: {hit_rate:.1%}")
    if args.regime:
        print(f"  레짐필터: {MA_WINDOW}일선(50:50 합성지수), 약세 판정 {bear}/{n_rebal}회 → 비중 {BEAR_EXPOSURE:.0%}")
    print(
        f"  전략  최종지수: {mp_values[-1]:.1f}  누적: {m['cumulative_return']:+.1%}  CAGR: {m['cagr']:+.1%}  "
        f"연변동성: {m['annualized_volatility']:.1%}  MDD: {m['mdd']:.1%}  샤프: {m['sharpe']:.2f}"
    )
    print(
        f"  {BM_LABEL_MAP[args.benchmark]:<16s} 최종지수: {bm_values[-1]:.1f}  누적: {bm['cumulative_return']:+.1%}  "
        f"CAGR: {bm['cagr']:+.1%}  연변동성: {bm['annualized_volatility']:.1%}  MDD: {bm['mdd']:.1%}  "
        f"샤프: {bm['sharpe']:.2f}"
    )


if __name__ == "__main__":
    main()
