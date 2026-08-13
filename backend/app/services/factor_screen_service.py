"""EBITDA PEG(EV/EBITDA(Fwd.12M) ÷ EBITDA 성장률) 팩터 스크리닝.

Factor = EV/EBITDA(Fwd.12M) / [ (EBITDA(Fwd.12M) - EBITDA(TTM)) / ABS(EBITDA(TTM)) ]

분모를 ABS(EBITDA(TTM))로 정규화해 TTM이 적자여도 성장률 부호가 실제 개선/악화
방향과 일치하도록 한다. EBITDA(Fwd.12M) <= 0 이거나(=EV/EBITDA(Fwd) 자체가 무의미)
성장률 <= 0(=EBITDA PEG <= 0)이면 탈락, 컨센서스가 아예 없는 종목도 탈락(보수적).

살아남은 종목 중 섹터별 EBITDA PEG 하위 top_pct(기본 50%, 낮을수록 저평가)만 통과.
섹터 내 유효 표본수가 min_sector_size 미만이면 그 섹터는 전체 유니버스 분포 기준
컷오프로 대체해서 판정한다(작은 섹터에서 "상위 50%"가 무의미해지는 것 방지).

point-in-time: EBITDA(TTM)은 검증 결과 실제 공시일이 아니라 분기말+1개월 시점에
거의 전종목이 동시에 갱신되는 패턴이 확인되어(사후 재구성 의심) 미래참조 위험이
있다. 조회 시 ttm_lag_days(기본 90일, 사업보고서 법정 공시기한 기준 최대 보수값)
만큼 과거로 밀어서 조회한다. EBITDA(Fwd.12M)/EV EBITDA(Fwd.12M) 컨센서스는 종목별로
갱신 시점이 분산되어 있어 point-in-time에 가까워 보이므로 consensus_lag_days
기본값은 0으로 둔다 — 전부 튜닝 가능한 파라미터.
"""
import bisect
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.monthly_fundamental import MonthlyFundamental

Logger = Callable[[str], None]

TTM_METRIC = "ebitda_ttm"
FWD_METRIC = "ebitda_fwd_12m"
MULTIPLE_METRIC = "ev_ebitda_fwd_12m"


@dataclass
class ScreenConfig:
    top_pct: float = 0.5
    min_sector_size: int = 5
    ttm_lag_days: int = 90
    consensus_lag_days: int = 0
    peg_min: float = 0.0  # EBITDA PEG가 이 값보다 커야 통과 (기본 0 = 성장률>0과 동치)
    # 조회 기준일로부터 이 일수보다 오래된 값은 **없는 것으로 본다**(그 종목은 후보에서 탈락).
    # None이면 아무리 오래된 값도 그대로 쓴다 — 2026-08-13 이전의 기존 동작.
    #
    # 왜 필요한가: `_latest_at_or_before`는 "as_of 이하 최근값"만 가져오고 나이를 보지
    # 않는다. 2014~2018 EV/EBITDA를 백필하자 **2019년 이후 데이터가 없는 종목이 10년 묵은
    # 배수로 2020년대 스크리닝을 통과**하기 시작했다(2026-06-30 기준 후보의 62%가 100일
    # 초과, p90 3,623일). 세 지표 모두 월간 시계열이라 정상이면 나이가 0~31일이어야 한다.
    max_age_days: int | None = None


def _build_metric_index(db: Session, instrument_ids: list[int], metric: str) -> dict[int, tuple[list[date], list[float]]]:
    if not instrument_ids:
        return {}
    rows = (
        db.query(MonthlyFundamental.instrument_id, MonthlyFundamental.date, MonthlyFundamental.value)
        .filter(MonthlyFundamental.instrument_id.in_(instrument_ids), MonthlyFundamental.metric == metric)
        .order_by(MonthlyFundamental.instrument_id, MonthlyFundamental.date)
        .all()
    )
    idx: dict[int, tuple[list[date], list[float]]] = {}
    for iid, d, v in rows:
        dates, values = idx.setdefault(iid, ([], []))
        dates.append(d)
        values.append(float(v))
    return idx


def _latest_at_or_before(
    idx: dict[int, tuple[list[date], list[float]]],
    instrument_id: int,
    as_of: date,
    max_age_days: int | None = None,
) -> float | None:
    """as_of 이하 최근값. max_age_days를 주면 그보다 오래된 값은 None으로 취급한다."""
    entry = idx.get(instrument_id)
    if not entry:
        return None
    dates, values = entry
    pos = bisect.bisect_right(dates, as_of) - 1
    if pos < 0:
        return None
    if max_age_days is not None and (as_of - dates[pos]).days > max_age_days:
        return None
    return values[pos]


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return float("inf")
    idx = min(len(sorted_values) - 1, max(0, round(len(sorted_values) * pct) - 1))
    return sorted_values[idx]


def screen_by_ebitda_peg(
    db: Session,
    universe: list[int],
    as_of_date: date,
    config: ScreenConfig | None = None,
    warn: Logger | None = None,
    info: Logger | None = None,
) -> list[int]:
    cfg = config or ScreenConfig()
    warn = warn or (lambda msg: None)
    info = info or (lambda msg: None)

    ttm_idx = _build_metric_index(db, universe, TTM_METRIC)
    fwd_idx = _build_metric_index(db, universe, FWD_METRIC)
    mult_idx = _build_metric_index(db, universe, MULTIPLE_METRIC)

    ttm_as_of = as_of_date - timedelta(days=cfg.ttm_lag_days)
    consensus_as_of = as_of_date - timedelta(days=cfg.consensus_lag_days)

    peg_by_instrument: dict[int, float] = {}
    excluded_no_data = 0
    excluded_peg = 0
    for iid in universe:
        ttm = _latest_at_or_before(ttm_idx, iid, ttm_as_of, cfg.max_age_days)
        fwd = _latest_at_or_before(fwd_idx, iid, consensus_as_of, cfg.max_age_days)
        mult = _latest_at_or_before(mult_idx, iid, consensus_as_of, cfg.max_age_days)
        if ttm is None or fwd is None or mult is None or ttm == 0 or fwd <= 0:
            excluded_no_data += 1
            continue
        growth = (fwd - ttm) / abs(ttm)
        if growth == 0:
            excluded_no_data += 1
            continue
        peg = mult / growth
        if peg <= cfg.peg_min:
            excluded_peg += 1
            continue
        peg_by_instrument[iid] = peg

    if not peg_by_instrument:
        warn(f"{as_of_date}: EBITDA PEG 계산 가능한 종목이 없음 (데이터부족 {excluded_no_data}, PEG<=0 {excluded_peg})")
        return []

    insts = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(peg_by_instrument.keys())).all()}
    sector_groups: dict[str, list[tuple[int, float]]] = {}
    for iid, peg in peg_by_instrument.items():
        sector = insts[iid].krx_sector or insts[iid].sector or "미분류"
        sector_groups.setdefault(sector, []).append((iid, peg))

    overall_sorted = sorted(peg_by_instrument.values())
    overall_cutoff = _percentile(overall_sorted, cfg.top_pct)

    survivors: list[int] = []
    thin_sectors: list[str] = []
    for sector, items in sector_groups.items():
        if len(items) >= cfg.min_sector_size:
            items_sorted = sorted(items, key=lambda x: x[1])
            k = max(1, round(len(items_sorted) * cfg.top_pct))
            survivors.extend(iid for iid, _ in items_sorted[:k])
        else:
            thin_sectors.append(sector)
            survivors.extend(iid for iid, peg in items if peg <= overall_cutoff)

    info(
        f"{as_of_date} EBITDA PEG 스크리닝: 유니버스 {len(universe)} -> 계산가능 {len(peg_by_instrument)} "
        f"(데이터부족/성장없음 제외 {excluded_no_data}, PEG<=0 제외 {excluded_peg}) -> 통과 {len(survivors)}"
        + (f" | 표본부족 섹터(전체분포 대체): {', '.join(thin_sectors)}" if thin_sectors else "")
    )

    return survivors
