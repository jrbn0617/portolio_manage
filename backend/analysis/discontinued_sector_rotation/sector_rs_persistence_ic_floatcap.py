"""sector_rs_persistence_ic.py의 유동시총가중 버전.

섹터 평균모멘텀/forward수익률을 동일가중 대신 유동시가총액(raw_close x
shares_outstanding_monthly x free_float_ratio/100) 비례 가중으로 계산한다.
가중치 계산 로직은 backend/app/services/backtest_service.py의
compute_free_float_weights / _free_float_raw_close_on / _latest_monthly_value와
동일한 공식을 재사용(그대로 import).
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
from app.models.monthly_fundamental import MonthlyFundamental  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.backtest_service import resolve_universe  # noqa: E402

FIELDS = ["sector", "industry", "krx_sector"]
LOOKBACKS = [1, 3, 6, 12]
FORWARDS = [1, 3, 6]
MIN_SECTOR_SIZE = 5
MIN_SECTORS_FOR_IC = 5
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
print(f"분석 구간: {START_YM[0]}-{START_YM[1]:02d} ~ {end_y}-{end_m:02d}")

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
print(f"superset 종목 수: {len(superset_ids)}")

instruments = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(superset_ids)).all()}

# 각 월의 실제 마지막 거래일(가중치 계산용 raw_close는 정확한 거래일 매칭이 필요)
as_of_by_ym: dict[tuple[int, int], date] = {}
for y, m in rebal_months:
    start, end = month_bounds(y, m)
    d = db.query(Price.date).filter(Price.period == "D", Price.date.between(start, end)).order_by(Price.date.desc()).first()
    if d is None:
        raise RuntimeError(f"{y}-{m:02d}: prices(D) 없음")
    as_of_by_ym[(y, m)] = d[0]

# 월말 배당조정가(모멘텀/forward 계산용, price_cache와 동일 패턴)
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
print(f"월말 배당조정가 캐시: {len(price_cache)}건")

# raw_close (유동시총 계산용, 실제 거래일 exact match)
as_of_dates = sorted(set(as_of_by_ym.values()))
raw_close_rows = (
    db.query(Price.instrument_id, Price.date, Price.raw_close)
    .filter(Price.period == "D", Price.instrument_id.in_(superset_ids), Price.date.in_(as_of_dates))
    .all()
)
raw_close_cache: dict[tuple[int, date], float] = {
    (iid, d): float(rc) for iid, d, rc in raw_close_rows if rc is not None
}
print(f"raw_close 캐시(가중치용): {len(raw_close_cache)}건")

# 월간 펀더멘털(유동비율/상장주식수) — "as_of 이하 최신값" 조회를 위해 정렬된 시계열로 preload
fund_rows = (
    db.query(MonthlyFundamental.instrument_id, MonthlyFundamental.metric, MonthlyFundamental.date, MonthlyFundamental.value)
    .filter(
        MonthlyFundamental.metric.in_(["free_float_ratio", "shares_outstanding_monthly"]),
        MonthlyFundamental.instrument_id.in_(superset_ids),
    )
    .order_by(MonthlyFundamental.instrument_id, MonthlyFundamental.metric, MonthlyFundamental.date)
    .all()
)
fund_series: dict[tuple[int, str], tuple[list[date], list[float]]] = {}
for iid, metric, d, v in fund_rows:
    key = (iid, metric)
    if key not in fund_series:
        fund_series[key] = ([], [])
    fund_series[key][0].append(d)
    fund_series[key][1].append(float(v))
print(f"월간 펀더멘털 시계열: {len(fund_series)}개 (종목x지표)")

import bisect  # noqa: E402


def latest_fundamental(iid: int, metric: str, as_of: date) -> float | None:
    key = (iid, metric)
    series = fund_series.get(key)
    if series is None:
        return None
    dates, values = series
    idx = bisect.bisect_right(dates, as_of) - 1
    return values[idx] if idx >= 0 else None


def float_cap(iid: int, as_of_date: date) -> float | None:
    raw_close = raw_close_cache.get((iid, as_of_date))
    shares = latest_fundamental(iid, "shares_outstanding_monthly", as_of_date)
    ratio = latest_fundamental(iid, "free_float_ratio", as_of_date)
    if raw_close is None or shares is None or ratio is None or ratio <= 0:
        return None
    return raw_close * shares * (ratio / 100.0)


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


def weighted_mean_or_none(pairs: list[tuple[float, float]]) -> float | None:
    """pairs: [(value, weight), ...] — weight<=0 이거나 value가 None인 건 이미 걸러졌다고 가정."""
    total_w = sum(w for _, w in pairs)
    if not pairs or total_w <= 0:
        return None
    return sum(v * w for v, w in pairs) / total_w


results: dict[tuple[str, int, int], list[tuple[date, float, float, int]]] = {
    (f, lb, fw): [] for f in FIELDS for lb in LOOKBACKS for fw in FORWARDS
}

missing_weight_count = 0
total_member_count = 0

for y, m in rebal_months:
    as_of_date = as_of_by_ym[(y, m)]
    universe = list(
        (set(resolve_universe(db, "KOSDAQ150", as_of_date)) | set(resolve_universe(db, "KOSPI200", as_of_date)))
        & superset_ids
    )
    if not universe:
        continue

    weights = {}
    for iid in universe:
        total_member_count += 1
        w = float_cap(iid, as_of_date)
        if w is None:
            missing_weight_count += 1
        weights[iid] = w

    groups_by_field: dict[str, dict[str, list[int]]] = {}
    for field in FIELDS:
        groups: dict[str, list[int]] = {}
        for iid in universe:
            val = getattr(instruments[iid], field)
            if val is None:
                continue
            groups.setdefault(val, []).append(iid)
        groups_by_field[field] = {s: members for s, members in groups.items() if len(members) >= MIN_SECTOR_SIZE}

    momentum_by_lb = {lb: {iid: momentum(iid, y, m, lb, 1 if lb == 12 else 0) for iid in universe} for lb in LOOKBACKS}
    forward_by_fw = {fw: {iid: forward_return(iid, y, m, fw) for iid in universe} for fw in FORWARDS}

    for field, groups in groups_by_field.items():
        if len(groups) < MIN_SECTORS_FOR_IC:
            continue
        for lb in LOOKBACKS:
            mom_map = momentum_by_lb[lb]
            sector_mom = {}
            for s, members in groups.items():
                pairs = [(mom_map[i], weights[i]) for i in members if mom_map[i] is not None and weights[i] is not None]
                v = weighted_mean_or_none(pairs)
                if v is not None:
                    sector_mom[s] = v
            for fw in FORWARDS:
                fwd_map = forward_by_fw[fw]
                sector_fwd = {}
                for s, members in groups.items():
                    pairs = [(fwd_map[i], weights[i]) for i in members if fwd_map[i] is not None and weights[i] is not None]
                    v = weighted_mean_or_none(pairs)
                    if v is not None:
                        sector_fwd[s] = v

                common = sorted(set(sector_mom) & set(sector_fwd))
                if len(common) < MIN_SECTORS_FOR_IC:
                    continue

                ic = spearman_corr([sector_mom[s] for s in common], [sector_fwd[s] for s in common])
                if pd.isna(ic):
                    continue

                ranked = sorted(common, key=lambda s: sector_mom[s], reverse=True)
                k = max(1, round(len(ranked) * 0.2))
                top_ret = statistics.mean(sector_fwd[s] for s in ranked[:k])
                bottom_ret = statistics.mean(sector_fwd[s] for s in ranked[-k:])
                spread = top_ret - bottom_ret

                results[(field, lb, fw)].append((as_of_date, ic, spread, len(common)))

print(f"\n가중치 결측: {missing_weight_count}/{total_member_count}종목-시점 ({missing_weight_count/total_member_count:.1%})")
print("계산 완료. 요약 통계 산출 중...")

summary_rows = []
for (field, lb, fw), series in results.items():
    if len(series) < 3:
        continue
    ics = [r[1] for r in series]
    spreads = [r[2] for r in series]
    n = len(ics)
    mean_ic = statistics.mean(ics)
    std_ic = statistics.stdev(ics) if n > 1 else 0.0
    tstat_ic = mean_ic / (std_ic / n**0.5) if std_ic else float("nan")
    pct_pos = sum(1 for x in ics if x > 0) / n
    mean_spread = statistics.mean(spreads)
    std_spread = statistics.stdev(spreads) if n > 1 else 0.0
    tstat_spread = mean_spread / (std_spread / n**0.5) if std_spread else float("nan")
    avg_n_sectors = statistics.mean(r[3] for r in series)

    summary_rows.append(
        dict(
            field=field, lookback=lb, forward=fw, n_periods=n,
            mean_ic=mean_ic, ic_tstat=tstat_ic, ic_pos_pct=pct_pos,
            mean_spread_pct=mean_spread * 100, spread_tstat=tstat_spread,
            avg_n_sectors=avg_n_sectors,
        )
    )

summary_df = pd.DataFrame(summary_rows).sort_values("ic_tstat", ascending=False)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 160)
print(summary_df.to_string(index=False))

out_dir = REPO_DIR / "reference"
out_path = out_dir / "섹터상대강도_IC_스프레드_요약_유동시총가중.csv"
summary_df.to_csv(out_path, index=False, encoding="utf-8-sig")
print(f"\n저장: {out_path}")

db.close()
