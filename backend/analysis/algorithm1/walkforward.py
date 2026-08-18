"""알고리즘 #1 워크포워드(시간분할 재추정). 읽기 전용 — DB 쓰기·외부 호출 없음.

선등록: docs/algorithms/algorithm1-walkforward-prereg.md (2026-08-18, 데이터 보기 전 확정)

**홀드아웃을 소비하지 않는다.** 설정값 선택 구간(2020-01~2026-07) 내부만 시간순으로 쪼갠다.

핵심 착안 — 파라미터는 구간 내내 고정이므로 **설정마다 전 구간을 1회만 실행**하고
폴드별로 학습창·검증창을 슬라이스하면 폴드마다 재실행한 것과 결과가 같다. 12회 실행으로
4폴드 × 12설정을 모두 커버한다.

주의: 엔진 nav_series는 첫 형성일부터 시작하므로 창 경계마다 반드시 rebase 한다
(methodology §1.2 — 이 함정에 두 번 걸렸다).
"""
import json
import os
import sys
from datetime import date
from functools import partial
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402,F401  ← 모델 전체 등록. 빼면 매퍼 오류
from app.db.session import SessionLocal  # noqa: E402
from app.services.backtest_service import (  # noqa: E402
    BacktestConfig, TransactionCost, compute_free_float_weights,
    compute_regime_exposure, run_momentum_backtest,
)
from app.services.factor_screen_service import ScreenConfig, screen_by_ebitda_peg  # noqa: E402

# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
SP = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)
# 가용 데이터 전체. 2015-08은 알고리즘이 가동 가능한 최초 시점(선행 EV/EBITDA 2013-12 +
# 12개월 모멘텀·200일 레짐 lookback). 홀드아웃 저촉 여부는 선등록 1절 참조 — 판정을 내지
# 않는 진단이고, 해당 구간 성과는 보고서 [참고 2]에 이미 공개돼 있다.
START, END = date(2015, 8, 1), date(2026, 7, 31)

# 선등록 2절 — 확장창(anchored) 9폴드. 학습창은 항상 START부터, 검증창은 다음 역년.
FOLDS = [(f"F{i}", START, date(y, 12, 31), date(y + 1, 1, 1),
          date(y + 1, 12, 31) if y + 1 < 2026 else END)
         for i, y in enumerate(range(2017, 2026), start=1)]

BASE = dict(max_weight=0.25, top_n=20, stop_loss_pct=0.10, max_per_sector=2, screen_top_pct=0.5)

# 선등록 3절 — one-factor-at-a-time 격자
GRID = {"종목당 최대비중": ("max_weight", [0.20, 0.25, 0.30]),
        "편입 종목수":     ("top_n", [15, 20, 25]),
        "손실 제한 폭":    ("stop_loss_pct", [0.08, 0.10, 0.15]),
        "업종당 종목수":   ("max_per_sector", [2, 3]),
        "재무 선별 강도":  ("screen_top_pct", [0.3, 0.5, 0.7])}


def run(**kw):
    p = {**BASE, **kw}
    db = SessionLocal()
    r = run_momentum_backtest(
        db,
        BacktestConfig(index_name="KOSPI", top_n=p["top_n"], max_per_sector=p["max_per_sector"],
                       sector_count_field="krx_sector", start_date=START, end_date=END),
        on_warning=lambda m: None, on_info=lambda m: None,
        screen_fn=partial(screen_by_ebitda_peg,
                          config=ScreenConfig(top_pct=p["screen_top_pct"], min_sector_size=5,
                                              ttm_lag_days=90, consensus_lag_days=0,
                                              peg_min=0.0, max_age_days=100),
                          warn=lambda m: None, info=lambda m: None),
        weight_fn=partial(compute_free_float_weights, max_weight=p["max_weight"],
                          group_field="krx_sector", max_group_weight=0.50, min_weight=0.01),
        exposure_fn=partial(compute_regime_exposure, benchmark_ticker="KOSPI",
                            ma_window_days=200, bull_exposure=1.0, bear_exposure=0.5),
        stop_loss_pct=p["stop_loss_pct"], stop_loss_execution="next_open",
        stop_loss_mode="cash", cost=TransactionCost(sell_tax=0.0020, commission=0.00015))
    db.close()
    return r.nav_series


def window(nav, a, b):
    """[a,b] 구간을 잘라 **시작점으로 rebase** 한 뒤 지표 산출 (methodology §1.2)."""
    pts = [(d, v) for d, v in nav if a <= d <= b]
    if len(pts) < 30:
        return None
    base = pts[0][1]
    vals = np.array([v / base for _, v in pts])
    ret = np.diff(vals) / vals[:-1]
    vol = float(ret.std(ddof=1) * np.sqrt(252))
    yrs = (pts[-1][0] - pts[0][0]).days / 365.25
    peak = np.maximum.accumulate(vals)
    return dict(sharpe=float(ret.mean() * 252 / vol) if vol else 0.0,
                cagr=float(vals[-1] ** (1 / yrs) - 1), vol=vol,
                mdd=float((vals / peak - 1).min()), n=len(pts))


# ── 실행: 설정별 1회 ────────────────────────────────────────────────────────
settings = {}
for label, (key, vals) in GRID.items():
    for v in vals:
        settings.setdefault((key, v), None)
print(f"고유 설정 {len(settings)}개 실행 (전 구간 {START} ~ {END})\n")

navs = {}
for i, (key, v) in enumerate(sorted(settings, key=str), 1):
    print(f"  [{i}/{len(settings)}] {key}={v} …", flush=True)
    navs[(key, v)] = run(**{key: v})

# ── 폴드별 선택 ────────────────────────────────────────────────────────────
out = {}
print("\n" + "=" * 78)
for label, (key, vals) in GRID.items():
    print(f"\n■ {label}   (현행 {BASE[key]})")
    hdr = "".join(f"{str(v):>10}" for v in vals)
    print(f"  {'폴드':<5}{'검증연도':<9}" + f"{'학습창 샤프':<{max(2,len(vals)*10-8)}}" + hdr[len(hdr)//len(vals)*0:] * 0
          + hdr + f"{'선택':>9}{'검증샤프':>10}")
    picks, rows = [], []
    for fname, ts, te, vs, ve in FOLDS:
        tr = {v: window(navs[(key, v)], ts, te) for v in vals}
        tr = {v: m for v, m in tr.items() if m}
        if not tr:
            continue
        best = max(tr, key=lambda v: tr[v]["sharpe"])
        va = window(navs[(key, best)], vs, ve)
        picks.append(best)
        print(f"  {fname:<5}{str(vs.year):<9}" + "".join(f"{tr[v]['sharpe']:>10.3f}" for v in vals)
              + f"{str(best):>9}{(va['sharpe'] if va else float('nan')):>10.3f}")
        rows.append(dict(fold=fname, train={str(v): tr[v] for v in tr}, pick=best, valid=va))
    n = len(picks)
    uniq = max(picks.count(x) for x in set(picks)) if picks else 0
    # 선등록 5절 — 9폴드 기준 7 이상 안정 / 5~6 대체로 안정 / 4 이하 불안정
    verdict = "안정" if uniq >= 7 else ("대체로 안정" if uniq >= 5 else "불안정")
    keep = "○" if all(p == BASE[key] for p in picks) else "✗"
    print(f"  → 선택값 {picks}")
    print(f"    최빈 {uniq}/{n} → **{verdict}**   현행값({BASE[key]})이 전 폴드에서 선택됐는가: {keep}")
    out[label] = dict(param=key, grid=vals, current=BASE[key], picks=[str(p) for p in picks],
                      max_agree=uniq, n_folds=n, verdict=verdict, folds=rows)

(SP / "walkforward.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
print("\n" + "=" * 78)
print(f"{'설정값':16}{'현행':>8}{'판정':>13}{'최빈':>7}   폴드별 선택값(F1→F9)")
for label, r in out.items():
    print(f"{label:16}{str(r['current']):>8}{r['verdict']:>13}"
          f"{r['max_agree']}/{r['n_folds']:>4}   {' '.join(r['picks'])}")
print(f"\n저장: {SP/'walkforward.json'}")
