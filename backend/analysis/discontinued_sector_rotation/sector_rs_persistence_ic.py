"""섹터 상대강도(동일가중 평균모멘텀)의 다음기간 예측력(IC)/분위수 스프레드 검증.

유니버스: KOSDAQ150 + KOSPI200 합집합(리밸런싱 시점마다 실제 편입종목)
분류필드: sector / industry / krx_sector 3종 병행
lookback: 1/3/6/12개월(12개월만 skip_months=1), forward: 1/3/6개월
"""
import calendar
import statistics
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


def spearman_corr(a: list[float], b: list[float]) -> float:
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    return float(np.corrcoef(ra, rb)[0, 1])

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.dividend_adjusted_price import DividendAdjustedPrice  # noqa: E402
from app.models.index_membership import IndexMembership  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
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


def month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


db = SessionLocal()

today = date.today()
last_complete_y, last_complete_m = shift_month(today.year, today.month, -1)
end_y, end_m = shift_month(last_complete_y, last_complete_m, -max(FORWARDS))
print(f"분석 구간: {START_YM[0]}-{START_YM[1]:02d} ~ {end_y}-{end_m:02d} (forward 최대 {max(FORWARDS)}개월 실현 보장)")

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
print(f"전체기간 편입 이력 있는 종목 수(superset): {len(superset_ids)}")

instruments = {
    i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(superset_ids)).all()
}

cache_end_date = month_end(last_complete_y, last_complete_m)
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


def mean_or_none(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return statistics.mean(vals) if vals else None


results: dict[tuple[str, int, int], list[tuple[date, float, float, int]]] = {
    (f, lb, fw): [] for f in FIELDS for lb in LOOKBACKS for fw in FORWARDS
}

for y, m in rebal_months:
    as_of_date = month_end(y, m)
    universe = list(
        (set(resolve_universe(db, "KOSDAQ150", as_of_date)) | set(resolve_universe(db, "KOSPI200", as_of_date)))
        & superset_ids
    )
    if not universe:
        continue

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
            sector_mom = {s: mean_or_none([mom_map[i] for i in members]) for s, members in groups.items()}
            sector_mom = {s: v for s, v in sector_mom.items() if v is not None}
            for fw in FORWARDS:
                fwd_map = forward_by_fw[fw]
                sector_fwd = {s: mean_or_none([fwd_map[i] for i in groups[s]]) for s in groups}
                sector_fwd = {s: v for s, v in sector_fwd.items() if v is not None}

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

print("\n계산 완료. 요약 통계 산출 중...")

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
            field=field,
            lookback=lb,
            forward=fw,
            n_periods=n,
            mean_ic=mean_ic,
            ic_tstat=tstat_ic,
            ic_pos_pct=pct_pos,
            mean_spread_pct=mean_spread * 100,
            spread_tstat=tstat_spread,
            avg_n_sectors=avg_n_sectors,
        )
    )

summary_df = pd.DataFrame(summary_rows).sort_values("ic_tstat", ascending=False)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 160)
print(summary_df.to_string(index=False))

out_dir = REPO_DIR / "reference"
out_path = out_dir / "섹터상대강도_IC_스프레드_요약.csv"
summary_df.to_csv(out_path, index=False, encoding="utf-8-sig")
print(f"\n저장: {out_path}")

detail_rows = []
for (field, lb, fw), series in results.items():
    for d, ic, spread, n_sectors in series:
        detail_rows.append(dict(field=field, lookback=lb, forward=fw, date=d, ic=ic, spread=spread, n_sectors=n_sectors))
detail_df = pd.DataFrame(detail_rows)
detail_path = out_dir / "섹터상대강도_IC_스프레드_시계열.csv"
detail_df.to_csv(detail_path, index=False, encoding="utf-8-sig")
print(f"저장: {detail_path}")

db.close()
