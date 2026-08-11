"""D) 종목모멘텀을 '산업평균 성분'과 '잔차(산업대비 상대모멘텀) 성분'으로 분해해서
어느 쪽이 그 종목의 forward 수익률을 더 잘 예측하는지 검증한다(Moskowitz-Grinblatt류
"industry momentum" 분해와 동일한 아이디어).

stock_momentum(i) = industry_avg_momentum(industry(i)) + residual_momentum(i)   (항등식)

1) 세 신호(raw/industry_avg/residual) 각각의 단변량 IC(스피어만, 종목 단위 cross-section)
2) Fama-MacBeth류 이변량 회귀: forward_return ~ industry_avg(표준화) + residual(표준화)
   매달 계수를 구해서 시계열 평균/t-stat — "다른 하나를 통제했을 때" 각 성분의 한계
   설명력을 본다(1번의 단순 상관과 달리 두 성분이 서로 경합할 때의 진짜 기여도).
"""
import calendar
import statistics
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
from app.models.dividend_adjusted_price import DividendAdjustedPrice  # noqa: E402
from app.models.index_membership import IndexMembership  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.backtest_service import resolve_universe  # noqa: E402

FIELD = "industry"
LOOKBACKS = [1, 3, 6, 12]
FORWARDS = [1, 3, 6]
MIN_SECTOR_SIZE = 5
START_YM = (2020, 1)


def shift_month(y: int, m: int, offset: int) -> tuple[int, int]:
    total = y * 12 + (m - 1) + offset
    return total // 12, total % 12 + 1


def month_bounds(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def spearman_corr(a: list[float], b: list[float]) -> float:
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    return float(np.corrcoef(ra, rb)[0, 1])


db = SessionLocal()

today = date.today()
last_complete_y, last_complete_m = shift_month(today.year, today.month, -1)
end_y, end_m = shift_month(last_complete_y, last_complete_m, -max(FORWARDS))
rebal_months = []
y, m = START_YM
while (y, m) <= (end_y, end_m):
    rebal_months.append((y, m))
    y, m = shift_month(y, m, 1)
print(f"리밸런싱 시점 수: {len(rebal_months)}")

superset_ids = {
    r[0]
    for r in db.query(IndexMembership.instrument_id)
    .filter(IndexMembership.index_name.in_(["KOSDAQ150", "KOSPI200"]))
    .distinct()
    .all()
}
instruments = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(superset_ids)).all()}

cache_end_date = month_bounds(last_complete_y, last_complete_m)[1]
price_rows = (
    db.query(DividendAdjustedPrice.instrument_id, DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close)
    .filter(
        DividendAdjustedPrice.period == "M",
        DividendAdjustedPrice.instrument_id.in_(superset_ids),
        DividendAdjustedPrice.date <= cache_end_date,
    )
    .all()
)
price_cache: dict[tuple[int, int, int], float] = {}
for iid, d, adj_close in price_rows:
    price_cache[(iid, d.year, d.month)] = float(adj_close)


def momentum(iid: int, y: int, m: int, lookback: int, skip: int) -> float | None:
    ey, em = shift_month(y, m, -skip)
    sy, sm = shift_month(y, m, -(skip + lookback))
    ev = price_cache.get((iid, ey, em))
    sv = price_cache.get((iid, sy, sm))
    if ev is None or sv is None or not sv:
        return None
    return ev / sv - 1


def forward_return(iid: int, y: int, m: int, forward: int) -> float | None:
    sv = price_cache.get((iid, y, m))
    ey, em = shift_month(y, m, forward)
    ev = price_cache.get((iid, ey, em))
    if ev is None or sv is None or not sv:
        return None
    return ev / sv - 1


# signal별 IC 시계열: (lb, fw, signal) -> [(date, ic, n), ...]
SIGNALS = ["raw", "industry_avg", "residual"]
ic_results: dict[tuple[int, int, str], list[tuple[date, float, int]]] = {
    (lb, fw, s): [] for lb in LOOKBACKS for fw in FORWARDS for s in SIGNALS
}
# Fama-MacBeth 이변량 회귀 계수: (lb, fw) -> [(date, coef_ind, coef_resid, n), ...]
fm_results: dict[tuple[int, int], list[tuple[date, float, float, int]]] = {
    (lb, fw): [] for lb in LOOKBACKS for fw in FORWARDS
}

for y, m in rebal_months:
    as_of_date = month_bounds(y, m)[1]
    universe = list(
        (set(resolve_universe(db, "KOSDAQ150", as_of_date)) | set(resolve_universe(db, "KOSPI200", as_of_date)))
        & superset_ids
    )
    if not universe:
        continue

    groups: dict[str, list[int]] = {}
    for iid in universe:
        val = getattr(instruments[iid], FIELD)
        if val is None:
            continue
        groups.setdefault(val, []).append(iid)
    groups = {s: mem for s, mem in groups.items() if len(mem) >= MIN_SECTOR_SIZE}
    stock_industry = {iid: s for s, mem in groups.items() for iid in mem}

    for lb in LOOKBACKS:
        skip = 1 if lb == 12 else 0
        raw_mom = {iid: momentum(iid, y, m, lb, skip) for iid in stock_industry}
        ind_avg = {}
        for s, mem in groups.items():
            vals = [raw_mom[iid] for iid in mem if raw_mom[iid] is not None]
            if vals:
                ind_avg[s] = statistics.mean(vals)

        for fw in FORWARDS:
            fwd = {iid: forward_return(iid, y, m, fw) for iid in stock_industry}

            rows = []
            for iid, ind in stock_industry.items():
                if raw_mom[iid] is None or ind not in ind_avg or fwd[iid] is None:
                    continue
                rows.append(dict(iid=iid, raw=raw_mom[iid], ind_avg=ind_avg[ind], fwd=fwd[iid]))
            if len(rows) < 30:
                continue
            df = pd.DataFrame(rows)
            df["residual"] = df["raw"] - df["ind_avg"]

            for sig_name, col in [("raw", "raw"), ("industry_avg", "ind_avg"), ("residual", "residual")]:
                ic = spearman_corr(df[col].tolist(), df["fwd"].tolist())
                if not pd.isna(ic):
                    ic_results[(lb, fw, sig_name)].append((as_of_date, ic, len(df)))

            # Fama-MacBeth: 표준화 후 OLS
            x_ind = (df["ind_avg"] - df["ind_avg"].mean()) / df["ind_avg"].std()
            x_res = (df["residual"] - df["residual"].mean()) / df["residual"].std()
            y_fwd = df["fwd"].values
            X = np.column_stack([np.ones(len(df)), x_ind.values, x_res.values])
            try:
                coefs, *_ = np.linalg.lstsq(X, y_fwd, rcond=None)
                fm_results[(lb, fw)].append((as_of_date, coefs[1], coefs[2], len(df)))
            except np.linalg.LinAlgError:
                pass

print("계산 완료.\n")

print("=== 1) 단변량 IC: raw vs industry_avg vs residual (종목단위 cross-section) ===")
summary_rows = []
for (lb, fw, sig), series in ic_results.items():
    if len(series) < 3:
        continue
    ics = [r[1] for r in series]
    n = len(ics)
    mean_ic = statistics.mean(ics)
    std_ic = statistics.stdev(ics) if n > 1 else 0.0
    tstat = mean_ic / (std_ic / n**0.5) if std_ic else float("nan")
    pos_pct = sum(1 for x in ics if x > 0) / n
    avg_n_stocks = statistics.mean(r[2] for r in series)
    summary_rows.append(dict(lookback=lb, forward=fw, signal=sig, n_periods=n,
                              mean_ic=mean_ic, ic_tstat=tstat, ic_pos_pct=pos_pct, avg_n_stocks=avg_n_stocks))

ic_df = pd.DataFrame(summary_rows).sort_values(["forward", "lookback", "signal"])
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 160)
print(ic_df.to_string(index=False))

out_dir = REPO_DIR / "reference"
ic_df.to_csv(out_dir / "산업모멘텀분해_IC_비교.csv", index=False, encoding="utf-8-sig")

print("\n=== 2) Fama-MacBeth 이변량회귀: forward ~ industry_avg(z) + residual(z), 계수 평균/t-stat ===")
fm_rows = []
for (lb, fw), series in fm_results.items():
    if len(series) < 3:
        continue
    coef_ind = [r[1] for r in series]
    coef_res = [r[2] for r in series]
    n = len(series)

    def stat(vals):
        mv = statistics.mean(vals)
        sv = statistics.stdev(vals) if n > 1 else 0.0
        t = mv / (sv / n**0.5) if sv else float("nan")
        return mv, t

    mv_ind, t_ind = stat(coef_ind)
    mv_res, t_res = stat(coef_res)
    fm_rows.append(dict(lookback=lb, forward=fw, n_periods=n,
                         coef_industry_avg=mv_ind, t_industry_avg=t_ind,
                         coef_residual=mv_res, t_residual=t_res))

fm_df = pd.DataFrame(fm_rows).sort_values(["forward", "lookback"])
print(fm_df.to_string(index=False))
fm_df.to_csv(out_dir / "산업모멘텀분해_FamaMacBeth.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {out_dir}/산업모멘텀분해_IC_비교.csv, 산업모멘텀분해_FamaMacBeth.csv")

db.close()
