"""prices/dividends로부터 파생되는 데이터(월봉, 배당조정 수정주가 지수)를 계산한다.

원본 데이터가 아니라 계산된 값이므로, 원본(prices/dividends)이 바뀔 때마다
영향받는 범위만 다시 계산해서 upsert하는 방식으로 쓴다 (전체 재계산 불필요).
"""
import bisect
import calendar
from datetime import date

from sqlalchemy import delete, exists
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.dividend import Dividend
from app.models.dividend_adjusted_price import DividendAdjustedPrice
from app.models.price import Price
from app.models.raw_close import RawClose


def recompute_monthly_bar(db: Session, instrument_id: int, year: int, month: int) -> None:
    """해당 종목의 특정 (년, 월) 일봉을 집계해 월봉 1건을 upsert한다."""
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    rows = (
        db.query(Price)
        .filter(
            Price.instrument_id == instrument_id,
            Price.period == "D",
            Price.date >= start,
            Price.date <= end,
        )
        .order_by(Price.date)
        .all()
    )
    if not rows:
        return

    highs = [r.high for r in rows if r.high is not None]
    lows = [r.low for r in rows if r.low is not None]
    volumes = [r.volume for r in rows if r.volume is not None]

    new_date = rows[-1].date  # 그 달의 마지막 거래일 (달력상 말일이 아님)

    # 월봉의 date는 "그 달 마지막 거래일"이라 달 진행 중에 새 일봉이 들어올 때마다
    # 바뀔 수 있다 (예: 08-04 -> 08-05). unique 제약이 (instrument_id, date, period)라
    # date가 바뀌면 upsert가 예전 행과 충돌하지 않고 새 행을 또 만들어버리므로,
    # 같은 달의 다른 날짜로 남아있는 이전 월봉 행을 먼저 정리한다.
    db.query(Price).filter(
        Price.instrument_id == instrument_id,
        Price.period == "M",
        Price.date >= start,
        Price.date <= end,
        Price.date != new_date,
    ).delete(synchronize_session=False)

    values = dict(
        instrument_id=instrument_id,
        date=new_date,
        period="M",
        open=rows[0].open,
        high=max(highs) if highs else None,
        low=min(lows) if lows else None,
        close=rows[-1].close,
        volume=sum(volumes) if volumes else None,
    )
    stmt = pg_insert(Price).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "date", "period"],
        set_={k: v for k, v in values.items() if k not in ("instrument_id", "date", "period")},
    )
    db.execute(stmt)


# 국내 주식은 결제가 T+2라, 배정기준일(ex_date)에 주주로 등록되려면 그보다 2거래일
# 전까지 매수해야 한다. 즉 마지막 배당부 거래일(cum-dividend)이 ex_date-2거래일이고,
# 실제로 가격이 배당락 반영되는 첫 거래일(ex-dividend day)은 그 다음날인 ex_date-1거래일이다.
EX_DATE_SETTLEMENT_OFFSET = 1


def _dividend_effective_date(ex_date: date, daily_dates: list[date]) -> date | None:
    """배정기준일(ex_date)로부터 실제 배당락이 반영되는 거래일(1거래일 전)을 구한다.
    데이터 시작 이전으로 밀려나면(과거 이력 부족) None을 반환한다.

    ex_date(배정기준일)는 달력상의 날짜라 그 자체가 거래일이 아닌 경우가 흔하다
    (특히 결산배당의 배정기준일=12/31은 거의 항상 휴장일). 이 경우 "ex_date 이하
    가장 최근 실제 거래일"을 먼저 구하고(bisect_right), 거기서 1거래일 전을 찾아야
    한다. bisect_left를 그대로 쓰면 ex_date가 거래일이 아닐 때 다음 거래일 인덱스를
    기준으로 잡아버려 하루 늦게 계산되는 off-by-one이 생긴다.
    """
    record_idx = bisect.bisect_right(daily_dates, ex_date) - 1  # ex_date 이하 마지막 실제 거래일
    if record_idx < 0:
        return None
    idx = record_idx - EX_DATE_SETTLEMENT_OFFSET
    if idx < 0:
        return None
    return daily_dates[idx]


def _dividend_effective_lookups(
    dividends, daily_dates: list[date], raw_close_by_date: dict[date, float]
) -> tuple[dict, dict]:
    """(일봉용: 날짜 -> [(배당금, 실제종가)], 월봉용: (연,월) -> [(배당금, 실제종가)]) 를 반환한다.
    실제 배당락 반영일(ex_date - 1거래일) 기준으로 계산한다.

    실제종가(raw_close_by_date에 캐시된 값)가 있으면 배당수익률을 그 날의 실제(비조정)
    종가 기준으로 계산해, 이후 액면분할 등으로 수정종가가 재조정돼도 정확하게 유지된다.
    캐시가 없는 날짜는 None으로 남겨두고, 계산 시점에 수정종가로 대체한다(기존 방식과 동일).
    """
    daily_lookup: dict[date, list[tuple[float, float | None]]] = {}
    for ex_date, amount in dividends:
        eff_date = _dividend_effective_date(ex_date, daily_dates)
        if eff_date is None:
            continue
        raw_close = raw_close_by_date.get(eff_date)
        daily_lookup.setdefault(eff_date, []).append((float(amount), raw_close))

    monthly_lookup: dict[tuple[int, int], list[tuple[float, float | None]]] = {}
    for eff_date, entries in daily_lookup.items():
        key = (eff_date.year, eff_date.month)
        monthly_lookup.setdefault(key, []).extend(entries)

    return daily_lookup, monthly_lookup


def recompute_dividend_adjusted(
    db: Session,
    instrument_id: int,
    from_date: date | None = None,
    force_full: bool = False,
) -> None:
    """해당 종목의 일봉/월봉 각각에 대해 배당조정 수정종가 지수를 계산한다.

    - from_date=None, force_full=False (기본): 증분 계산. 마지막으로 저장된 지수
      이후의 새 날짜만 이어서 계산한다. 저장된 값이 아예 없으면 최초 가격일을
      지수 100으로 놓고 처음부터 계산한다.
    - from_date=<date>: 그 날짜의 ex_date를 가진 배당(또는 그 이후) 때문에 다시
      계산해야 하는 경우. 실제 반영일은 ex_date보다 1거래일 이를 수 있으므로,
      해당 배당들의 반영일 중 가장 이른 날짜부터 다시 계산한다.
    - force_full=True: 기존 값을 모두 지우고 최초 가격일(지수 100)부터 전부
      다시 계산한다 — 가격 히스토리 자체가 통째로 재수집된 경우.
    """
    daily_dates = [
        r[0]
        for r in db.query(Price.date)
        .filter(Price.instrument_id == instrument_id, Price.period == "D")
        .order_by(Price.date)
        .all()
    ]

    dividends = (
        db.query(Dividend.ex_date, Dividend.amount)
        .filter(Dividend.instrument_id == instrument_id, Dividend.amount > 0)
        .all()
    )

    if from_date is not None and daily_dates:
        effective_dates = [
            eff
            for ex_date, _ in dividends
            if ex_date >= from_date
            for eff in [_dividend_effective_date(ex_date, daily_dates)]
            if eff is not None
        ]
        if effective_dates:
            from_date = min(from_date, min(effective_dates))

    raw_close_by_date = {
        r.date: float(r.close)
        for r in db.query(RawClose).filter(RawClose.instrument_id == instrument_id).all()
    }

    daily_lookup, monthly_lookup = _dividend_effective_lookups(dividends, daily_dates, raw_close_by_date)

    _recompute_dividend_adjusted_for_period(db, instrument_id, "D", from_date, force_full, daily_lookup)
    _recompute_dividend_adjusted_for_period(db, instrument_id, "M", from_date, force_full, monthly_lookup)


def _recompute_dividend_adjusted_for_period(
    db: Session,
    instrument_id: int,
    period: str,
    from_date: date | None,
    force_full: bool,
    dividend_lookup: dict,
) -> None:
    # prices의 날짜가 바뀌거나 없어졌는데(예: 월봉 마지막 거래일 이동) 예전 값이
    # dividend_adjusted_prices에 고아로 남아있을 수 있으므로 먼저 정리한다.
    stale_exists = exists().where(
        Price.instrument_id == DividendAdjustedPrice.instrument_id,
        Price.period == DividendAdjustedPrice.period,
        Price.date == DividendAdjustedPrice.date,
    )
    db.execute(
        delete(DividendAdjustedPrice).where(
            DividendAdjustedPrice.instrument_id == instrument_id,
            DividendAdjustedPrice.period == period,
            ~stale_exists,
        )
    )

    prices = (
        db.query(Price.date, Price.close)
        .filter(Price.instrument_id == instrument_id, Price.period == period)
        .order_by(Price.date)
        .all()
    )
    if not prices:
        return

    dates: list[date] = [p.date for p in prices]
    close_by_date = {p.date: float(p.close) for p in prices}

    prev_index: float | None = None
    prev_close: float | None = None
    start_idx = 0

    if force_full:
        db.query(DividendAdjustedPrice).filter(
            DividendAdjustedPrice.instrument_id == instrument_id,
            DividendAdjustedPrice.period == period,
        ).delete(synchronize_session=False)
    elif from_date is not None:
        anchor = (
            db.query(DividendAdjustedPrice)
            .filter(
                DividendAdjustedPrice.instrument_id == instrument_id,
                DividendAdjustedPrice.period == period,
                DividendAdjustedPrice.date < from_date,
            )
            .order_by(DividendAdjustedPrice.date.desc())
            .first()
        )
        db.query(DividendAdjustedPrice).filter(
            DividendAdjustedPrice.instrument_id == instrument_id,
            DividendAdjustedPrice.period == period,
            DividendAdjustedPrice.date >= from_date,
        ).delete(synchronize_session=False)

        start_idx = bisect.bisect_left(dates, from_date)
        if anchor is not None:
            prev_index = float(anchor.adj_close)
            prev_close = close_by_date.get(anchor.date)
    else:
        last = (
            db.query(DividendAdjustedPrice)
            .filter(
                DividendAdjustedPrice.instrument_id == instrument_id,
                DividendAdjustedPrice.period == period,
            )
            .order_by(DividendAdjustedPrice.date.desc())
            .first()
        )
        if last is not None:
            start_idx = bisect.bisect_right(dates, last.date)
            prev_index = float(last.adj_close)
            prev_close = close_by_date.get(last.date)

    if start_idx >= len(prices):
        return  # 새로 계산할 날짜 없음

    rows = []
    for i in range(start_idx, len(prices)):
        d = dates[i]
        c = close_by_date[d]
        div_entries = dividend_lookup.get(d if period == "D" else (d.year, d.month), [])

        # 가격변동분(수정종가 기준, 분할 등은 이미 반영돼 있음)과 배당수익률분을
        # 독립적으로 곱한다. 배당수익률은 "그 날의 실제(비조정) 종가" 기준으로 계산해야
        # 정확한데, 캐시된 실제종가가 없으면 수정종가로 대체한다 — 이 경우 아래 계산식은
        # 대수적으로 예전 방식(수정종가에 배당금을 더하는 것)과 동일한 값이 된다.
        div_factor = 1.0
        for amount, raw_close in div_entries:
            basis = raw_close if raw_close is not None else c
            if basis:
                div_factor *= 1 + amount / basis

        if prev_index is None:
            # 이 종목(기간)의 최초 계산 시작일 = 지수 100
            idx_value = 100.0
        elif prev_close:
            idx_value = prev_index * (c / prev_close) * div_factor
        else:
            idx_value = prev_index  # 방어적 처리 (직전 종가가 0/결측인 이상치)

        rows.append(dict(instrument_id=instrument_id, date=d, period=period, adj_close=round(idx_value, 4)))
        prev_index = idx_value
        prev_close = c

    batch_size = 5000
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        stmt = pg_insert(DividendAdjustedPrice).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "period"],
            set_={"adj_close": stmt.excluded.adj_close},
        )
        db.execute(stmt)
