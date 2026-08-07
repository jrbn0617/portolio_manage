import re
from datetime import date, datetime
from io import BytesIO, StringIO

import pandas as pd
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.dividend import Dividend
from app.models.dividend_adjusted_price import DividendAdjustedPrice
from app.models.index_membership import IndexMembership
from app.models.instrument import Instrument
from app.models.macro_indicator import MacroIndicator
from app.models.market_holiday import MarketHoliday
from app.models.price import Price
from app.models.raw_close import RawClose
from app.models.upload_batch import UploadBatch
from app.services.derived_prices import recompute_dividend_adjusted, recompute_monthly_bar

REQUIRED_COLUMNS = {
    "prices": {"ticker", "date", "period", "close"},
    "dividends": {"ticker", "ex_date", "amount"},
    "macro": {"indicator_name", "date", "value"},
    "index_memberships": {"ticker", "index_name", "as_of_date"},
    "raw_closes": {"ticker", "date", "close"},
}

# 데이터 타입별 내부 필드명 -> 허용되는 컬럼명(별칭) 목록.
# 새로운 출처의 파일을 지원하려면 여기에 별칭만 추가하면 된다.
COLUMN_ALIASES: dict[str, dict[str, list[str]]] = {
    "prices": {
        "ticker": ["ticker", "종목코드"],
        "name": ["name", "종목명"],
        "market": ["market", "시장구분"],
        "date": ["date", "일자", "날짜"],
        "period": ["period", "주기"],
        "open": ["open", "시가"],
        "high": ["high", "고가"],
        "low": ["low", "저가"],
        "close": ["close", "종가"],
        "volume": ["volume", "거래량"],
    },
    "dividends": {
        "ticker": ["ticker", "종목코드"],
        "name": ["name", "종목명"],
        "market": ["market", "시장구분"],
        "ex_date": ["ex_date", "배정기준일"],
        "pay_date": ["pay_date", "현금배당 지급일", "현금배당지급일"],
        "amount": ["amount", "주당배당금", "주당배당금 일반"],
    },
    "macro": {
        "indicator_name": ["indicator_name", "지표명"],
        "date": ["date", "일자", "날짜"],
        "value": ["value", "값"],
    },
    "index_memberships": {
        "ticker": ["ticker", "종목코드"],
        "name": ["name", "종목명"],
        "index_name": ["index_name", "지수명", "구분"],
        "as_of_date": ["as_of_date", "기준일"],
    },
    "raw_closes": {
        "ticker": ["ticker", "종목코드"],
        "date": ["date", "일자", "날짜"],
        "close": ["close", "종가"],
    },
}

# 종목코드(예: 005930)처럼 앞자리 0이 의미를 갖는 컬럼은 숫자로 추론되면 안 되므로
# 항상 문자열로 강제 파싱한다.
STRING_COLUMNS = {"ticker", "indicator_name"}

TEXT_ENCODINGS = ["utf-8", "cp949", "euc-kr"]


def _decode_text(raw: bytes) -> str:
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    new_columns = []
    for col in df.columns:
        parts = col if isinstance(col, tuple) else (col,)
        flat: list[str] = []
        for part in parts:
            text = str(part).strip()
            if not text or text.lower() == "nan":
                continue
            if not flat or flat[-1] != text:
                flat.append(text)
        new_columns.append(" ".join(flat))
    df.columns = new_columns
    return df


OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _read_raw(raw: bytes, string_positions: set[int] | None = None) -> pd.DataFrame:
    # 진짜 Excel 파일(xlsx/xls)은 매직 바이트로 식별한다. 확장자만으로는
    # 이 프로젝트에서 다루는 파일들(예: 증권사/거래소가 .xls로 내보내지만
    # 실제로는 HTML인 파일)을 제대로 구분할 수 없다.
    dtype = {pos: str for pos in string_positions} if string_positions else None

    if raw.startswith(b"PK\x03\x04"):
        df = pd.read_excel(BytesIO(raw), engine="openpyxl", dtype=dtype)
    elif raw.startswith(OLE2_MAGIC):
        df = pd.read_excel(BytesIO(raw), engine="xlrd", dtype=dtype)
    else:
        text = _decode_text(raw)
        if re.search(r"<table", text, re.IGNORECASE):
            tables = pd.read_html(StringIO(text), converters=dtype)
            if not tables:
                raise ValueError("파일에서 표를 찾을 수 없습니다.")
            df = tables[0]
        else:
            df = pd.read_csv(StringIO(text), dtype=dtype)

    return _flatten_columns(df)


def _build_rename_map(df: pd.DataFrame, data_type: str) -> dict[str, str]:
    aliases = COLUMN_ALIASES[data_type]
    rename_map: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for col in df.columns:
            if str(col).strip() in candidates and col not in rename_map:
                rename_map[col] = canonical
                break
    return rename_map


def _read_table(raw: bytes, data_type: str) -> pd.DataFrame:
    # 1차 읽기: 컬럼 구조(순서/이름)만 파악한다. ticker처럼 문자열로 강제해야 하는
    # 컬럼이 이 단계에서 숫자로 잘못 추론돼도(예: 005930 -> 5930) 상관없다 —
    # 위치만 알아내면 2차 읽기에서 해당 위치를 문자열로 고정해 다시 읽는다.
    df = _read_raw(raw)
    rename_map = _build_rename_map(df, data_type)

    string_positions = {
        pos
        for pos, col in enumerate(df.columns)
        if rename_map.get(col, col) in STRING_COLUMNS
    }

    if string_positions:
        df = _read_raw(raw, string_positions=string_positions)
        # 컬럼 순서/이름은 재파싱해도 동일하므로 같은 매핑을 재사용한다.

    return df.rename(columns=rename_map)


def process_upload(db: Session, data_type: str, file: UploadFile, raw: bytes) -> UploadBatch:
    if data_type not in REQUIRED_COLUMNS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 data_type: {data_type}")

    try:
        df = _read_table(raw, data_type)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"파일을 읽을 수 없습니다: {exc}") from exc

    missing = REQUIRED_COLUMNS[data_type] - set(df.columns)
    if missing:
        raise HTTPException(status_code=400, detail=f"필수 컬럼 누락: {sorted(missing)}")

    instruments_by_ticker: dict[str, int] = {}
    if data_type in ("prices", "dividends", "index_memberships", "raw_closes"):
        instruments_by_ticker = {
            ticker: iid for ticker, iid in db.query(Instrument.ticker, Instrument.id).all()
        }

    # 원본 파일(WISEfn 등)이 휴장일도 직전 거래일 값으로 채워 내보내는 경우가 있어,
    # 일봉 업로드 시 실제 KRX 휴장일에 해당하는 행은 걸러낸다.
    holidays: set[date] = set()
    if data_type == "prices":
        holidays = {r[0] for r in db.query(MarketHoliday.date).all()}

    row_count = 0
    errors: list[str] = []
    touched_months: set[tuple[int, int, int]] = set()  # (instrument_id, year, month)
    touched_price_instruments: set[int] = set()
    touched_price_min_date: dict[int, date] = {}
    touched_dividend_min_exdate: dict[int, date] = {}
    touched_raw_close_instruments: set[int] = set()

    for idx, row in df.iterrows():
        excel_row = idx + 2  # 헤더 포함 1-indexed 위치
        try:
            # 개별 행을 SAVEPOINT로 격리한다. 한 행이 DB 제약(예: NOT NULL)을
            # 위반해도 트랜잭션 전체가 중단되지 않고, 나머지 행은 계속 처리된다.
            with db.begin_nested():
                if data_type == "prices":
                    instrument_id, period, price_date = _upsert_price(db, row, instruments_by_ticker, holidays)
                    touched_price_instruments.add(instrument_id)
                    prev_min = touched_price_min_date.get(instrument_id)
                    if prev_min is None or price_date < prev_min:
                        touched_price_min_date[instrument_id] = price_date
                    if period == "D":
                        touched_months.add((instrument_id, price_date.year, price_date.month))
                elif data_type == "dividends":
                    instrument_id, ex_date = _upsert_dividend(db, row, instruments_by_ticker)
                    prev_min = touched_dividend_min_exdate.get(instrument_id)
                    if prev_min is None or ex_date < prev_min:
                        touched_dividend_min_exdate[instrument_id] = ex_date
                elif data_type == "index_memberships":
                    _upsert_index_membership(db, row, instruments_by_ticker)
                elif data_type == "raw_closes":
                    instrument_id = _upsert_raw_close(db, row, instruments_by_ticker)
                    touched_raw_close_instruments.add(instrument_id)
                else:
                    _upsert_macro(db, row)
            row_count += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"row {excel_row}: {exc}")

    db.commit()

    # 파생 데이터(월봉, 배당조정 수정주가 지수) 재계산 — 영향받은 범위만.
    for instrument_id, year, month in touched_months:
        with db.begin_nested():
            recompute_monthly_bar(db, instrument_id, year, month)
    db.commit()

    # dividends로 영향받은 종목은 소급 정정일(from_date)부터, prices만 영향받은
    # 종목은 증분(기본)으로 재계산한다. 둘 다 해당하면 from_date 쪽이 신규 가격
    # 구간까지 포함해 항상 더 넓은 범위를 커버하므로 그쪽만 수행한다.
    for instrument_id, min_exdate in touched_dividend_min_exdate.items():
        with db.begin_nested():
            recompute_dividend_adjusted(db, instrument_id, from_date=min_exdate)
        touched_price_instruments.discard(instrument_id)
    db.commit()

    for instrument_id in touched_price_instruments:
        new_min_date = touched_price_min_date.get(instrument_id)
        force_full = False
        if new_min_date is not None:
            existing_min = (
                db.query(func.min(DividendAdjustedPrice.date))
                .filter(
                    DividendAdjustedPrice.instrument_id == instrument_id,
                    DividendAdjustedPrice.period == "D",
                )
                .scalar()
            )
            if existing_min is not None and new_min_date < existing_min:
                force_full = True
        with db.begin_nested():
            recompute_dividend_adjusted(db, instrument_id, force_full=force_full)
    db.commit()

    # 실제(비조정) 종가가 새로 들어오면 배당조정 지수 계산 정확도가 달라지므로
    # 해당 종목만 처음부터 다시 계산한다.
    for instrument_id in touched_raw_close_instruments:
        with db.begin_nested():
            recompute_dividend_adjusted(db, instrument_id, force_full=True)
    db.commit()

    status = "success" if not errors else ("failed" if row_count == 0 else "partial")
    batch = UploadBatch(
        data_type=data_type,
        file_name=file.filename or "unknown",
        row_count=row_count,
        error_count=len(errors),
        status=status,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    batch.errors = errors[:50]  # type: ignore[attr-defined]
    return batch


def _get_or_create_instrument(db: Session, row, instruments_by_ticker: dict[str, int]) -> int:
    ticker = _clean_ticker(row["ticker"])
    if not ticker or ticker.lower() in _MISSING_TOKENS:
        raise ValueError("ticker 값이 비어 있습니다.")

    name = row.get("name")
    market = row.get("market")
    name = str(name).strip() if pd.notna(name) else None
    market = str(market).strip() if pd.notna(market) else None

    if ticker in instruments_by_ticker:
        instrument_id = instruments_by_ticker[ticker]
        # 이미 등록된 종목이라도 파일에 이름/시장구분이 있으면 최신 값으로 갱신한다.
        if name or market:
            update_values = {}
            if name:
                update_values["name"] = name
            if market:
                update_values["market"] = market
            db.execute(update(Instrument).where(Instrument.id == instrument_id).values(**update_values))
        return instrument_id

    # 아직 종목마스터에 없는 ticker는 자동으로 등록한다 (데이터를 채워가는 단계이므로
    # 업로드 파일에 있는 종목을 건너뛰지 않고 최대한 반영한다).
    instrument = Instrument(
        ticker=ticker,
        name=name or ticker,
        asset_type="stock",
        market=market,
    )
    db.add(instrument)
    db.flush()
    instruments_by_ticker[ticker] = instrument.id
    return instrument.id


def _clean_ticker(value) -> str:
    text = str(value).strip()
    # 숫자로 잘못 추론되어 "5930.0" 처럼 들어온 경우 소수부를 제거한다.
    # (원래 문자열이었다면 이 분기를 타지 않으므로 앞자리 0 손실과는 무관)
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def _upsert_price(
    db: Session, row, instruments_by_ticker: dict[str, int], holidays: set[date] | None = None
) -> tuple[int, str, date]:
    instrument_id = _get_or_create_instrument(db, row, instruments_by_ticker)
    period = str(row["period"]).strip().upper()
    if period not in ("D", "M"):
        raise ValueError(f"period는 D 또는 M 이어야 합니다: {row['period']}")

    price_date = _parse_date(row["date"])
    if price_date is None:
        raise ValueError(f"날짜를 해석할 수 없습니다: {row['date']}")

    if period == "D" and holidays and price_date in holidays:
        raise ValueError(f"휴장일이라 건너뜀: {price_date}")

    values = dict(
        instrument_id=instrument_id,
        date=price_date,
        period=period,
        open=_to_float(row.get("open")),
        high=_to_float(row.get("high")),
        low=_to_float(row.get("low")),
        close=_to_float(row["close"]),
        volume=_to_int(row.get("volume")),
    )
    stmt = pg_insert(Price).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "date", "period"],
        set_={k: v for k, v in values.items() if k not in ("instrument_id", "date", "period")},
    )
    db.execute(stmt)
    return instrument_id, period, price_date


def _upsert_dividend(db: Session, row, instruments_by_ticker: dict[str, int]) -> tuple[int, date]:
    instrument_id = _get_or_create_instrument(db, row, instruments_by_ticker)

    ex_date = _parse_date(row["ex_date"])
    if ex_date is None:
        raise ValueError(f"배정기준일(ex_date)을 해석할 수 없습니다: {row['ex_date']}")

    # 무배당(배당 없음) 등으로 금액이 비어 있는 행도 유효한 데이터이므로 0으로 적재한다.
    amount = _to_float(row["amount"])
    values = dict(
        instrument_id=instrument_id,
        ex_date=ex_date,
        pay_date=_parse_date(row.get("pay_date")),
        amount=amount if amount is not None else 0.0,
    )
    stmt = pg_insert(Dividend).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "ex_date"],
        set_={k: v for k, v in values.items() if k not in ("instrument_id", "ex_date")},
    )
    db.execute(stmt)
    return instrument_id, ex_date
    return instrument_id


def _upsert_index_membership(db: Session, row, instruments_by_ticker: dict[str, int]) -> None:
    instrument_id = _get_or_create_instrument(db, row, instruments_by_ticker)

    as_of_date = _parse_date(row["as_of_date"])
    if as_of_date is None:
        raise ValueError(f"기준일(as_of_date)을 해석할 수 없습니다: {row['as_of_date']}")

    index_name = str(row["index_name"]).strip()
    if not index_name:
        raise ValueError("index_name 값이 비어 있습니다.")

    values = dict(index_name=index_name, as_of_date=as_of_date, instrument_id=instrument_id)
    stmt = pg_insert(IndexMembership).values(**values)
    # 편입 여부는 "존재하면 편입"이라는 의미만 가지므로, 같은 스냅샷을 다시 올려도
    # 갱신할 값이 없다 — 충돌 시 그냥 무시한다.
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["index_name", "as_of_date", "instrument_id"],
    )
    db.execute(stmt)


def _upsert_raw_close(db: Session, row, instruments_by_ticker: dict[str, int]) -> int:
    instrument_id = _get_or_create_instrument(db, row, instruments_by_ticker)

    close_date = _parse_date(row["date"])
    if close_date is None:
        raise ValueError(f"날짜를 해석할 수 없습니다: {row['date']}")

    values = dict(instrument_id=instrument_id, date=close_date, close=_to_float(row["close"]))
    stmt = pg_insert(RawClose).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "date"],
        set_={"close": stmt.excluded.close},
    )
    db.execute(stmt)
    return instrument_id


def _upsert_macro(db: Session, row) -> None:
    indicator_date = _parse_date(row["date"])
    if indicator_date is None:
        raise ValueError(f"날짜를 해석할 수 없습니다: {row['date']}")

    values = dict(
        indicator_name=str(row["indicator_name"]).strip(),
        date=indicator_date,
        value=_to_float(row["value"]),
    )
    stmt = pg_insert(MacroIndicator).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["indicator_name", "date"],
        set_={"value": values["value"]},
    )
    db.execute(stmt)


_MISSING_TOKENS = {"", "-", "nan", "nat", "none", "null"}


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if text.lower() in _MISSING_TOKENS:
        return None

    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date()

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _to_float(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return float(value)


def _to_int(value) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return int(value)
