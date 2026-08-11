"""6개월 lookback 기준, 산업평균모멘텀(industry_avg)과 잔차모멘텀(residual)을
FM(Fama-MacBeth) 회귀계수 비율로 결합한 복합스코어가 raw모멘텀/각 성분 단독보다
forward 수익률을 더 잘 예측하는지 확인한다.

방법: 전체기간(2020-01~) 산업모멘텀분해_FamaMacBeth.csv에서 이미 구한 lb=6 평균계수를
"고정 가중치"로 삼아(매달 다시 적합하지 않음 — 그러면 그 달만 봐도 항상 이겨서 의미가
없어짐) composite = w_ind * z(industry_avg) + w_res * z(residual) 를 매달 계산하고,
그 composite의 단변량 IC/분위수 스프레드를 raw/industry_avg/residual과 나란히 비교한다.

주의(방법론적 한계): 가중치 자체가 검증에 쓰는 것과 같은 전체기간에서 추정된 값이라
완전한 표본외(out-of-sample) 검증은 아니다 — "이 결합비율이 그럴듯한가"를 보는 in-sample
정합성 체크로 이해해야 한다.
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
LOOKBACKS = [3, 6, 12]
FORWARDS = [3, 6]
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


# ---- 월별 (raw, ind_avg, residual, fwd) 데이터프레임 미리 구성 (lookback x forward별) ----
monthly_frames: dict[tuple[int, int], list[tuple[date, pd.DataFrame]]] = {
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
        ind_avg_by_sector = {}
        for s, mem in groups.items():
            vals = [raw_mom[iid] for iid in mem if raw_mom[iid] is not None]
            if vals:
                ind_avg_by_sector[s] = statistics.mean(vals)

        for fw in FORWARDS:
            fwd = {iid: forward_return(iid, y, m, fw) for iid in stock_industry}
            rows = []
            for iid, ind in stock_industry.items():
                if raw_mom[iid] is None or ind not in ind_avg_by_sector or fwd[iid] is None:
                    continue
                rows.append(dict(iid=iid, raw=raw_mom[iid], ind_avg=ind_avg_by_sector[ind], fwd=fwd[iid]))
            if len(rows) < 30:
                continue
            df = pd.DataFrame(rows)
            df["residual"] = df["raw"] - df["ind_avg"]
            monthly_frames[(lb, fw)].append((as_of_date, df))

# ---- 1단계: FM 회귀계수(전체기간 평균)를 고정 결합비율로 산출 ----
print("\n=== lookback x forward별 FM 회귀계수(결합비율 산출용) ===")
fixed_weights: dict[tuple[int, int], tuple[float, float]] = {}
for lb in LOOKBACKS:
    for fw in FORWARDS:
        coefs_ind, coefs_res = [], []
        for d, df in monthly_frames[(lb, fw)]:
            x_ind = (df["ind_avg"] - df["ind_avg"].mean()) / df["ind_avg"].std()
            x_res = (df["residual"] - df["residual"].mean()) / df["residual"].std()
            X = np.column_stack([np.ones(len(df)), x_ind.values, x_res.values])
            try:
                coefs, *_ = np.linalg.lstsq(X, df["fwd"].values, rcond=None)
                coefs_ind.append(coefs[1])
                coefs_res.append(coefs[2])
            except np.linalg.LinAlgError:
                pass
        w_ind, w_res = statistics.mean(coefs_ind), statistics.mean(coefs_res)
        fixed_weights[(lb, fw)] = (w_ind, w_res)
        print(f"  lookback={lb}개월, forward={fw}개월: w_industry_avg={w_ind:.4f}, w_residual={w_res:.4f} (비율 {w_ind/(w_ind+w_res):.2f} : {w_res/(w_ind+w_res):.2f})")

# ---- 2단계: 고정가중치로 composite 계산 후, raw/industry_avg/residual/composite IC·스프레드 비교 ----
print("\n=== 신호별 IC / 분위수 스프레드 비교 ===")
summary = []
for lb in LOOKBACKS:
    for fw in FORWARDS:
        w_ind, w_res = fixed_weights[(lb, fw)]
        signal_series = {"raw": [], "industry_avg": [], "residual": [], "composite": []}
        spread_series = {"raw": [], "industry_avg": [], "residual": [], "composite": []}

        for d, df in monthly_frames[(lb, fw)]:
            z_ind = (df["ind_avg"] - df["ind_avg"].mean()) / df["ind_avg"].std()
            z_res = (df["residual"] - df["residual"].mean()) / df["residual"].std()
            composite = w_ind * z_ind + w_res * z_res

            for name, series in [("raw", df["raw"]), ("industry_avg", df["ind_avg"]), ("residual", df["residual"]), ("composite", composite)]:
                ic = spearman_corr(series.tolist(), df["fwd"].tolist())
                if not pd.isna(ic):
                    signal_series[name].append(ic)

                k = max(1, round(len(df) * 0.2))
                order = series.sort_values(ascending=False).index
                top = df.loc[order[:k], "fwd"].mean()
                bottom = df.loc[order[-k:], "fwd"].mean()
                spread_series[name].append(top - bottom)

        for name in signal_series:
            ics = signal_series[name]
            spreads = spread_series[name]
            n = len(ics)
            mean_ic = statistics.mean(ics)
            std_ic = statistics.stdev(ics) if n > 1 else 0.0
            tstat = mean_ic / (std_ic / n**0.5) if std_ic else float("nan")
            pos_pct = sum(1 for x in ics if x > 0) / n
            mean_spread = statistics.mean(spreads)
            std_spread = statistics.stdev(spreads) if n > 1 else 0.0
            tstat_spread = mean_spread / (std_spread / n**0.5) if std_spread else float("nan")
            summary.append(dict(lookback=lb, forward=fw, signal=name, n_periods=n, mean_ic=mean_ic, ic_tstat=tstat,
                                 ic_pos_pct=pos_pct, mean_spread_pct=mean_spread * 100, spread_tstat=tstat_spread))

summary_df = pd.DataFrame(summary)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 160)
print(summary_df.to_string(index=False))

out_dir = REPO_DIR / "reference"
summary_df.to_csv(out_dir / "산업복합스코어_검증.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {out_dir}/산업복합스코어_검증.csv")

db.close()
