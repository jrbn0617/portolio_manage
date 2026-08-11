"""모멘텀 전략 백테스트 엔진.

1차 전략: 코스닥150 편입종목, 최근 1개월 제외 12개월 모멘텀(13개월치 월말
데이터) 상위 N종목 동일가중, 매월말 리밸런싱. 통계는 일 단위로 산출한다.

보유종목의 가격 시계열이 보유구간 도중에 끊기면(상장폐지/인수합병 추정)
`corporate_action_events`에 등록된 분류를 따르고, 없으면 경고만 남기고
마지막 가치로 동결한 채 계속 진행한다 — 사유는 resolve_backtest_event.py로
별도 입력한다.
"""
import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.corporate_action_event import CorporateActionEvent
from app.models.dividend_adjusted_price import DividendAdjustedPrice
from app.models.index_membership import IndexMembership
from app.models.instrument import Instrument
from app.models.market_holiday import MarketHoliday
from app.models.monthly_fundamental import MonthlyFundamental
from app.models.price import Price
from app.services.index_market_cap_service import snap_to_trading_day

Logger = Callable[[str], None]


def get_trading_days(db: Session, start: date, end: date) -> list[date]:
    """market_holidays 기반 거래일 캘린더. [start, end] 양끝 포함."""
    holidays = {r[0] for r in db.query(MarketHoliday.date).filter(MarketHoliday.date.between(start, end)).all()}
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            days.append(d)
        d += timedelta(days=1)
    return days


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + offset
    return total // 12, total % 12 + 1


def _last_trading_day_of_month(db: Session, year: int, month: int) -> date:
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    days = get_trading_days(db, start, end)
    return days[-1]


def resolve_universe(db: Session, index_name: str, as_of_date: date) -> list[int]:
    """as_of_date 시점에 유효한 가장 최근 반기 스냅샷의 편입종목 instrument_id 목록.

    스냅샷 자체 날짜(예: 12/31)가 비거래일일 수 있으므로, 그 스냅샷의 실제
    적용 시점은 "스냅샷 날짜의 직전 실제 거래일"로 스냅해서 비교한다
    (index_market_cap_service.snap_to_trading_day 재사용) — 그래야 리밸런싱
    시점이 스냅샷 날짜 하루 전(예: 2019-12-30)이어도 그 스냅샷이 정상 적용된다.
    """
    snapshots = [
        r[0]
        for r in db.query(IndexMembership.as_of_date).filter(IndexMembership.index_name == index_name).distinct().all()
    ]
    eligible = [s for s in snapshots if snap_to_trading_day(db, s) <= as_of_date]
    if not eligible:
        return []
    snapshot = max(eligible)
    return [
        r[0]
        for r in db.query(IndexMembership.instrument_id)
        .filter(IndexMembership.index_name == index_name, IndexMembership.as_of_date == snapshot)
        .all()
    ]


def _monthly_adj_close(db: Session, instrument_id: int, year: int, month: int) -> tuple[date, float] | None:
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    row = (
        db.query(DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close)
        .filter(
            DividendAdjustedPrice.instrument_id == instrument_id,
            DividendAdjustedPrice.period == "M",
            DividendAdjustedPrice.date.between(start, end),
        )
        .first()
    )
    return (row.date, float(row.adj_close)) if row else None


def compute_momentum(
    db: Session,
    instrument_ids: list[int],
    rebalance_date: date,
    lookback_months: int = 12,
    skip_months: int = 1,
) -> dict[int, float | None]:
    """모멘텀(iid) = adj_close(월말 M-skip) / adj_close(월말 M-skip-lookback) - 1.
    이상적 시작월 데이터가 없으면 보유한 가장 이른 월말 데이터로 자동 축소한다
    (2개월 미만이면 계산 불가로 None)."""
    end_year, end_month = _shift_month(rebalance_date.year, rebalance_date.month, -skip_months)
    start_year, start_month = _shift_month(rebalance_date.year, rebalance_date.month, -(skip_months + lookback_months))

    result: dict[int, float | None] = {}
    for iid in instrument_ids:
        end_point = _monthly_adj_close(db, iid, end_year, end_month)
        if end_point is None:
            result[iid] = None
            continue
        end_date, end_value = end_point

        start_point = _monthly_adj_close(db, iid, start_year, start_month)
        if start_point is None:
            earliest = (
                db.query(DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close)
                .filter(
                    DividendAdjustedPrice.instrument_id == iid,
                    DividendAdjustedPrice.period == "M",
                    DividendAdjustedPrice.date < end_date,
                )
                .order_by(DividendAdjustedPrice.date)
                .first()
            )
            start_point = (earliest.date, float(earliest.adj_close)) if earliest else None

        if start_point is None:
            result[iid] = None
            continue

        start_date, start_value = start_point
        months_span = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
        if months_span < 2 or not start_value:
            result[iid] = None
            continue

        result[iid] = end_value / start_value - 1
    return result


def find_series_break(db: Session, instrument_id: int, period_start: date, period_end: date) -> date | None:
    """[period_start, period_end] 보유구간 내 시계열이 끊기는지 확인.
    끊기면 마지막 실제 데이터일을, 안 끊기면 None을 반환한다."""
    last_date = (
        db.query(func.max(DividendAdjustedPrice.date))
        .filter(
            DividendAdjustedPrice.instrument_id == instrument_id,
            DividendAdjustedPrice.period == "D",
            DividendAdjustedPrice.date <= period_end,
        )
        .scalar()
    )
    if last_date is None or last_date >= period_end:
        return None
    return last_date


def get_corporate_action_event(db: Session, instrument_id: int) -> CorporateActionEvent | None:
    return db.query(CorporateActionEvent).filter(CorporateActionEvent.instrument_id == instrument_id).first()


def _daily_adj_close_map(db: Session, instrument_id: int, start: date, end: date) -> dict[date, float]:
    rows = (
        db.query(DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close)
        .filter(
            DividendAdjustedPrice.instrument_id == instrument_id,
            DividendAdjustedPrice.period == "D",
            DividendAdjustedPrice.date.between(start, end),
        )
        .all()
    )
    return {r.date: float(r.adj_close) for r in rows}


def _raw_close_on(db: Session, instrument_id: int, d: date) -> float | None:
    row = (
        db.query(Price.close)
        .filter(Price.instrument_id == instrument_id, Price.period == "D", Price.date == d)
        .first()
    )
    return float(row[0]) if row else None


def _free_float_raw_close_on(db: Session, instrument_id: int, d: date) -> float | None:
    row = (
        db.query(Price.raw_close)
        .filter(Price.instrument_id == instrument_id, Price.period == "D", Price.date == d)
        .first()
    )
    return float(row[0]) if row and row[0] is not None else None


def _latest_monthly_value(db: Session, instrument_id: int, metric: str, as_of: date) -> float | None:
    row = (
        db.query(MonthlyFundamental.value)
        .filter(MonthlyFundamental.instrument_id == instrument_id, MonthlyFundamental.metric == metric, MonthlyFundamental.date <= as_of)
        .order_by(MonthlyFundamental.date.desc())
        .first()
    )
    return float(row[0]) if row else None


def cap_weights(weights: dict[int, float], max_weight: float) -> dict[int, float]:
    """비중 상한 캡. 상한 초과분을 캡에 안 걸린 종목들에 비례 재분배한다(표준 지수캡
    waterfall 방식 — 재분배 후 다른 종목이 새로 캡을 넘을 수 있어 수렴할 때까지 반복).
    max_weight * 종목수 < 1 이라 수학적으로 캡을 지킬 방법이 없으면 동일가중으로 폴백."""
    weights = dict(weights)
    n = len(weights)
    if n == 0 or max_weight * n < 1.0 - 1e-9:
        return {iid: 1.0 / n for iid in weights} if n else {}

    for _ in range(n):
        over = {iid: w for iid, w in weights.items() if w > max_weight + 1e-12}
        if not over:
            break
        excess = sum(w - max_weight for w in over.values())
        for iid in over:
            weights[iid] = max_weight
        uncapped_ids = [iid for iid in weights if iid not in over]
        uncapped_total = sum(weights[iid] for iid in uncapped_ids)
        if uncapped_total <= 0:
            return {iid: 1.0 / n for iid in weights}
        for iid in uncapped_ids:
            weights[iid] += excess * (weights[iid] / uncapped_total)
    return weights


def cap_weights_with_groups(
    weights: dict[int, float],
    max_weight: float | None = None,
    groups: dict[int, str] | None = None,
    max_group_weight: float | None = None,
    max_rounds: int = 50,
) -> dict[int, float]:
    """cap_weights를 종목캡+그룹캡(업종/섹터/산업 등) 동시 적용으로 확장한 버전.
    한 라운드에 (1) 종목캡 초과분을 캡 안 걸린 종목에 비례 재분배 → (2) 그룹합계 초과분을
    다른 그룹 종목에 비례 재분배, 순서로 처리하고 더 이상 변화가 없을 때까지 반복한다
    (그룹 캡 재분배가 개별 종목을 다시 종목캡 위로 밀어올릴 수 있어 두 제약이 동시에
    수렴할 때까지 번갈아 적용해야 함). groups가 None이거나 max_group_weight가 None이면
    그룹캡은 건너뛰고 cap_weights와 동일하게 동작."""
    weights = dict(weights)
    n = len(weights)
    if n == 0:
        return {}

    for _ in range(max_rounds):
        changed = False

        if max_weight is not None:
            over = {iid: w for iid, w in weights.items() if w > max_weight + 1e-12}
            if over:
                if max_weight * n < 1.0 - 1e-9:
                    return {iid: 1.0 / n for iid in weights}
                changed = True
                excess = sum(w - max_weight for w in over.values())
                for iid in over:
                    weights[iid] = max_weight
                uncapped_ids = [iid for iid in weights if iid not in over]
                uncapped_total = sum(weights[iid] for iid in uncapped_ids)
                if uncapped_total <= 0:
                    return {iid: 1.0 / n for iid in weights}
                for iid in uncapped_ids:
                    weights[iid] += excess * (weights[iid] / uncapped_total)

        if groups is not None and max_group_weight is not None:
            group_sums: dict[str, float] = {}
            for iid, w in weights.items():
                group_sums[groups.get(iid, "미분류")] = group_sums.get(groups.get(iid, "미분류"), 0.0) + w
            over_groups = {g for g, s in group_sums.items() if s > max_group_weight + 1e-12}
            if over_groups:
                if max_group_weight * len(group_sums) < 1.0 - 1e-9:
                    return {iid: 1.0 / n for iid in weights}
                changed = True
                excess = 0.0
                for iid in weights:
                    g = groups.get(iid, "미분류")
                    if g in over_groups:
                        old = weights[iid]
                        weights[iid] = old * (max_group_weight / group_sums[g])
                        excess += old - weights[iid]
                under_ids = [iid for iid in weights if groups.get(iid, "미분류") not in over_groups]
                under_total = sum(weights[iid] for iid in under_ids)
                if under_total <= 0:
                    return {iid: 1.0 / n for iid in weights}
                for iid in under_ids:
                    weights[iid] += excess * (weights[iid] / under_total)

        if not changed:
            break
    return weights


def drop_below_min_weight(
    weights: dict[int, float],
    min_weight: float,
    max_weight: float | None = None,
    groups: dict[int, str] | None = None,
    max_group_weight: float | None = None,
    min_holdings: int = 2,
    max_rounds: int = 20,
) -> dict[int, float]:
    """실험#14: 비중이 min_weight 미만인 종목을 편입에서 빼고 잔여 종목에 **원래 비중
    비율대로** 재분배한다. 너무 작아서 실질적 기여도 없이 매매비용·체결부담만 만드는
    자투리 편입을 없애는 규칙이다.

    재분배로 특정 종목이 다시 종목캡/그룹캡을 넘을 수 있고, 캡 재적용이 다른 종목을
    다시 min_weight 아래로 밀어낼 수도 있어(그룹캡은 초과 그룹의 비중을 깎는다)
    **더 이상 탈락자가 없을 때까지 "제외 → 재분배 → 캡 재적용"을 반복한다.**

    반환값은 입력과 같은 키를 유지하되 탈락 종목은 0.0이다 — run_momentum_backtest가
    holdings 전체에 대해 weights를 조회하므로 키를 빼면 KeyError가 난다.
    min_holdings 미만으로 줄어드는 제외는 수행하지 않는다."""
    active = {iid: w for iid, w in weights.items() if w > 0}
    for _ in range(max_rounds):
        drop = [iid for iid, w in active.items() if w < min_weight - 1e-12]
        if not drop or len(active) - len(drop) < min_holdings:
            break
        for iid in drop:
            del active[iid]
        total = sum(active.values())
        if total <= 0:
            break
        active = {iid: w / total for iid, w in active.items()}
        if max_weight is not None or max_group_weight is not None:
            active = cap_weights_with_groups(
                active,
                max_weight=max_weight,
                groups={iid: groups[iid] for iid in active} if groups else None,
                max_group_weight=max_group_weight,
            )
    return {iid: active.get(iid, 0.0) for iid in weights}


def compute_free_float_weights(
    db: Session,
    instrument_ids: list[int],
    as_of_date: date,
    warn: Logger | None = None,
    momentum: dict[int, float] | None = None,  # WeightFn 시그니처 통일용, 여기선 안 씀
    max_weight: float | None = None,
    group_field: str | None = None,
    max_group_weight: float | None = None,
    min_weight: float | None = None,
) -> dict[int, float]:
    """보유종목의 유동주식시가총액(raw_close x 상장주식수 x 유동비율) 비례 가중치.
    데이터가 없는 종목은 동일가중 몫(1/n)을 그대로 배정하고, 나머지 예산을 시총
    비례로 나머지 종목에 분배한다(전부 실패하면 동일가중으로 폴백) — run_momentum_backtest의
    weight_fn으로 사용. max_weight가 주어지면 종목당 비중 상한을 적용한다. group_field(
    Instrument의 "krx_sector"/"sector"/"industry" 등)와 max_group_weight를 함께 주면
    같은 그룹(업종/섹터/산업) 합산 비중에도 상한을 적용한다(cap_weights_with_groups).
    min_weight가 주어지면 캡 적용 후 그 비중에 못 미치는 종목을 편입에서 빼고 잔여
    종목에 비례 재분배한다(drop_below_min_weight) — 탈락 종목은 0.0으로 반환된다."""
    warn = warn or (lambda msg: None)
    n = len(instrument_ids)
    if n == 0:
        return {}
    equal_share = 1.0 / n

    caps: dict[int, float] = {}
    for iid in instrument_ids:
        raw_close = _free_float_raw_close_on(db, iid, as_of_date)
        shares = _latest_monthly_value(db, iid, "shares_outstanding_monthly", as_of_date)
        ratio = _latest_monthly_value(db, iid, "free_float_ratio", as_of_date)
        if raw_close is None or shares is None or ratio is None or ratio <= 0:
            continue
        caps[iid] = raw_close * shares * (ratio / 100.0)

    missing = [iid for iid in instrument_ids if iid not in caps]
    if missing:
        warn(f"{as_of_date}: 유동시총 데이터 부족으로 {len(missing)}종목 동일가중 대체")

    if not caps:
        weights = {iid: equal_share for iid in instrument_ids}
    else:
        reserved = len(missing) * equal_share
        remaining_budget = 1.0 - reserved
        total_cap = sum(caps.values())
        weights = {iid: equal_share for iid in missing}
        for iid, cap in caps.items():
            weights[iid] = remaining_budget * (cap / total_cap)

    if max_weight is None and max_group_weight is None and min_weight is None:
        return weights

    groups = None
    if group_field is not None and max_group_weight is not None:
        instruments = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(instrument_ids)).all()}
        groups = {iid: (getattr(instruments[iid], group_field) or "미분류") for iid in instrument_ids}
    weights = cap_weights_with_groups(
        weights, max_weight=max_weight, groups=groups, max_group_weight=max_group_weight
    )
    if min_weight is not None:
        weights = drop_below_min_weight(
            weights,
            min_weight=min_weight,
            max_weight=max_weight,
            groups=groups,
            max_group_weight=max_group_weight,
        )
    return weights


def compute_inverse_vol_weights(
    db: Session,
    instrument_ids: list[int],
    as_of_date: date,
    warn: Logger | None = None,
    momentum: dict[int, float] | None = None,  # WeightFn 시그니처 통일용, 여기선 안 씀
    lookback_days: int = 63,
    min_observations: int = 20,
    max_weight: float | None = None,
) -> dict[int, float]:
    """보유종목의 역변동성 비례 가중치(단순 risk budget — 종목간 상관관계는 무시하고
    각 종목 자체 변동성만 반영). as_of_date 이전 lookback_days 거래일(기본 63일=~3개월)의
    DividendAdjustedPrice.adj_close 일간수익률 표준편차를 변동성으로 쓴다. 관측치가
    min_observations 미만이면(신규상장 등) 그 종목은 동일가중 몫으로 대체(전부 실패하면
    전체 동일가중 폴백). max_weight가 주어지면 종목당 비중 상한을 적용한다(cap_weights)."""
    warn = warn or (lambda msg: None)
    n = len(instrument_ids)
    if n == 0:
        return {}
    equal_share = 1.0 / n

    start = as_of_date - timedelta(days=lookback_days * 2 + 10)  # 휴장일 감안 넉넉히
    vols: dict[int, float] = {}
    for iid in instrument_ids:
        rows = (
            db.query(DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close)
            .filter(
                DividendAdjustedPrice.instrument_id == iid,
                DividendAdjustedPrice.period == "D",
                DividendAdjustedPrice.date <= as_of_date,
                DividendAdjustedPrice.date >= start,
            )
            .order_by(DividendAdjustedPrice.date)
            .all()
        )
        closes = [float(r.adj_close) for r in rows][-(lookback_days + 1) :]
        if len(closes) < min_observations + 1:
            continue
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        mean_r = sum(rets) / len(rets)
        variance = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        vol = variance**0.5
        if vol > 0:
            vols[iid] = vol

    missing = [iid for iid in instrument_ids if iid not in vols]
    if missing:
        warn(f"{as_of_date}: 변동성 데이터 부족으로 {len(missing)}종목 동일가중 대체")

    if not vols:
        weights = {iid: equal_share for iid in instrument_ids}
        return cap_weights(weights, max_weight) if max_weight is not None else weights

    inv_vols = {iid: 1.0 / v for iid, v in vols.items()}
    reserved = len(missing) * equal_share
    remaining_budget = 1.0 - reserved
    total_inv = sum(inv_vols.values())
    weights = {iid: equal_share for iid in missing}
    for iid, iv in inv_vols.items():
        weights[iid] = remaining_budget * (iv / total_inv)
    return cap_weights(weights, max_weight) if max_weight is not None else weights


def compute_momentum_weights(
    db: Session,
    instrument_ids: list[int],
    as_of_date: date,
    warn: Logger | None = None,
    momentum: dict[int, float] | None = None,
    max_weight: float | None = None,
) -> dict[int, float]:
    """보유종목의 모멘텀 스코어 비례 가중치 — 모멘텀이 강할수록 비중도 커진다.
    momentum에 음수가 섞여있으면(하락장 등 상위권도 마이너스인 경우) 최솟값만큼
    전체를 평행이동해 모두 양수로 만든 뒤(종목간 상대적 격차는 유지) 비례 배분한다.
    momentum이 없거나 계산 불가한 종목은 동일가중 몫으로 대체. max_weight가 주어지면
    종목당 비중 상한을 적용한다(cap_weights)."""
    warn = warn or (lambda msg: None)
    n = len(instrument_ids)
    if n == 0:
        return {}
    equal_share = 1.0 / n
    momentum = momentum or {}

    scores = {iid: momentum[iid] for iid in instrument_ids if momentum.get(iid) is not None}
    missing = [iid for iid in instrument_ids if iid not in scores]
    if missing:
        warn(f"{as_of_date}: 모멘텀 데이터 부족으로 {len(missing)}종목 동일가중 대체")

    if not scores:
        weights = {iid: equal_share for iid in instrument_ids}
        return cap_weights(weights, max_weight) if max_weight is not None else weights

    min_score = min(scores.values())
    epsilon = 1e-6
    shift = (-min_score + epsilon) if min_score <= 0 else 0.0
    shifted = {iid: v + shift for iid, v in scores.items()}

    reserved = len(missing) * equal_share
    remaining_budget = 1.0 - reserved
    total = sum(shifted.values())
    weights = {iid: equal_share for iid in missing}
    for iid, v in shifted.items():
        weights[iid] = remaining_budget * (v / total)
    return cap_weights(weights, max_weight) if max_weight is not None else weights


def compute_inverse_momentum_weights(
    db: Session,
    instrument_ids: list[int],
    as_of_date: date,
    warn: Logger | None = None,
    momentum: dict[int, float] | None = None,
    smoothing_multiplier: float = 2.0,
    max_weight: float | None = None,
) -> dict[int, float]:
    """보유종목의 역모멘텀 비례 가중치 — 모멘텀이 약한(top_n 안에서 상대적으로 덜
    급등한) 종목일수록 비중이 커진다. 먼저 momentum을 전부 양수로 평행이동한 뒤
    (compute_momentum_weights와 동일), 그 평균의 smoothing_multiplier배를 분모에
    더해(기본 2배) 순수 1/score보다 완만하게 역수를 취한다 — 점수가 0에 가까운
    종목에서 가중치가 폭발하는 것을 막고 종목간 격차를 눌러준다. momentum이 없거나
    계산 불가한 종목은 동일가중 몫으로 대체. max_weight가 주어지면 종목당 비중
    상한을 적용한다(cap_weights)."""
    warn = warn or (lambda msg: None)
    n = len(instrument_ids)
    if n == 0:
        return {}
    equal_share = 1.0 / n
    momentum = momentum or {}

    scores = {iid: momentum[iid] for iid in instrument_ids if momentum.get(iid) is not None}
    missing = [iid for iid in instrument_ids if iid not in scores]
    if missing:
        warn(f"{as_of_date}: 모멘텀 데이터 부족으로 {len(missing)}종목 동일가중 대체")

    if not scores:
        weights = {iid: equal_share for iid in instrument_ids}
        return cap_weights(weights, max_weight) if max_weight is not None else weights

    min_score = min(scores.values())
    epsilon = 1e-6
    shift = (-min_score + epsilon) if min_score <= 0 else 0.0
    shifted = {iid: v + shift for iid, v in scores.items()}

    smoothing_constant = smoothing_multiplier * (sum(shifted.values()) / len(shifted))
    inv = {iid: 1.0 / (v + smoothing_constant) for iid, v in shifted.items()}

    reserved = len(missing) * equal_share
    remaining_budget = 1.0 - reserved
    total_inv = sum(inv.values())
    weights = {iid: equal_share for iid in missing}
    for iid, v in inv.items():
        weights[iid] = remaining_budget * (v / total_inv)
    return cap_weights(weights, max_weight) if max_weight is not None else weights


def _ratio_path(values: dict[date, float], base_date: date, trading_days: list[date]) -> dict[date, float]:
    """values(날짜->절대값)를 base_date 대비 비율로 변환. 결측일은 직전 값으로 forward-fill.
    base_date 자체에 값이 없으면(스왑 첫날 거래정지 등) 구간 내 첫 유효값을 기준으로 삼는다."""
    base = values.get(base_date)
    if base is None:
        later = sorted(d for d in values if d >= base_date)
        base = values[later[0]] if later else None
    if base is None:
        return {d: 1.0 for d in trading_days}
    path = {}
    last = base
    for d in trading_days:
        if d in values:
            last = values[d]
        path[d] = last / base
    return path


def _holding_value_path(
    db: Session,
    instrument_id: int,
    period_start: date,
    trading_days: list[date],
    warn: Logger,
    info: Logger,
    warnings_log: list[str],
) -> dict[date, float]:
    """보유구간(trading_days) 동안 이 종목의 period_start 대비 가치 배수 시계열."""
    period_end = trading_days[-1]
    adj_values = _daily_adj_close_map(db, instrument_id, period_start, period_end)
    break_date = find_series_break(db, instrument_id, period_start, period_end)

    if break_date is None:
        return _ratio_path(adj_values, period_start, trading_days)

    pre_days = [d for d in trading_days if d <= break_date]
    post_days = [d for d in trading_days if d > break_date]
    pre_path = _ratio_path(adj_values, period_start, pre_days) if pre_days else {}
    multiplier_at_break = pre_path.get(break_date, 1.0)

    inst = db.get(Instrument, instrument_id)
    event = get_corporate_action_event(db, instrument_id)
    orig_raw = _raw_close_on(db, instrument_id, break_date)

    if event is None:
        warn(f"[⚠ 시계열단절] {inst.ticker} {inst.name}: {break_date} 이후 데이터 없음 → 미분류, 마지막 가치로 동결(잠정치)")
        warnings_log.append(f"{inst.ticker}({inst.name}) {break_date} 이후 시계열 단절 — 미분류(잠정치)")
        return {**pre_path, **{d: multiplier_at_break for d in post_days}}

    if event.event_type == "delisted":
        disposal = float(event.disposal_value) if event.disposal_value is not None else 0.0
        info(
            f"{inst.ticker} {inst.name}: {break_date} 상장폐지 처리됨 "
            f"(처분가치 {disposal:,.0f}원, 사유: {event.note or '-'})"
        )
        swap = (disposal / orig_raw) if orig_raw else 0.0
        after_value = multiplier_at_break * swap
        return {**pre_path, **{d: after_value for d in post_days}}

    # merger
    successor = db.get(Instrument, event.successor_instrument_id)
    info(
        f"{inst.ticker} {inst.name}: {break_date} 인수합병 처리됨 "
        f"(후속: {successor.ticker} {successor.name}, 교환비율 {event.exchange_ratio}, 사유: {event.note or '-'})"
    )
    successor_raw = _raw_close_on(db, successor.id, break_date)
    swap = (float(event.exchange_ratio) * successor_raw / orig_raw) if (orig_raw and successor_raw) else 1.0
    after_base = multiplier_at_break * swap

    successor_values = _daily_adj_close_map(db, successor.id, break_date, period_end)
    successor_ratio = _ratio_path(successor_values, break_date, [break_date] + post_days) if post_days else {}
    post_path = {d: after_base * successor_ratio.get(d, 1.0) for d in post_days}
    return {**pre_path, **post_path}


def _overnight_gap_ratio(db: Session, instrument_id: int, close_day: date, open_day: date) -> float | None:
    """close_day 종가 대비 open_day 시가 비율(분할조정 기준 Price.close/Price.open —
    DividendAdjustedPrice엔 시가가 없어서, 배당조정과 무관하게 근사적으로 동일하다고
    가정하고 갭 비율만 이 테이블에서 구한다). 둘 중 하나라도 없으면 None."""
    close_row = (
        db.query(Price.close)
        .filter(Price.instrument_id == instrument_id, Price.period == "D", Price.date == close_day)
        .first()
    )
    open_row = (
        db.query(Price.open)
        .filter(Price.instrument_id == instrument_id, Price.period == "D", Price.date == open_day)
        .first()
    )
    if not close_row or not open_row or open_row[0] is None or not close_row[0]:
        return None
    return float(open_row[0]) / float(close_row[0])


def apply_stop_loss(
    db: Session,
    instrument_id: int,
    value_path: dict[date, float],
    trading_days: list[date],
    stop_loss_pct: float,
    execution: str = "close",
) -> dict[date, float]:
    """실험#6: period_start 대비 -stop_loss_pct 이하로 처음 떨어지는 거래일(T)부터
    현금(0%수익) 보유로 남은 기간을 버틴다고 가정(남은 보유종목으로의 재배분은 하지
    않음). threshold = 1 - stop_loss_pct.

    execution="close"(기본): T의 종가 그 자체로 동결. execution="next_open"(실험#6
    추가검증에서 더 나은 결과를 보인 방식): 다음 거래일(T+1)의 시가에 매도한다고
    가정 — T까지는 원래(미실현) 가치배수를 그대로 쓰고, T+1부터
    value_path[T] * (Price.open(T+1)/Price.close(T))로 동결(오버나잇 갭 반영,
    `_overnight_gap_ratio`). T가 구간의 마지막 거래일이거나 갭 비율을 구할 가격
    데이터가 없으면 close 방식으로 대체."""
    threshold = 1.0 - stop_loss_pct
    frozen: float | None = None
    freeze_from_idx: int | None = None
    result: dict[date, float] = {}

    for i, d in enumerate(trading_days):
        if frozen is not None and i >= freeze_from_idx:
            result[d] = frozen
            continue
        v = value_path[d]
        if frozen is None and v <= threshold:
            if execution == "close" or i == len(trading_days) - 1:
                frozen = v
                freeze_from_idx = i
                result[d] = v
                continue
            next_day = trading_days[i + 1]
            gap = _overnight_gap_ratio(db, instrument_id, d, next_day)
            frozen = v * gap if gap is not None else v
            freeze_from_idx = i + 1
            result[d] = v
            continue
        result[d] = v
    return result


def compute_regime_exposure(
    db: Session,
    as_of_date: date,
    benchmark_ticker: str,
    ma_window_days: int = 200,
    bull_exposure: float = 1.0,
    bear_exposure: float = 0.5,
) -> float:
    """실험#7: 유니버스 벤치마크 지수(예: KOSDAQ150) 종가가 장기 이동평균(기본
    200거래일) 위/아래인지로 시장 레짐을 판정해 포트폴리오 전체 노출비중
    (bull_exposure/bear_exposure)을 정한다. 이평 계산에 필요한 데이터가 부족하면
    (백테스트 극초반, 벤치마크 지수 시세가 2019-12-02부터라 200거래일 이평은
    2020-10경부터 계산 가능) 강세 레짐(bull_exposure)으로 간주."""
    inst = db.query(Instrument).filter(Instrument.ticker == benchmark_ticker).first()
    rows = (
        db.query(Price.close)
        .filter(Price.instrument_id == inst.id, Price.period == "D", Price.date <= as_of_date)
        .order_by(Price.date.desc())
        .limit(ma_window_days)
        .all()
    )
    if len(rows) < ma_window_days:
        return bull_exposure
    closes = [float(r[0]) for r in rows]
    ma = sum(closes) / len(closes)
    return bull_exposure if closes[0] > ma else bear_exposure


@dataclass
class BacktestConfig:
    index_name: str = "KOSDAQ150"
    lookback_months: int = 12
    skip_months: int = 1
    top_n: int = 10
    max_per_sector: int | None = None  # 섹터당 최대 편입종목수, None이면 제한 없음
    sector_count_field: str | None = None  # None이면 krx_sector/sector 폴백(기존 동작), 필드명 지정시 그 필드만 사용(예: "industry")
    start_date: date = date(2020, 1, 1)
    end_date: date = date(2020, 4, 30)


@dataclass
class BacktestResult:
    nav_series: list[tuple[date, float]]
    rebalances: list[dict]
    warnings: list[str]
    metrics: dict


def _compute_metrics(nav_series: list[tuple[date, float]]) -> dict:
    dates = [d for d, _ in nav_series]
    navs = [v for _, v in nav_series]

    cumulative_return = navs[-1] / navs[0] - 1
    years = (dates[-1] - dates[0]).days / 365.25
    cagr = (navs[-1] / navs[0]) ** (1 / years) - 1 if years > 0 else None

    daily_returns = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
    n = len(daily_returns)
    if n > 1:
        mean_r = sum(daily_returns) / n
        variance = sum((r - mean_r) ** 2 for r in daily_returns) / (n - 1)
        annualized_vol = (variance**0.5) * (252**0.5)
        sharpe = (mean_r * 252) / annualized_vol if annualized_vol else None
    else:
        annualized_vol = None
        sharpe = None

    peak = navs[0]
    mdd = 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)

    monthly_returns: dict[str, float] = {}
    by_month: dict[tuple[int, int], list[float]] = {}
    for d, v in nav_series:
        by_month.setdefault((d.year, d.month), []).append(v)
    prev_last = navs[0]
    for key in sorted(by_month):
        month_end_val = by_month[key][-1]
        monthly_returns[f"{key[0]}-{key[1]:02d}"] = month_end_val / prev_last - 1
        prev_last = month_end_val

    return dict(
        cumulative_return=cumulative_return,
        cagr=cagr,
        annualized_volatility=annualized_vol,
        mdd=mdd,
        sharpe=sharpe,
        monthly_returns=monthly_returns,
    )


ScreenFn = Callable[[Session, list, date], list]
WeightFn = Callable[[Session, list, date, "Logger | None", dict], dict]
ExposureFn = Callable[[Session, date], float]


def run_momentum_backtest(
    db: Session,
    config: BacktestConfig,
    on_warning: Logger | None = None,
    on_info: Logger | None = None,
    screen_fn: "ScreenFn | None" = None,
    weight_fn: "WeightFn | None" = None,
    exposure_fn: "ExposureFn | None" = None,
    stop_loss_pct: float | None = None,
    stop_loss_execution: str = "close",
) -> BacktestResult:
    """실험#9: 실험6(월중 손절)과 실험7(시장 레짐 필터)을 동시에 적용해 조합 효과를
    검증하기 위해 두 훅을 한 브랜치에 합쳤다 — screen_fn/weight_fn은 기존과 동일.

    screen_fn이 주어지면 반기 편입종목(resolve_universe) 이후, 모멘텀 랭킹 이전에
    (db, universe, period_start) -> 통과한 instrument_id 리스트로 유니버스를 추가로
    좁힌다 — 펀더멘털 팩터 스크리닝을 모멘텀 랭킹과 조합할 때 사용
    (예: app/services/factor_screen_service.screen_by_ebitda_peg).

    weight_fn이 주어지면 매 리밸런싱마다 (db, holdings, period_start, warn, momentum) ->
    {instrument_id: weight}로 최종 선정된 top_n 종목의 편입비중을 정한다 — 생략하면
    기존과 동일하게 동일가중. 유동주식시가총액/역변동성/모멘텀 비례 가중은
    app/services/backtest_service.compute_free_float_weights /
    compute_inverse_vol_weights / compute_momentum_weights 참고.

    exposure_fn이 주어지면(실험#7) 매 리밸런싱마다 (db, period_start) -> 노출비중
    스칼라(0~1)를 구해 weight_fn이 정한 종목별 비중 전체에 곱한다(합이 1 미만이 되는
    만큼은 현금 0%수익으로 남는다 — cash_weight로 명시 처리, 실험#7에서 발견한 버그
    수정 반영). 시장 레짐에 따라 포트폴리오 전체 익스포저를 조절할 때 사용(예:
    compute_regime_exposure).

    stop_loss_pct가 주어지면(실험#6) 각 보유종목의 리밸런싱 구간 내 가치배수가
    period_start 대비 -stop_loss_pct 이하로 떨어지는 첫 거래일부터 그 값을 동결한다
    (apply_stop_loss) — 손절 후 현금(0%수익) 보유, 재배분 없음. 생략하면(None, 기본값)
    손절 없이 기존과 동일하게 동작. stop_loss_execution="close"(기본)면 문턱을 넘은
    그 날 종가로 즉시 동결, "next_open"이면 다음 거래일 시가(오버나잇 갭 반영)로
    동결(실험#6 추가검증에서 더 나은 결과)."""
    warn = on_warning or (lambda msg: None)
    info = on_info or (lambda msg: None)

    prev_y, prev_m = _shift_month(config.start_date.year, config.start_date.month, -1)
    initial_date = _last_trading_day_of_month(db, prev_y, prev_m)

    rebalance_dates = [initial_date]
    y, m = config.start_date.year, config.start_date.month
    while (y, m) <= (config.end_date.year, config.end_date.month):
        rebalance_dates.append(_last_trading_day_of_month(db, y, m))
        y, m = _shift_month(y, m, 1)

    nav = 100.0
    nav_series: list[tuple[date, float]] = [(rebalance_dates[0], nav)]
    rebalance_log: list[dict] = []
    warnings_log: list[str] = []

    for i in range(len(rebalance_dates) - 1):
        period_start = rebalance_dates[i]
        period_end = rebalance_dates[i + 1]

        universe = resolve_universe(db, config.index_name, period_start)
        if screen_fn is not None:
            universe = screen_fn(db, universe, period_start)
        momentum = compute_momentum(
            db, universe, period_start, lookback_months=config.lookback_months, skip_months=config.skip_months
        )
        ranked = sorted(
            (iid for iid, v in momentum.items() if v is not None), key=lambda iid: momentum[iid], reverse=True
        )
        ranked_instruments = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(ranked)).all()}

        if config.max_per_sector is not None:
            holdings = []
            sector_count: dict[str, int] = {}
            for iid in ranked:
                if len(holdings) >= config.top_n:
                    break
                if config.sector_count_field is not None:
                    sector = getattr(ranked_instruments[iid], config.sector_count_field) or "미분류"
                else:
                    sector = ranked_instruments[iid].krx_sector or ranked_instruments[iid].sector or "미분류"
                if sector_count.get(sector, 0) >= config.max_per_sector:
                    continue
                holdings.append(iid)
                sector_count[sector] = sector_count.get(sector, 0) + 1
        else:
            holdings = ranked[: config.top_n]

        instruments = ranked_instruments
        info(
            f"{period_start} 리밸런싱: 유니버스 {len(universe)}종목 중 모멘텀 계산가능 {len(ranked)}종목, "
            f"상위 {len(holdings)}종목 선택 — "
            + ", ".join(f"{instruments[iid].ticker}({momentum[iid]:+.1%})" for iid in holdings)
        )

        weights_by_iid = (
            weight_fn(db, holdings, period_start, warn, momentum)
            if (weight_fn is not None and holdings)
            else {iid: 1.0 / len(holdings) for iid in holdings}
        )
        if exposure_fn is not None and holdings:
            exposure = exposure_fn(db, period_start)
            weights_by_iid = {iid: w * exposure for iid, w in weights_by_iid.items()}

        rebalance_log.append(
            {
                "date": period_start,
                "holdings": [instruments[iid].ticker for iid in holdings],
                "momentum": {instruments[iid].ticker: momentum[iid] for iid in holdings},
                "weights": {instruments[iid].ticker: weights_by_iid[iid] for iid in holdings},
            }
        )

        trading_days = get_trading_days(db, period_start, period_end)
        if not holdings or len(trading_days) < 2:
            for day in trading_days[1:]:
                nav_series.append((day, nav))
            continue

        value_paths = {
            iid: _holding_value_path(db, iid, period_start, trading_days, warn, info, warnings_log)
            for iid in holdings
        }
        if stop_loss_pct is not None:
            value_paths = {
                iid: apply_stop_loss(db, iid, path, trading_days, stop_loss_pct, execution=stop_loss_execution)
                for iid, path in value_paths.items()
            }
        # 편입비중 합이 1.0 미만이면(exposure_fn으로 노출비중을 줄인 경우) 나머지는
        # 현금(배수 1.0, 무수익)으로 보유한다고 간주(실험#7에서 발견한 버그 수정).
        cash_weight = max(0.0, 1.0 - sum(weights_by_iid.values()))
        period_base_nav = nav
        for day in trading_days[1:]:
            multiplier = cash_weight + sum(weights_by_iid[iid] * value_paths[iid][day] for iid in holdings)
            nav = period_base_nav * multiplier
            nav_series.append((day, nav))

    metrics = _compute_metrics(nav_series)
    return BacktestResult(nav_series=nav_series, rebalances=rebalance_log, warnings=warnings_log, metrics=metrics)
