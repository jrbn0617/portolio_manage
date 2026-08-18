"""사내 알고리즘 제안서용 데이터 추출 → JSON. 읽기 전용.

주 소스는 제출용 백테스트 엑셀의 `시계열` 시트 하나다(2,697행). 누적곡선·낙폭·연도별·
월별·VaR을 전부 여기서 산출해 문서 안 수치가 한 소스에서 나오도록 한다.

지표 정의는 엔진(`backtest_service._compute_metrics`)과 맞춘다:
  샤프 = 일간수익률 산술평균 x 252 / 연변동성   (무위험이자율 미반영)
VaR은 예시 문서(키우GO)가 쓰는 칸을 채우되 공식이 불명확해 **1년 보유 수익률의 하위 5%
지점(경험적)**으로 정의하고 그렇게 표기한다.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import glob

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.backtest_service import (  # noqa: E402
    compute_free_float_weights, compute_momentum, compute_regime_exposure,
    find_halted_instruments, resolve_universe,
)
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg  # noqa: E402

XLSX = "/Users/mckim/Documents/GitHub/portolio_manage/reference/알고리즘1_코스피전체_백테스트_2015-2026.xlsx"
# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
OUT = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
OUT.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT / "proposal_data.json"
FORM = date(2026, 7, 31)
# 제안서 성과 구간 시작 — 사내 "키움 Momentum" 알고리즘 공시 개시 시점과 같은 출발선.
# 결과를 보고 고른 날짜가 아니라 외부 사건 기준이다.
START = "2017-05-22"


def metrics(s: pd.Series) -> dict:
    s = s / s.iloc[0]
    r = s.pct_change().dropna()
    years = (s.index[-1] - s.index[0]).days / 365.25
    vol = float(r.std(ddof=1) * np.sqrt(252))
    roll = (s / s.shift(252) - 1).dropna()
    return dict(
        cagr=float(s.iloc[-1] ** (1 / years) - 1),
        vol=vol,
        mdd=float((s / s.cummax() - 1).min()),
        sharpe=float(r.mean() * 252 / vol),
        var5=float(roll.quantile(0.05)) if len(roll) > 250 else None,
        cum=float(s.iloc[-1] - 1),
        years=years,
        n=int(len(s)),
    )


ts = pd.read_excel(XLSX, sheet_name="시계열")
ts.columns = ["date", "mp", "bm"]
ts["date"] = pd.to_datetime(ts["date"])
ts = ts.set_index("date").sort_index()
full = ts.copy()          # 전 구간(참고용)
ts = ts.loc[START:]       # 제안서 성과 구간

# 비교 대상 — RA 테스트베드 공시 「키움 Momentum」 적극투자형(acnutSn 776).
# 공시개시일이 2017-05-22로 본 제안서 성과 구간 시작과 동일하다.
# 수집: scripts/collect_ratestbed.py
_kp = glob.glob(str(Path(XLSX).parent / "테스트베드_키움*.csv"))
peer = None
if _kp:
    k = pd.read_csv(_kp[0])
    k = k[k.acnutSn == 776].copy()
    k["date"] = pd.to_datetime(k["date"])
    k = k.set_index("date").sort_index()["standardPrice"]
    k = k.loc[START:ts.index[-1]]
    peer = k

periods = {
    "all": ts,
    "oos": ts.loc[:"2019-12-31"],          # 설정값 선택에 쓰지 않은 구간
    "insample": ts.loc["2019-12-31":],     # 설정값 선택 구간
    "full": full,                          # 데이터 전 구간 (참고 각주용)
}
summary = {k: dict(start=str(v.index[0].date()), end=str(v.index[-1].date()),
                   mp=metrics(v["mp"]), bm=metrics(v["bm"])) for k, v in periods.items()}
if peer is not None:
    summary["all"]["peer"] = metrics(peer)
    summary["oos"]["peer"] = metrics(peer.loc[:"2019-12-31"])
    summary["insample"]["peer"] = metrics(peer.loc["2019-12-31":])

# 누적곡선 (주간 샘플링 — 인쇄 해상도에 충분하고 파일이 작아진다)
wk = ts.resample("W-FRI").last().dropna()
wk = wk / ts.iloc[0]
curve = dict(dates=[str(d.date()) for d in wk.index],
             mp=[round(float(v), 4) for v in wk["mp"]],
             bm=[round(float(v), 4) for v in wk["bm"]])
if peer is not None:
    pk = peer.reindex(ts.index).ffill()
    pk = (pk / pk.dropna().iloc[0]).resample("W-FRI").last().reindex(wk.index).ffill()
    curve["peer"] = [round(float(v), 4) if v == v else None for v in pk]

# 낙폭 — 일간으로 계산한 뒤 주별 **최저점**을 취한다. 주말 종가(.last())로 뽑으면
# 주중에 찍은 저점이 사라져 곡선의 최저치가 표의 MDD와 어긋난다(코스피 -37.0% vs -41.4%).
base = ts / ts.iloc[0]
dd = dict(dates=curve["dates"],
          mp=[round(float(v), 4) for v in (base["mp"] / base["mp"].cummax() - 1).resample("W-FRI").min().dropna()],
          bm=[round(float(v), 4) for v in (base["bm"] / base["bm"].cummax() - 1).resample("W-FRI").min().dropna()])

# 연도별 / 월별
ye = ts.resample("YE").last()
ye = pd.concat([ts.iloc[[0]], ye])
yearly = []
_pk_y = None
if peer is not None:
    _pkd = peer.reindex(ts.index).ffill()
    _pk_y = pd.concat([_pkd.iloc[[0]], _pkd.resample("YE").last()])
for i in range(1, len(ye)):
    y = ye.index[i].year
    pk = None
    if _pk_y is not None and i < len(_pk_y):
        a, b = _pk_y.iloc[i - 1], _pk_y.iloc[i]
        if a == a and b == b and a:
            pk = float(b / a - 1)
    yearly.append(dict(year=y, peer=pk,
                       mp=float(ye["mp"].iloc[i] / ye["mp"].iloc[i - 1] - 1),
                       bm=float(ye["bm"].iloc[i] / ye["bm"].iloc[i - 1] - 1),
                       partial=(y in (2017, 2026))))

me = ts.resample("ME").last()
me = pd.concat([ts.iloc[[0]], me])
monthly = []
for i in range(1, len(me)):
    d = me.index[i]
    monthly.append(dict(y=d.year, m=d.month,
                        mp=float(me["mp"].iloc[i] / me["mp"].iloc[i - 1] - 1)))

# 현재 포트폴리오 + 선정 깔때기
db = SessionLocal()
CFG = ScreenConfig(top_pct=0.5, min_sector_size=5, ttm_lag_days=90,
                   consensus_lag_days=0, peg_min=0.0, max_age_days=100)
uni = resolve_universe(db, "KOSPI", FORM)
tradable = [i for i in uni if i not in find_halted_instruments(db, uni, FORM, 10)]
passed = screen_by_ebitda_peg(db, tradable, FORM, config=CFG, warn=lambda m: None, info=lambda m: None)
mom = compute_momentum(db, passed, FORM)
ranked = sorted([i for i in passed if mom.get(i) is not None], key=lambda i: mom[i], reverse=True)
insts = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(ranked)).all()}
picks, cnt = [], {}
for i in ranked:
    if len(picks) >= 20:
        break
    g = insts[i].krx_sector or insts[i].sector or "미분류"
    if cnt.get(g, 0) >= 2:
        continue
    picks.append(i)
    cnt[g] = cnt.get(g, 0) + 1
w = compute_free_float_weights(db, picks, FORM, lambda m: None, mom, max_weight=0.25,
                               group_field="krx_sector", max_group_weight=0.50, min_weight=0.01)
exposure = compute_regime_exposure(db, FORM, benchmark_ticker="KOSPI", ma_window_days=200,
                                   bull_exposure=1.0, bear_exposure=0.5)
holdings = [dict(name=insts[i].name, ticker=insts[i].ticker,
                 sector=insts[i].krx_sector or insts[i].sector or "미분류",
                 weight=w[i] * exposure) for i in picks if w.get(i)]
holdings.sort(key=lambda r: -r["weight"])
by_sector = {}
for h in holdings:
    by_sector[h["sector"]] = by_sector.get(h["sector"], 0.0) + h["weight"]
sectors = sorted(({"name": k, "weight": v} for k, v in by_sector.items()),
                 key=lambda r: -r["weight"])
db.close()

funnel = [dict(label="코스피 상장 종목", n=len(uni)),
          dict(label="거래 가능 종목", n=len(tradable)),
          dict(label="재무 선별 통과", n=len(passed)),
          dict(label="순위 상위 · 업종 분산 적용", n=len(picks)),
          dict(label="최종 편입", n=len(holdings))]

eff_n = 1.0 / sum((h["weight"] / sum(x["weight"] for x in holdings)) ** 2 for h in holdings)

regime = json.loads((OUT / "momentum_regime.json").read_text())
diag = json.loads((OUT / "diag2019.json").read_text())
diag_sum = dict(
    A=sum(r["A"] for r in diag), B=sum(r["B"] for r in diag), C=sum(r["C"] for r in diag),
    stop_eff=sum(r["stop_eff"] for r in diag), exp_eff=sum(r["exp_eff"] for r in diag),
    kospi=sum(r["kospi"] for r in diag),
    avg_exposure=sum(r["exposure"] for r in diag) / len(diag),
    n_stop=sum(r["n_stop"] for r in diag), n_pos=sum(r["n"] for r in diag),
)
sec2019 = {}
for m in diag:
    for h in m["detail"]:
        s = sec2019.setdefault(h["sector"], {"contrib": 0.0, "n": 0})
        s["contrib"] += h["w"] * h["r"]
        s["n"] += 1
sec2019 = sorted(({"name": k, **v} for k, v in sec2019.items()), key=lambda r: r["contrib"])[:6]

OUT_FILE.write_text(json.dumps(dict(
    start=START, regime=regime, diag2019=diag_sum, sec2019=sec2019,
    summary=summary, curve=curve, dd=dd, yearly=yearly, monthly=monthly,
    holdings=holdings, sectors=sectors, funnel=funnel,
    exposure=exposure, eff_n=eff_n, formation=str(FORM),
    # 운용 통계는 **성과 구간과 같은 창(START~)** 으로 재실행한 실측값을 쓴다.
    # 이전에는 2017-06-01 시작 별도 실행값(110회/35%/28.3pt)을 하드코딩해 두어
    # 본문의 기간(2017-05-22)과 어긋나 있었다. 산출: analysis/algorithm1/ops_stats.py
    ops=json.loads((OUT / "ops_stats.json").read_text()),
), ensure_ascii=False, indent=2, default=str))

a, b = summary["all"]["mp"], summary["all"]["bm"]
print(f"전 구간 {summary['all']['start']} ~ {summary['all']['end']} ({a['years']:.2f}년, {a['n']}행)")
print(f"  전략  CAGR {a['cagr']:+.2%} 변동성 {a['vol']:.1%} MDD {a['mdd']:.1%} 샤프 {a['sharpe']:.3f} VaR {a['var5']:+.1%}")
print(f"  KOSPI CAGR {b['cagr']:+.2%} 변동성 {b['vol']:.1%} MDD {b['mdd']:.1%} 샤프 {b['sharpe']:.3f} VaR {b['var5']:+.1%}")
print(f"\n연도 {len(yearly)}개 · 월 {len(monthly)}개 · 곡선 {len(curve['dates'])}점")
print(f"현재 포트폴리오 {len(holdings)}종목 · 업종 {len(sectors)}개 · 실효N {eff_n:.1f} · 노출 {exposure:.0%}")
print("깔때기: " + " → ".join(f"{f['n']}" for f in funnel))
print(f"저장: {OUT_FILE}")
