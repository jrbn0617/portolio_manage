"""WISEfn/DataGuide 월간 벌크 엑셀(예: reference/monthly_data_response_*.xlsx)을
monthly_fundamentals 테이블로 적재한다. backend/scripts/load_monthly_fundamentals.py,
load_factor_fundamentals.py의 파싱 로직을 API 업로드 경로로 옮긴 것 — 시트 레이아웃과
필드/metric 매핑은 두 스크립트와 동일하다.

파일 형식: 한 파일 안에 여러 필드가 각각 "<필드명 접두어><번호>" 시트로 나뉜 벌크 포맷.
행 7(0-idx)=Code("A"+티커), 행 14(0-idx)부터 날짜별 데이터.
"""
from io import BytesIO

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.instrument import Instrument
from app.models.monthly_fundamental import MonthlyFundamental
from app.schemas.monthly_fundamental import (
    MonthlyFundamentalBulkUploadMetricResult,
    MonthlyFundamentalBulkUploadResult,
)

CODE_ROW = 7
DATA_START_ROW = 14
BATCH_SIZE = 5000

# (시트명 접두어, metric명, 단위 환산 배율) — reference/monthly_data_template.xlsx 및
# scripts/generate_monthly_data_request_template.py의 ITEM_SPECS와 동일한 4개 항목.
FIELD_METRIC_MAP: list[tuple[str, str, float]] = [
    ("유동비율", "free_float_ratio", 1.0),
    ("EBITDA(TTM)", "ebitda_ttm", 1000.0),
    ("EBITDA(Fwd.12M)", "ebitda_fwd_12m", 1000.0),
    ("EV EBITDA(Fwd.12M)", "ev_ebitda_fwd_12m", 1.0),
]


def _clean_ticker(code) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def _parse_field_long(xl: pd.ExcelFile, sheets: list[str], scale: float) -> pd.DataFrame:
    frames = []
    for sheet in sheets:
        raw = xl.parse(sheet, header=None)
        codes = [_clean_ticker(c) for c in raw.iloc[CODE_ROW, 1:]]
        block = raw.iloc[DATA_START_ROW:, :].copy()
        block.columns = ["date"] + codes
        block["date"] = pd.to_datetime(block["date"], errors="coerce")
        block = block.dropna(subset=["date"])
        long = block.melt(id_vars="date", var_name="ticker", value_name="value")
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        long = long.dropna(subset=["value"])
        long["value"] = long["value"] * scale
        frames.append(long)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "ticker", "value"])


def process_monthly_fundamental_bulk_upload(
    db: Session, file_name: str, raw: bytes
) -> MonthlyFundamentalBulkUploadResult:
    xl = pd.ExcelFile(BytesIO(raw))
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    metric_results: list[MonthlyFundamentalBulkUploadMetricResult] = []
    errors: list[str] = []
    total_rows = 0

    for prefix, metric, scale in FIELD_METRIC_MAP:
        sheets = [s for s in xl.sheet_names if s.startswith(prefix)]
        if not sheets:
            errors.append(f"'{prefix}' 로 시작하는 시트를 찾지 못해 건너뜀")
            continue

        df = _parse_field_long(xl, sheets, scale)
        df = df.drop_duplicates(subset=["ticker", "date"], keep="first")

        unknown_tickers = set(df["ticker"].unique()) - set(instruments_by_ticker)
        if unknown_tickers:
            errors.append(
                f"{metric}: instruments에 없는 티커 {len(unknown_tickers)}건은 건너뜀 "
                f"(예: {sorted(unknown_tickers)[:10]})"
            )
            df = df[~df["ticker"].isin(unknown_tickers)]

        rows = [
            dict(
                instrument_id=instruments_by_ticker[r.ticker],
                date=r.date.date(),
                metric=metric,
                value=float(r.value),
            )
            for r in df.itertuples(index=False)
        ]

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            stmt = pg_insert(MonthlyFundamental).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "date", "metric"], set_={"value": stmt.excluded.value}
            )
            db.execute(stmt)
        db.commit()

        metric_results.append(
            MonthlyFundamentalBulkUploadMetricResult(
                metric=metric, sheets=len(sheets), rows=len(rows), unknown_tickers=len(unknown_tickers)
            )
        )
        total_rows += len(rows)

    if not metric_results:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "success"

    return MonthlyFundamentalBulkUploadResult(
        status=status, file_name=file_name, total_rows=total_rows, metrics=metric_results, errors=errors
    )
