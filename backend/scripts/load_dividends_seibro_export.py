"""SEIBRO '배당내역상세' 수동 내보내기 파일을 dividends에 적재한다 (과거 백필용).

load_dividends_seibro.py는 Playwright로 최근 1주일을 긁는 일일 배치이고, 이 스크립트는
사용자가 SEIBRO 화면에서 직접 내려받은 파일을 넣는 용도다.

**파일 형식 주의**: 확장자가 .xls지만 실제로는 euc-kr HTML 테이블이다(엑셀이 열어줄 뿐).
pandas.read_html(encoding="euc-kr")로 읽는다. 헤더가 2단(MultiIndex)이라 열 이름이
불안정해서 일일 배치와 동일하게 **열 위치**로 뽑는다:
  0=배정기준일 1=현금배당지급일 4=종목코드 5=종목명 7=배당구분 9=주식종류 10=주당배당금(일반)

**ex_date 컬럼에는 배정기준일을 그대로 넣는다.** 실제 배당락 반영일(배정기준일의 1거래일
전)은 derived_prices._dividend_effective_date가 계산한다 — 여기서 미리 당기면 이중 보정이 된다.

적재 대상 필터:
  - 배당구분이 현금배당/동시배당인 것만 (무배당·주식배당 제외 — 현금 배당액이 없다)
  - 주당배당금 > 0
  - 우선주 제외 (종목코드 끝자리 != '0', instrument_rules.is_common_stock)

**SEIBRO 내보내기는 10,000행에서 잘린다.** 파일이 정확히 10,000행이면 구간을 쪼개서
다시 받아야 한다고 경고한다(정렬이 배정기준일 내림차순이라 과거쪽이 잘린다).

가격이 아직 백필되지 않은 구간이면 배당조정 재계산은 의미가 없으므로 하지 않는다.
가격 적재 후 backfill_dividend_adjusted_prices.py를 돌릴 것.

사용법:
  python scripts/load_dividends_seibro_export.py "../reference/배당내역상세 (9).xls" --dry-run
  python scripts/load_dividends_seibro_export.py "../reference/배당내역상세 (9).xls"
"""
import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.instrument_rules import is_common_stock  # noqa: E402
from app.services.upload_service import _upsert_dividend  # noqa: E402

SEIBRO_ROW_LIMIT = 10_000
CASH_KINDS = {"현금배당", "동시배당"}


def read_export(path: str) -> pd.DataFrame:
    warnings.filterwarnings("ignore")
    raw = pd.read_html(path, encoding="euc-kr")[0]
    df = pd.DataFrame(
        {
            "ex_date": raw.iloc[:, 0],
            "pay_date": raw.iloc[:, 1],
            "ticker": raw.iloc[:, 4].astype(str).str.strip(),
            "name": raw.iloc[:, 5].astype(str).str.strip(),
            "kind": raw.iloc[:, 7].astype(str).str.strip(),
            "share_type": raw.iloc[:, 9].astype(str).str.strip(),
            "amount": raw.iloc[:, 10],
        }
    )
    return df[df["ticker"].str.match(r"^\d")]


def main(path: str, dry_run: bool):
    df = read_export(path)
    total = len(df)
    print(f"파일 행수: {total:,}")
    if total >= SEIBRO_ROW_LIMIT:
        print(
            f"  경고: SEIBRO 내보내기 상한({SEIBRO_ROW_LIMIT:,}행)에 걸렸습니다. 정렬이 배정기준일\n"
            f"        내림차순이라 **과거 구간이 잘려 있습니다** — 기간을 쪼개서 추가로 받아야 합니다."
        )

    dates = pd.to_numeric(df["ex_date"], errors="coerce")
    print(f"  배정기준일 범위: {int(dates.min())} ~ {int(dates.max())}")

    amount = pd.to_numeric(df["amount"], errors="coerce")
    keep = df[df["kind"].isin(CASH_KINDS) & (amount > 0) & df["ticker"].map(is_common_stock)].copy()
    print(f"\n적재 대상(현금배당·보통주·배당금>0): {len(keep):,}행 / 종목 {keep['ticker'].nunique():,}개")
    print(f"  제외: 무배당·주식배당 {(~df['kind'].isin(CASH_KINDS)).sum():,}, "
          f"배당금 0 이하 {((amount <= 0) & df['kind'].isin(CASH_KINDS)).sum():,}, "
          f"우선주 {(~df['ticker'].map(is_common_stock)).sum():,}")

    db = SessionLocal()
    known = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    unknown = sorted(set(keep.loc[~keep["ticker"].isin(known), "ticker"]))
    if unknown:
        print(f"  instruments에 없는 종목 {len(unknown)}개 — 신규 등록됨: {unknown[:10]}")

    if dry_run:
        print("\n--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return

    upserted, errors = 0, []
    for _, row in keep.iterrows():
        try:
            with db.begin_nested():
                _upsert_dividend(db, row, known)
            upserted += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row['ticker']} {row['name']}: {exc}")
    db.commit()

    print(f"\n적재 {upserted:,}건, 실패 {len(errors)}건")
    for e in errors[:10]:
        print(f"  {e}")
    from sqlalchemy import text

    lo, hi, n = db.execute(text("select min(ex_date), max(ex_date), count(*) from dividends")).fetchone()
    print(f"dividends 현황: {lo} ~ {hi}, {n:,}건")
    print("\n가격 백필이 끝난 뒤 backfill_dividend_adjusted_prices.py로 배당조정을 다시 계산할 것.")
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    main(a.path, a.dry_run)
