"""유동주식시가총액 방식 지수 재현 엔진.

KOSPI200/KOSDAQ150의 실제 편입종목(index_memberships 반기 스냅샷)을 그대로 쓰되,
비중은 raw_close(실제 체결가) x shares_outstanding_monthly x free_float_ratio 로 계산한
유동주식시가총액 기준으로 매월 재계산한다. 편입종목 자체는 반기 스냅샷이 바뀔 때만
바뀐다(=6개월 주기 "지수업데이트"). 일별 가치평가는 Price.close(분할조정, 배당 미반영
- 순수 가격지수 성격)의 리밸런싱일 대비 비율을 누적한다.

보유구간 도중 시계열이 끊기면(상장폐지/인수합병) backtest_service와 동일하게
corporate_action_events 분류를 재사용한다 — 이미 momentum 백테스트에서 사유를
입력해둔 종목은 여기서도 자동으로 같은 분류가 적용된다.
"""
import bisect
from dataclasses import dataclass
from datetime import date
from typing import Callable

from sqlalchemy.orm import Session

from app.models.index_membership import IndexMembership
from app.models.instrument import Instrument
from app.models.monthly_fundamental import MonthlyFundamental
from app.models.price import Price
from app.services.backtest_service import (
    _raw_close_on as _adjusted_close_on_break_date,
)
from app.services.backtest_service import (
    _ratio_path,
    _shift_month,
    get_corporate_action_event,
    get_trading_days,
    resolve_universe,
)
from app.services.backtest_service import _last_trading_day_of_month as _month_end

Logger = Callable[[str], None]


def _all_ever_members(db: Session, index_name: str) -> list[int]:
    return [
        r[0] for r in db.query(IndexMembership.instrument_id).filter(IndexMembership.index_name == index_name).distinct().all()
    ]


def _build_fundamental_index(db: Session, instrument_ids: list[int], metric: str) -> dict[int, tuple[list[date], list[float]]]:
    """instrument_id -> (정렬된 date 리스트, 대응 value 리스트). bisect로 '기준일 이하 최신값' 조회용."""
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


def _latest_at_or_before(idx: dict[int, tuple[list[date], list[float]]], instrument_id: int, as_of: date) -> float | None:
    entry = idx.get(instrument_id)
    if not entry:
        return None
    dates, values = entry
    pos = bisect.bisect_right(dates, as_of) - 1
    if pos < 0:
        return None
    return values[pos]


def _period_close_maps(db: Session, instrument_ids: list[int], period_start: date, period_end: date) -> dict[int, dict[date, float]]:
    if not instrument_ids:
        return {}
    rows = (
        db.query(Price.instrument_id, Price.date, Price.close)
        .filter(
            Price.instrument_id.in_(instrument_ids),
            Price.period == "D",
            Price.date.between(period_start, period_end),
            Price.close.isnot(None),
        )
        .all()
    )
    maps: dict[int, dict[date, float]] = {}
    for iid, d, c in rows:
        maps.setdefault(iid, {})[d] = float(c)
    return maps


def _close_map_query(db: Session, instrument_id: int, start: date, end: date) -> dict[date, float]:
    rows = (
        db.query(Price.date, Price.close)
        .filter(Price.instrument_id == instrument_id, Price.period == "D", Price.date.between(start, end), Price.close.isnot(None))
        .all()
    )
    return {r.date: float(r.close) for r in rows}


def _holding_value_path(
    db: Session,
    instrument_id: int,
    period_start: date,
    trading_days: list[date],
    close_map: dict[date, float],
    warn: Logger,
    info: Logger,
    warnings_log: list[str],
) -> dict[date, float]:
    """보유구간(trading_days) 동안 이 종목의 period_start 대비 가치 배수 시계열 (Price.close 기준, 배당 미반영)."""
    period_end = trading_days[-1]
    break_date: date | None = None
    if close_map:
        last_date = max(close_map)
        if last_date < period_end:
            break_date = last_date

    if break_date is None:
        return _ratio_path(close_map, period_start, trading_days)

    pre_days = [d for d in trading_days if d <= break_date]
    post_days = [d for d in trading_days if d > break_date]
    pre_path = _ratio_path(close_map, period_start, pre_days) if pre_days else {}
    multiplier_at_break = pre_path.get(break_date, 1.0)

    inst = db.get(Instrument, instrument_id)
    event = get_corporate_action_event(db, instrument_id)
    orig_raw = _adjusted_close_on_break_date(db, instrument_id, break_date)

    if event is None:
        warn(f"[⚠ 시계열단절] {inst.ticker} {inst.name}: {break_date} 이후 데이터 없음 → 미분류, 마지막 가치로 동결(잠정치)")
        warnings_log.append(f"{inst.ticker}({inst.name}) {break_date} 이후 시계열 단절 — 미분류(잠정치)")
        return {**pre_path, **{d: multiplier_at_break for d in post_days}}

    if event.event_type == "delisted":
        disposal = float(event.disposal_value) if event.disposal_value is not None else 0.0
        info(f"{inst.ticker} {inst.name}: {break_date} 상장폐지 처리됨 (처분가치 {disposal:,.0f}원, 사유: {event.note or '-'})")
        swap = (disposal / orig_raw) if orig_raw else 0.0
        after_value = multiplier_at_break * swap
        return {**pre_path, **{d: after_value for d in post_days}}

    # merger
    successor = db.get(Instrument, event.successor_instrument_id)
    info(
        f"{inst.ticker} {inst.name}: {break_date} 인수합병 처리됨 "
        f"(후속: {successor.ticker} {successor.name}, 교환비율 {event.exchange_ratio}, 사유: {event.note or '-'})"
    )
    successor_raw = _adjusted_close_on_break_date(db, successor.id, break_date)
    swap = (float(event.exchange_ratio) * successor_raw / orig_raw) if (orig_raw and successor_raw) else 1.0
    after_base = multiplier_at_break * swap

    successor_values = _close_map_query(db, successor.id, break_date, period_end)
    successor_ratio = _ratio_path(successor_values, break_date, [break_date] + post_days) if post_days else {}
    post_path = {d: after_base * successor_ratio.get(d, 1.0) for d in post_days}
    return {**pre_path, **post_path}


@dataclass
class IndexBacktestConfig:
    index_name: str  # "KOSPI200" | "KOSDAQ150"
    start_date: date = date(2020, 1, 1)
    end_date: date = date(2026, 4, 30)


@dataclass
class IndexBacktestResult:
    nav_series: list[tuple[date, float]]
    rebalances: list[dict]  # {"date", "universe_changed", "reason", "weights": {ticker: weight}}
    warnings: list[str]
    missing_data: list[dict]  # [{"ticker", "name", "excluded_count", "first_date", "last_date", "reasons"}]


def run_free_float_index(
    db: Session,
    config: IndexBacktestConfig,
    on_warning: Logger | None = None,
    on_info: Logger | None = None,
) -> IndexBacktestResult:
    warn = on_warning or (lambda msg: None)
    info = on_info or (lambda msg: None)

    all_members = _all_ever_members(db, config.index_name)
    ff_idx = _build_fundamental_index(db, all_members, "free_float_ratio")
    so_idx = _build_fundamental_index(db, all_members, "shares_outstanding_monthly")

    prev_y, prev_m = _shift_month(config.start_date.year, config.start_date.month, -1)
    initial_date = _month_end(db, prev_y, prev_m)

    rebalance_dates = [initial_date]
    y, m = config.start_date.year, config.start_date.month
    while True:
        candidate = _month_end(db, y, m)
        if candidate > config.end_date:
            break
        rebalance_dates.append(candidate)
        y, m = _shift_month(y, m, 1)
    if rebalance_dates[-1] < config.end_date:
        # end_date가 아직 끝나지 않은 달 도중이면(예: 최근까지 실험) 그 달은 정식
        # 월말 리밸런싱을 하지 않고, 마지막으로 확정된 비중을 유지한 채 데이터가
        # 있는 마지막 날짜까지만 시계열을 이어붙인다.
        rebalance_dates.append(config.end_date)

    nav = 100.0
    nav_series: list[tuple[date, float]] = [(rebalance_dates[0], nav)]
    rebalance_log: list[dict] = []
    warnings_log: list[str] = []
    missing_log: dict[int, list[tuple[date, list[str]]]] = {}
    prev_universe: set[int] | None = None

    for i in range(len(rebalance_dates) - 1):
        period_start = rebalance_dates[i]
        period_end = rebalance_dates[i + 1]
        trading_days = get_trading_days(db, period_start, period_end)

        universe = resolve_universe(db, config.index_name, period_start)
        universe_set = set(universe)
        universe_changed = universe_set != prev_universe
        prev_universe = universe_set

        raw_rows = (
            db.query(Price.instrument_id, Price.raw_close)
            .filter(
                Price.instrument_id.in_(universe),
                Price.period == "D",
                Price.date == period_start,
                Price.raw_close.isnot(None),
            )
            .all()
        )
        raw_map = {iid: float(rc) for iid, rc in raw_rows}

        caps: dict[int, float] = {}
        missing: list[tuple[int, list[str]]] = []
        for iid in universe:
            raw_close = raw_map.get(iid)
            shares = _latest_at_or_before(so_idx, iid, period_start)
            ratio = _latest_at_or_before(ff_idx, iid, period_start)
            reasons = []
            if raw_close is None:
                reasons.append("raw_close")
            if shares is None:
                reasons.append("상장주식수")
            if ratio is None or ratio <= 0:
                reasons.append("유동비율")
            if reasons:
                missing.append((iid, reasons))
                continue
            caps[iid] = raw_close * shares * (ratio / 100.0)

        if missing:
            insts = {i.id: i.ticker for i in db.query(Instrument).filter(Instrument.id.in_([iid for iid, _ in missing])).all()}
            warn(
                f"{period_start} 유동시총 데이터 부족으로 {len(missing)}종목 제외: "
                + ", ".join(insts.get(iid, str(iid)) for iid, _ in missing)
            )
            for iid, reasons in missing:
                missing_log.setdefault(iid, []).append((period_start, reasons))

        total = sum(caps.values())
        if total <= 0:
            warn(f"{period_start}: 유효한 유동시총 데이터가 없어 리밸런싱을 건너뜁니다 (NAV 동결).")
            for day in trading_days[1:]:
                nav_series.append((day, nav))
            continue

        weights = {iid: cap / total for iid, cap in caps.items()}
        instruments = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(weights.keys())).all()}
        reason = "정기리밸런싱(종목변경)" if universe_changed else "정기리밸런싱(비중갱신)"
        info(f"{period_start} {reason}: {len(weights)}종목 유동시총 가중")
        rebalance_log.append(
            {
                "date": period_start,
                "universe_changed": universe_changed,
                "reason": reason,
                "weights": {instruments[iid].ticker: w for iid, w in weights.items()},
            }
        )

        if len(trading_days) < 2:
            for day in trading_days[1:]:
                nav_series.append((day, nav))
            continue

        close_maps = _period_close_maps(db, list(weights.keys()), period_start, period_end)
        value_paths = {
            iid: _holding_value_path(db, iid, period_start, trading_days, close_maps.get(iid, {}), warn, info, warnings_log)
            for iid in weights
        }
        period_base_nav = nav
        for day in trading_days[1:]:
            multiplier = sum(weights[iid] * value_paths[iid].get(day, 1.0) for iid in weights)
            nav = period_base_nav * multiplier
            nav_series.append((day, nav))

    missing_summary: list[dict] = []
    if missing_log:
        insts = {i.id: i for i in db.query(Instrument).filter(Instrument.id.in_(missing_log.keys())).all()}
        for iid, entries in missing_log.items():
            inst = insts.get(iid)
            dates = [d for d, _ in entries]
            reasons = sorted({r for _, rs in entries for r in rs})
            missing_summary.append(
                {
                    "ticker": inst.ticker if inst else str(iid),
                    "name": inst.name if inst else "",
                    "excluded_count": len(entries),
                    "first_date": min(dates),
                    "last_date": max(dates),
                    "reasons": reasons,
                }
            )
        missing_summary.sort(key=lambda x: x["ticker"])

    return IndexBacktestResult(
        nav_series=nav_series, rebalances=rebalance_log, warnings=warnings_log, missing_data=missing_summary
    )
