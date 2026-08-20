"""배분 #1 — 경기 사이클 스위치 개요·성과 리포트. 읽기 전용.

계산은 `cycle_switch.py`(신호·비중)와 `backtest.py`(엔진)를 그대로 부른다 — 표와 그림이
어긋나지 않도록 원천을 한 곳에 둔다. 여기서는 집계와 HTML 생성만 한다.

사용법:  python analysis/allocation/cycle_switch_report.py [--from 2007-12-31] [--cost 0.001]
"""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cycle_switch as cs  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
SP = Path(os.environ.get("ALGO_OUT",
                         Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)

NAMES = ["MP1", "MP2", "MP3", "BM"]
ASSET_LABEL = {"SPXNTR": "미국주식", "MSCIEAFENTR": "선진국주식", "MSCIEMNTR": "신흥국주식",
               "US30Y": "미국채 20Y+", "US10Y": "미국채 7-10Y", "GOLD": "금",
               "USBIL": "현금(T-Bill)"}
GROUP = {"SPXNTR": "주식", "MSCIEAFENTR": "주식", "MSCIEMNTR": "주식",
         "US30Y": "채권", "US10Y": "채권", "GOLD": "금", "USBIL": "현금"}


def build(start: str, cost: float) -> dict:
    px, res, engine_ms = cs.run_backtest(cost, start)
    nav, stats = res["nav"], res["stats"]

    db = SessionLocal()
    try:
        cyc = cs.calc_cycle_data(cs.read_economic_data(db), cs.read_underlying_price(db))
    finally:
        db.close()
    key = cyc.astype(int).astype(str).sum(axis=1)

    # ── 곡선 — 주 단위로 솎는다. 낙폭은 일별로 구한 뒤 주간 최솟값을 취해 깊이를 보존한다
    dd = nav / nav.cummax() - 1
    w_nav = nav.resample("W-FRI").last().dropna(how="all")
    w_dd = dd.resample("W-FRI").min().dropna(how="all")

    # ── 연도별
    yr = nav.resample("YE").last()
    yr = pd.concat([nav.iloc[[0]], yr]).pct_change().dropna(how="all")
    # 시작일이 연말이면 resample 첫 행과 날짜가 겹쳐 0% 짜리 가짜 해가 생긴다(2007-12-31).
    yr = yr[yr.index > nav.index[0]]
    yearly = [{"year": d.year, **{n: round(float(yr.loc[d, n]), 5) for n in NAMES}}
              for d in yr.index]

    # ── 국면별 성과 (MP1 월간)
    mret = nav["MP1"].resample("ME").last().pct_change()
    j = pd.concat([key.rename("k"), mret.rename("r")], axis=1).dropna()
    grp = j.groupby("k")["r"].agg(["count", "mean"])
    regimes = []
    for k in sorted(cs.weight_sheet):
        row = grp.loc[k] if k in grp.index else None
        sub = key[key == k]
        regimes.append({
            "key": k, "weights": cs.weight_sheet[k],
            "months_all": int((key == k).sum()),
            "share_all": round(float((key == k).mean()), 4),
            "months": int(row["count"]) if row is not None else 0,
            "annualized": round(float((1 + row["mean"]) ** 12 - 1), 5) if row is not None else None,
            "first": str(sub.index[0].date()) if len(sub) else None,
        })

    # ── 스위치 요약
    switches = [{"name": c,
                 "on_ratio": round(float(cyc[c].mean()), 4),
                 "flips": int((cyc[c].diff().abs() > 0).sum())} for c in cyc.columns]

    # ── 자산배분 추이 (MP1, 월말)
    w = res["detail"]["MP1"]["weights"].resample("ME").last().dropna(how="all")
    order = ["SPXNTR", "MSCIEAFENTR", "MSCIEMNTR", "GOLD", "US30Y", "US10Y", "USBIL"]
    weights = {"dates": [d.strftime("%Y-%m") for d in w.index],
               "order": order,
               "series": {c: [round(float(v), 4) for v in w[c]] for c in order}}

    latest = cyc.iloc[-1]
    latest_key = key.iloc[-1]
    mp_now = {f"MP{r + 1}": {t: round(float(v), 4)
                             for t, v in cs.gen_mp(cyc, risk=r).iloc[-1].items() if v > 1e-9}
              for r in (0, 1, 2)}

    return dict(
        period=[str(nav.index[0].date()), str(nav.index[-1].date())],
        signal_period=[str(cyc.index[0].date()), str(cyc.index[-1].date())],
        cost=cost, engine_ms=round(engine_ms, 1),
        days=len(px), assets=int(px.shape[1]),
        stats={n: {k: (None if pd.isna(v) else round(float(v), 6))
                   for k, v in stats.loc[n].items()} for n in NAMES},
        curve={"dates": [d.strftime("%Y-%m-%d") for d in w_nav.index],
               "nav": {n: [round(float(v), 3) for v in w_nav[n]] for n in NAMES},
               "dd": {n: [round(float(v), 5) for v in w_dd[n]] for n in NAMES}},
        yearly=yearly, regimes=regimes, switches=switches, weights=weights,
        switch_history={"dates": [d.strftime("%Y-%m") for d in cyc.index],
                        "series": {c: [int(v) for v in cyc[c]] for c in cyc.columns}},
        latest={"date": str(cyc.index[-1].date()), "key": latest_key,
                "switch": {c: int(latest[c]) for c in cyc.columns},
                "weight_sheet": cs.weight_sheet[latest_key],
                "next_rebalance": str((pd.Timestamp(cyc.index[-1]) +
                                       pd.offsets.QuarterEnd(0)).date()),
                "mp": mp_now},
        asset_label=ASSET_LABEL, group=GROUP,
        ticker_map={k: v for k, v in cs.TICKER_MAP.items()},
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", default="2007-12-31")
    p.add_argument("--cost", type=float, default=0.001)
    a = p.parse_args()

    D = build(a.start, a.cost)
    (SP / "cycle_switch.json").write_text(json.dumps(D, ensure_ascii=False, indent=1))

    from cycle_switch_html import render  # noqa: E402
    (SP / "cycle_switch.html").write_text(render(D))
    print(f"저장: {SP / 'cycle_switch.json'}")
    print(f"      {SP / 'cycle_switch.html'}")
    s = D["stats"]["MP1"]
    print(f"MP1 CAGR {s['cagr']:.2%} · 샤프(rf) {s['sharpe_rf']:.2f} · MDD {s['mdd']:.1%}"
          f" · 현재 국면 [{D['latest']['key']}]")
