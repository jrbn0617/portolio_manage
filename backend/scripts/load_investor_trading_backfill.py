"""1회성 백필 응답(reference/backfill_data_response.xlsx)을 investor_trading 테이블에 적재한다.
generate_backfill_data_request_template.py로 만든 요청(기관/개인/외국인 일별 순매수대금,
2018-12-28~)에 대한 응답 파일을 그대로 적재하는 스크립트 — 정기 실행용이 아님.

행 레이아웃은 다른 WISEfn 벌크 파일과 동일(행8=Code, 행15부터 날짜별 데이터, 0-idx로는
CODE_ROW=7, DATA_START_ROW=14).

daily_update.py가 2026-08-04부터 pykrx로 investor_trading을 실시간 적재 중이라 이 백필과
날짜가 겹친다. 겹치는 구간은 buy/sell 분해까지 있는 pykrx 쪽 데이터가 더 상세하므로,
이미 존재하는 (instrument_id, date, investor_type) 조합은 건드리지 않고 건너뛴다
(ON CONFLICT DO NOTHING — upsert 아님).

사용법:
  python scripts/load_investor_trading_backfill.py [../reference/backfill_data_response.xlsx]
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.investor_trading import InvestorTrading  # noqa: E402

CODE_ROW = 7
DATA_START_ROW = 14
BATCH_SIZE = 5000
SCALE = 1_000_000.0  # Local mn(백만원) -> 원 (investor_trading.net_value와 단위 일치)

# 시트명 접두어(Item Code) -> investor_trading.investor_type (daily_update.py의 INVESTOR_TYPES와 동일 표기)
ITEM_CODE_TO_INVESTOR_TYPE = {
    "U110320": "기관합계",  # 기관 순매수대금(일간)
    "U140320": "개인",  # 개인 순매수대금(일간)
    "U130320": "외국인",  # 외국인총합계 순매수대금(일간)
}


def _clean_ticker(code) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def _parse_sheet_long(xl: pd.ExcelFile, sheet: str) -> pd.DataFrame:
    raw = xl.parse(sheet, header=None)
    codes = [_clean_ticker(c) for c in raw.iloc[CODE_ROW, 1:]]
    block = raw.iloc[DATA_START_ROW:, :].copy()
    block.columns = ["date"] + codes
    block["date"] = pd.to_datetime(block["date"], errors="coerce")
    block = block.dropna(subset=["date"])
    long = block.melt(id_vars="date", var_name="ticker", value_name="value")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.dropna(subset=["value"])
    return long.drop_duplicates(subset=["ticker", "date"], keep="first")


def main(path: str):
    xl = pd.ExcelFile(path)
    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    grand_total = 0
    for item_code, investor_type in ITEM_CODE_TO_INVESTOR_TYPE.items():
        sheets = [s for s in xl.sheet_names if s.startswith(item_code)]
        if not sheets:
            print(f"경고: '{item_code}' 로 시작하는 시트를 찾지 못함 — 건너뜀")
            continue
        print(f"{investor_type} ({item_code}): 시트 {sheets}")

        for sheet in sheets:
            long = _parse_sheet_long(xl, sheet)

            unknown = set(long["ticker"].unique()) - set(instruments_by_ticker)
            if unknown:
                print(f"  {sheet}: instruments에 없는 티커 {len(unknown)}건 건너뜀 (예: {sorted(unknown)[:5]})")
                long = long[~long["ticker"].isin(unknown)]

            rows = [
                dict(
                    instrument_id=instruments_by_ticker[r.ticker],
                    date=r.date.date(),
                    investor_type=investor_type,
                    net_value=round(r.value * SCALE),
                )
                for r in long.itertuples(index=False)
            ]

            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i : i + BATCH_SIZE]
                stmt = pg_insert(InvestorTrading).values(batch)
                stmt = stmt.on_conflict_do_nothing(index_elements=["instrument_id", "date", "investor_type"])
                db.execute(stmt)
            db.commit()

            grand_total += len(rows)
            print(f"  {sheet}: {len(rows)}행 처리 (누적 {grand_total})")

    db.close()
    print("완료.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/backfill_data_response.xlsx"
    main(target)
