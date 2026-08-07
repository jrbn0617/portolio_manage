"""TradingView 스크리너에서 CSV로 받은 국내 종목 섹터/산업 분류를 instruments 테이블에 적재한다.
CSV 컬럼: 심볼, 설명, ..., 섹터, 산업, ... (헤더에 이 이름 그대로 존재해야 함)

사용법: python scripts/load_sector_industry.py [csv_path]
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402


def main(path: str):
    print(f"reading {path} ...")
    df = pd.read_csv(path, dtype=str)
    missing = {"심볼", "설명", "섹터", "산업"} - set(df.columns)
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    created = 0
    updated = 0
    for row in df.itertuples(index=False):
        ticker = row.심볼.strip()
        sector = row.섹터.strip() if pd.notna(row.섹터) else None
        industry = row.산업.strip() if pd.notna(row.산업) else None
        name = row.설명.strip() if pd.notna(row.설명) else ticker

        if ticker not in instruments_by_ticker:
            instrument = Instrument(ticker=ticker, name=name, asset_type="stock", sector=sector, industry=industry)
            db.add(instrument)
            db.flush()
            instruments_by_ticker[ticker] = instrument.id
            created += 1
        else:
            db.query(Instrument).filter(Instrument.id == instruments_by_ticker[ticker]).update(
                {"sector": sector, "industry": industry}
            )
            updated += 1

    db.commit()
    print(f"신규 등록 {created}건, 섹터/산업 갱신 {updated}건")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/산업섹터.csv"
    main(target)
