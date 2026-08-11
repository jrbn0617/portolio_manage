""""섹터 베팅"이 성립하려면 같은 섹터 종목들이 실제로 같이 움직여야 한다는 전제를
두 각도로 검증한다 (동일가중 기준, sector/industry/krx_sector 3필드 병행).

A) 구조적 co-movement: 종목별 월간수익률 시계열의 페어와이즈 상관계수를 구해서
   "같은 섹터 페어" vs "다른 섹터 페어" 평균 상관계수를 비교.
B) 분산분해(eta^2, 1-way ANOVA 방식): 매 리밸런싱 시점 forward 수익률의 전체
   분산 중 "어느 섹터에 속했는가"로 설명되는 비중(between-group SS / total SS).
   섹터상대강도 IC 분석에서 가장 신호가 좋았던 forward=1개월/6개월 두 horizon으로.
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

FIELDS = ["sector", "industry", "krx_sector"]
MIN_SECTOR_SIZE = 5
MIN_SECTORS_FOR_ETA = 5
MIN_OVERLAP_MONTHS = 24
FORWARD_HORIZONS = [1, 6]
START_YM = (2020, 1)


def shift_month(y: int, m: int, offset: int) -> tuple[int, int]:
    total = y * 12 + (m - 1) + offset
    return total // 12, total % 12 + 1


def month_bounds(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


db = SessionLocal()

today = date.today()
last_complete_y, last_complete_m = shift_month(today.year, today.month, -1)
end_y, end_m = shift_month(last_complete_y, last_complete_m, -max(FORWARD_HORIZONS))
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
instruments = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(superset_ids)).all()}
print(f"superset 종목 수: {len(superset_ids)}")

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


def forward_return(iid: int, y: int, m: int, forward: int) -> float | None:
    sv = price_cache.get((iid, y, m))
    ey, em = shift_month(y, m, forward)
    ev = price_cache.get((iid, ey, em))
    if ev is None or sv is None or not sv:
        return None
    return ev / sv - 1


# ---------- A) 페어와이즈 상관계수: 같은 섹터 vs 다른 섹터 ----------
print("\n=== A) 월간수익률 페어와이즈 상관계수 ===")

month_list = sorted({(y, m) for (_, y, m) in price_cache})
ids_sorted_cols = sorted(superset_ids)
price_matrix = pd.DataFrame(index=[f"{y}-{m:02d}" for y, m in month_list], columns=ids_sorted_cols, dtype=float)
for (iid, y, m), v in price_cache.items():
    price_matrix.at[f"{y}-{m:02d}", iid] = v
ret_matrix = price_matrix.pct_change()

corr_matrix = ret_matrix.corr(min_periods=MIN_OVERLAP_MONTHS)
print(f"상관계수 행렬 크기: {corr_matrix.shape}, 최소 겹치는 개월수 요건: {MIN_OVERLAP_MONTHS}")

ids_sorted = corr_matrix.columns.tolist()
corr_np = corr_matrix.values
n_ids = len(ids_sorted)
iu = np.triu_indices(n_ids, k=1)
c_vals = corr_np[iu]
valid_corr_mask = ~np.isnan(c_vals)

pairwise_summary = []
per_sector_cohesion: dict[str, list] = {}
for field in FIELDS:
    field_arr = np.array([getattr(instruments[iid], field) for iid in ids_sorted], dtype=object)
    left, right = field_arr[iu[0]], field_arr[iu[1]]
    valid_field_mask = (left != None) & (right != None)  # noqa: E711
    same = (left == right) & valid_field_mask
    mask = valid_corr_mask & valid_field_mask

    within_vals = c_vals[mask & same]
    between_vals = c_vals[mask & ~same]

    mean_w, mean_b = float(np.mean(within_vals)), float(np.mean(between_vals))
    sd_w, sd_b = float(np.std(within_vals, ddof=1)), float(np.std(between_vals, ddof=1))
    n_w, n_b = len(within_vals), len(between_vals)
    se = (sd_w**2 / n_w + sd_b**2 / n_b) ** 0.5
    tstat = (mean_w - mean_b) / se if se else float("nan")
    pairwise_summary.append(
        dict(field=field, n_within_pairs=n_w, n_between_pairs=n_b,
             mean_within_corr=mean_w, mean_between_corr=mean_b, diff=mean_w - mean_b, tstat=tstat)
    )

    same_idx = np.where(mask & same)[0]
    sector_pairs: dict[str, list[float]] = {}
    for k in same_idx:
        sector_pairs.setdefault(left[k], []).append(c_vals[k])
    per_sector_cohesion[field] = sorted(
        [(s, statistics.mean(vs), len(vs)) for s, vs in sector_pairs.items() if len(vs) >= 3],
        key=lambda x: -x[1],
    )

pairwise_df = pd.DataFrame(pairwise_summary)
pd.set_option("display.float_format", lambda x: f"{x:.4f}")
pd.set_option("display.width", 160)
print(pairwise_df.to_string(index=False))

print("\n--- sector 필드: 섹터별 내부 응집도(평균 페어 상관) 상위/하위 5개 ---")
sc = per_sector_cohesion["sector"]
for s, mc, n in sc[:5]:
    print(f"  {s}: 평균상관 {mc:.3f} (페어 {n}개)")
print("  ...")
for s, mc, n in sc[-5:]:
    print(f"  {s}: 평균상관 {mc:.3f} (페어 {n}개)")

out_dir = REPO_DIR / "reference"
pairwise_df.to_csv(out_dir / "섹터내상관성_페어와이즈_요약.csv", index=False, encoding="utf-8-sig")
for field in FIELDS:
    rows = [dict(field=field, sector=s, mean_corr=mc, n_pairs=n) for s, mc, n in per_sector_cohesion[field]]
    pd.DataFrame(rows).to_csv(out_dir / f"섹터내상관성_{field}_섹터별응집도.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {out_dir}/섹터내상관성_페어와이즈_요약.csv, 섹터내상관성_<field>_섹터별응집도.csv")


# ---------- B) 분산분해 (eta^2) ----------
print("\n\n=== B) forward 수익률 분산분해 (eta^2 = between-sector SS / total SS) ===")

eta_results: dict[tuple[str, int], list[tuple[date, float, int]]] = {(f, h): [] for f in FIELDS for h in FORWARD_HORIZONS}

for y, m in rebal_months:
    as_of_date = month_bounds(y, m)[1]
    universe = list(
        (set(resolve_universe(db, "KOSDAQ150", as_of_date)) | set(resolve_universe(db, "KOSPI200", as_of_date)))
        & superset_ids
    )
    if not universe:
        continue

    for horizon in FORWARD_HORIZONS:
        fwd = {iid: forward_return(iid, y, m, horizon) for iid in universe}
        for field in FIELDS:
            groups: dict[str, list[int]] = {}
            for iid in universe:
                val = getattr(instruments[iid], field)
                if val is None or fwd[iid] is None:
                    continue
                groups.setdefault(val, []).append(iid)
            groups = {s: mem for s, mem in groups.items() if len(mem) >= MIN_SECTOR_SIZE}
            if len(groups) < MIN_SECTORS_FOR_ETA:
                continue

            all_vals = [fwd[iid] for mem in groups.values() for iid in mem]
            grand_mean = statistics.mean(all_vals)
            ss_total = sum((v - grand_mean) ** 2 for v in all_vals)
            if ss_total <= 0:
                continue
            ss_between = sum(
                len(mem) * (statistics.mean(fwd[iid] for iid in mem) - grand_mean) ** 2 for mem in groups.values()
            )
            eta_sq = ss_between / ss_total
            eta_results[(field, horizon)].append((as_of_date, eta_sq, len(all_vals)))

eta_summary = []
for (field, horizon), series in eta_results.items():
    if len(series) < 3:
        continue
    etas = [r[1] for r in series]
    eta_summary.append(
        dict(
            field=field, forward=horizon, n_periods=len(etas),
            mean_eta_sq=statistics.mean(etas), median_eta_sq=statistics.median(etas),
            std_eta_sq=statistics.stdev(etas), avg_n_stocks=statistics.mean(r[2] for r in series),
        )
    )
eta_df = pd.DataFrame(eta_summary).sort_values(["forward", "mean_eta_sq"], ascending=[True, False])
print(eta_df.to_string(index=False))
eta_df.to_csv(out_dir / "섹터내상관성_분산분해_eta2.csv", index=False, encoding="utf-8-sig")
print(f"저장: {out_dir}/섹터내상관성_분산분해_eta2.csv")

db.close()
