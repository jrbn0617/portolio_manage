"""연도별 모멘텀 우호도 진단 — "직전 해 오른 종목이 다음 해에도 오르는가".

알고리즘의 파라미터를 건드리지 않는 **시장 구조 진단**이다. 코스피 종목의 전년 수익률과
당해 수익률의 횡단면 관계를 보고, 그 해가 모멘텀에 우호적이었는지(추세 지속) 아니면
비우호적이었는지(반전)를 판정한다.

  스피어만 상관 > 0  → 추세 지속 (모멘텀 우호)
  스피어만 상관 < 0  → 반전 (모멘텀 비우호)

분위 스프레드는 전년 수익률 상위 20%와 하위 20%의 당해 수익률 차이다.

읽기 전용. 홀드아웃 파라미터 탐색이 아니라 시장 국면 서술이므로 추가 소비가 아니다.
"""
import sys
from datetime import date
from pathlib import Path

import os

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.dividend_adjusted_price import DividendAdjustedPrice  # noqa: E402
from app.services.backtest_service import resolve_universe  # noqa: E402

# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
OUT = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
OUT.mkdir(parents=True, exist_ok=True)

db = SessionLocal()

# 연말 배당조정 종가 (월간 시계열 사용)
rows = db.execute(
    __import__("sqlalchemy").text("""
        SELECT d.instrument_id iid, d.date, d.adj_close::float v
        FROM dividend_adjusted_prices d
        WHERE d.period='M' AND d.date >= '2014-11-01'
    """)
).fetchall()
df = pd.DataFrame(rows, columns=["iid", "date", "v"])
df["date"] = pd.to_datetime(df["date"])
df["ym"] = df["date"].dt.to_period("M")
dec = df[df["date"].dt.month == 12].set_index(["iid", df[df["date"].dt.month == 12]["date"].dt.year])["v"]
px = dec.unstack()  # index=iid, columns=year (연말 종가)

print(f"연말 종가 매트릭스: {px.shape[0]}종목 x {px.shape[1]}년 ({list(px.columns)[:3]}...{list(px.columns)[-1]})\n")

# 전략 초과성과 (참고용, proposal_data.json)
import json  # noqa: E402
PD = json.loads((OUT / "proposal_data.json").read_text())
excess = {r["year"]: r["mp"] - r["bm"] for r in PD["yearly"]}

print(f"{'연도':6s}{'표본':>6s}{'상관':>8s}{'t값':>9s}{'상위20%':>9s}{'하위20%':>9s}{'스프레드':>10s}"
      f"{'국면':>10s}{'전략초과':>10s}")
out = []
for y in range(2016, 2027):
    if y not in px.columns or (y - 1) not in px.columns or (y - 2) not in px.columns:
        continue
    uni = set(resolve_universe(db, "KOSPI", date(y, 1, 2)))
    if not uni:
        continue
    sub = px.loc[px.index.isin(uni), [y - 2, y - 1, y]].dropna()
    if len(sub) < 100:
        continue
    prior = sub[y - 1] / sub[y - 2] - 1     # 전년 수익률
    cur = sub[y] / sub[y - 1] - 1           # 당해 수익률
    rho = float(prior.rank().corr(cur.rank()))  # 스피어만 = 순위 피어슨
    n_ = len(sub)
    t_ = rho * np.sqrt((n_ - 2) / max(1 - rho**2, 1e-12))
    q = pd.qcut(prior.rank(method="first"), 5, labels=False)
    hi, lo = cur[q == 4].median(), cur[q == 0].median()
    regime = "추세지속" if rho > 0.05 else ("반전" if rho < -0.05 else "중립")
    ex = excess.get(y)
    print(f"{y:<6d}{len(sub):>6d}{rho:>+8.3f}{t_:>9.1f}{hi*100:>8.1f}%{lo*100:>8.1f}%"
          f"{(hi-lo)*100:>+9.1f}%p{regime:>10s}"
          + (f"{ex*100:>+9.1f}%p" if ex is not None else f"{'-':>10s}"))
    out.append(dict(year=y, n=len(sub), rho=float(rho), t=float(t_),
                    hi=float(hi), lo=float(lo), spread=float(hi - lo),
                    regime=regime, excess=ex))

db.close()
(OUT / "momentum_regime.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))

r = np.array([o["rho"] for o in out])
e = np.array([o["excess"] for o in out if o["excess"] is not None])
if len(e) == len(r):
    print(f"\n상관(모멘텀우호도 rho vs 전략초과성과): {np.corrcoef(r, e)[0,1]:+.3f}")
