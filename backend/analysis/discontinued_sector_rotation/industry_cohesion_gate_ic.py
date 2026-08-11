"""'응집도 게이트(cohesion gate)' 가설 검증: industry 필드를 결속력(내부 페어와이즈
상관계수) 기준으로 상/하위로 나눠서, 결속력 높은 산업군에서만 섹터모멘텀 IC/스프레드가
더 강하게 나타나는지 확인한다.

sector_internal_correlation.py에서 계산한 industry별 응집도(reference/섹터내상관성_industry_섹터별응집도.csv)
중 표본이 충분한(페어 15개 이상, 대략 종목 6개 이상) 산업만 골라 중앙값 기준으로
cohesive(상위) / incoherent(하위) 두 그룹으로 나눈다. 정적(전체기간) 응집도로 나눈
1차 검증이라 실제 point-in-time 배포에는 롤링 응집도로 다시 검증해야 함(look-ahead 주의).

sector_rs_persistence_ic.py와 동일한 IC/스프레드 계산을 pooled(전체 industry) /
cohesive만 / incoherent만 세 가지로 나눠 비교한다.
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
MIN_SECTORS_FOR_IC = 5
MIN_PAIRS_FOR_COHESION = 15
START_YM = (2020, 1)

COHESION_CSV = REPO_DIR / "reference" / "섹터내상관성_industry_섹터별응집도.csv"


def shift_month(y: int, m: int, offset: int) -> tuple[int, int]:
    total = y * 12 + (m - 1) + offset
    return total // 12, total % 12 + 1


def month_bounds(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def spearman_corr(a: list[float], b: list[float]) -> float:
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    return float(np.corrcoef(ra, rb)[0, 1])


cohesion_df = pd.read_csv(COHESION_CSV)
cohesion_df = cohesion_df[cohesion_df["n_pairs"] >= MIN_PAIRS_FOR_COHESION].sort_values("mean_corr", ascending=False)
median_corr = cohesion_df["mean_corr"].median()
cohesive_industries = set(cohesion_df[cohesion_df["mean_corr"] >= median_corr]["sector"])
incoherent_industries = set(cohesion_df[cohesion_df["mean_corr"] < median_corr]["sector"])
print(f"응집도 표본(페어>={MIN_PAIRS_FOR_COHESION}): {len(cohesion_df)}개 industry, 중앙값 상관 {median_corr:.3f}")
print(f"cohesive({len(cohesive_industries)}): {sorted(cohesive_industries)}")
print(f"incoherent({len(incoherent_industries)}): {sorted(incoherent_industries)}")

db = SessionLocal()

today = date.today()
last_complete_y, last_complete_m = shift_month(today.year, today.month, -1)
end_y, end_m = shift_month(last_complete_y, last_complete_m, -max(FORWARDS))
rebal_months = []
y, m = START_YM
while (y, m) <= (end_y, end_m):
    rebal_months.append((y, m))
    y, m = shift_month(y, m, 1)
print(f"\n리밸런싱 시점 수: {len(rebal_months)}")

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


def mean_or_none(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return statistics.mean(vals) if vals else None


SUBSETS = {"pooled": None, "cohesive": cohesive_industries, "incoherent": incoherent_industries}

results: dict[tuple[str, int, int], list[tuple[date, float, float, int]]] = {
    (subset, lb, fw): [] for subset in SUBSETS for lb in LOOKBACKS for fw in FORWARDS
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

    momentum_by_lb = {lb: {iid: momentum(iid, y, m, lb, 1 if lb == 12 else 0) for iid in universe} for lb in LOOKBACKS}
    forward_by_fw = {fw: {iid: forward_return(iid, y, m, fw) for iid in universe} for fw in FORWARDS}

    for subset_name, allowed in SUBSETS.items():
        subset_groups = groups if allowed is None else {s: mem for s, mem in groups.items() if s in allowed}
        if len(subset_groups) < MIN_SECTORS_FOR_IC:
            continue
        for lb in LOOKBACKS:
            mom_map = momentum_by_lb[lb]
            sector_mom = {s: mean_or_none([mom_map[i] for i in mem]) for s, mem in subset_groups.items()}
            sector_mom = {s: v for s, v in sector_mom.items() if v is not None}
            for fw in FORWARDS:
                fwd_map = forward_by_fw[fw]
                sector_fwd = {s: mean_or_none([fwd_map[i] for i in subset_groups[s]]) for s in subset_groups}
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

                results[(subset_name, lb, fw)].append((as_of_date, ic, spread, len(common)))

print("\n계산 완료.\n")

summary_rows = []
for (subset_name, lb, fw), series in results.items():
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
    avg_n = statistics.mean(r[3] for r in series)

    summary_rows.append(
        dict(subset=subset_name, lookback=lb, forward=fw, n_periods=n,
             mean_ic=mean_ic, ic_tstat=tstat_ic, ic_pos_pct=pct_pos,
             mean_spread_pct=mean_spread * 100, spread_tstat=tstat_spread, avg_n_industries=avg_n)
    )

summary_df = pd.DataFrame(summary_rows).sort_values(["forward", "lookback", "subset"])
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
pd.set_option("display.width", 160)
print(summary_df.to_string(index=False))

out_dir = REPO_DIR / "reference"
summary_df.to_csv(out_dir / "산업응집도게이트_IC_스프레드_비교.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {out_dir}/산업응집도게이트_IC_스프레드_비교.csv")

print("\n--- pooled 대비 cohesive/incoherent 개선폭 (ic_tstat 기준) ---")
pivot = summary_df.pivot_table(index=["lookback", "forward"], columns="subset", values="ic_tstat")
pivot["cohesive_minus_pooled"] = pivot.get("cohesive") - pivot.get("pooled")
pivot["incoherent_minus_pooled"] = pivot.get("incoherent") - pivot.get("pooled")
print(pivot.sort_values("cohesive_minus_pooled", ascending=False).to_string())

db.close()
