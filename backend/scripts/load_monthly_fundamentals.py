"""월 단위 종목 펀더멘털(유동비율, 상장주식수 등)을 WISEfn 벌크 엑셀에서
monthly_fundamentals 테이블로 적재한다. metric 인자로 항목을 구분해서
같은 스크립트로 앞으로 추가될 다른 월간 지표도 그대로 적재할 수 있다.

파일 형식: 여러 시트("1","2","3"...)로 나뉜 단일필드 월간 시계열.
3행=Frequency(M), 7행=Code, 8행=Name, 13행=필드라벨, 14행부터 데이터.

사용법:
  python scripts/load_monthly_fundamentals.py "../reference/유동비율.xlsx" free_float_ratio
  python scripts/load_monthly_fundamentals.py "../reference/상장주식수.xlsx" shares_outstanding_monthly
"""
import re
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.models.instrument import Instrument
from app.models.monthly_fundamental import MonthlyFundamental

CODE_ROW = 7
NAME_ROW = 8
DATA_START_ROW = 14

# DataGuide는 값 대신 한글 상태표시를 넣고 실제 수치를 괄호로 붙여 보내는 경우가 있다
# (예: `적전(2.96000)`). 괄호 안 숫자를 값으로 쓴다.
#
# **`적전`을 액면 그대로 "적자전환"으로 읽으면 안 된다.** 2026-08-13 실측:
# EV EBITDA(Fwd.12M) 2014~2018 응답에서 13,025셀이 이 표기였는데, 2017-12 시점
# 삼성전자·SK하이닉스가 여기 들어 있다 — 창사 이래 최대 실적을 낸 해다. 통계로도
# 적전 그룹의 96%가 EBITDA TTM>0 & Fwd>0이고 EBITDA 증가율 분포가 숫자 그룹과 같다.
# 명단(삼성전자·SK하이닉스·현대모비스·NAVER = 순현금 / 현대차·SKT·LG화학 = 순차입금)과
# `파일값 ÷ (시가총액÷EBITDA)` 중앙값(적전 0.82 vs 숫자 1.32 = EV가 시총보다 작다)으로
# 볼 때 **순차입금이 음수(순현금)라 EV 구성요소가 뒤집혔다는 표시**로 해석했다.
# 괄호 안 값은 정상 배수다(삼성전자 2.96, NAVER 14.43 — 그 시점 실제 배수와 부합).
# 다만 2019-01부터 이 표기가 연 2,800건에서 11건으로 끊기는 이유는 설명하지 못했다.
# **미확인 가정이며 docs/algorithms/methodology.md 4.1절에 기록돼 있다.**
_MARKER_RE = re.compile(r"^[^\d()+-]*\(\s*(-?[\d,]*\.?\d+)\s*\)$")


def _clean_ticker(code) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def _to_number(v):
    """숫자면 그대로, `표시(숫자)` 형태면 괄호 안 숫자, 그 외(N/A 등)는 None.

    한 컬럼에 숫자와 문자열이 섞이면 pandas가 컬럼 전체를 object로 읽어 숫자도
    문자열로 들어온다 — 그래서 float() 먼저 시도한다.
    """
    if isinstance(v, (int, float)):
        return None if v != v else float(v)  # NaN 제외
    s = str(v).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        pass
    m = _MARKER_RE.match(s)
    return float(m.group(1)) if m else None


def _load_all_sheets_long(path: str, column_name: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    frames = []
    for sheet in xl.sheet_names:
        raw = pd.read_excel(path, sheet_name=sheet, header=None)
        codes = [_clean_ticker(c) for c in raw.iloc[CODE_ROW, 1:]]
        block = raw.iloc[DATA_START_ROW:, :].copy()
        block.columns = ["date"] + codes
        block["date"] = pd.to_datetime(block["date"], errors="coerce")
        block = block.dropna(subset=["date"])
        long = block.melt(id_vars="date", var_name="ticker", value_name=column_name)
        long = long.dropna(subset=[column_name])
        frames.append(long)
    return pd.concat(frames, ignore_index=True)


def main(path: str, metric: str):
    print(f"reading {path} (metric={metric!r}) ...")
    df = _load_all_sheets_long(path, "value")
    print(f"파싱된 행수: {len(df)}")

    raw_is_num = df["value"].map(lambda v: isinstance(v, (int, float)))
    df["value"] = df["value"].map(_to_number)
    dropped = df["value"].isna().sum()
    marker = int((~raw_is_num & df["value"].notna()).sum())
    if marker or dropped:
        print(f"  숫자 {int(raw_is_num.sum()):,} · 상태표시에서 값 추출 {marker:,} · 해석불가 폐기 {dropped:,}")
    df = df.dropna(subset=["value"])

    dup = df.duplicated(subset=["ticker", "date"]).sum()
    if dup:
        print(f"중복 (ticker,date) {dup}건 발견 — 첫 값만 남기고 제거")
        df = df.drop_duplicates(subset=["ticker", "date"], keep="first")

    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    unknown = set(df["ticker"].unique()) - set(instruments_by_ticker)
    if unknown:
        print(f"경고: instruments에 없는 티커 {len(unknown)}건은 건너뜁니다: {sorted(unknown)[:10]}...")
        df = df[~df["ticker"].isin(unknown)]

    rows = [
        dict(instrument_id=instruments_by_ticker[r.ticker], date=r.date.date(), metric=metric, value=float(r.value))
        for r in df.itertuples(index=False)
    ]

    print(f"upserting {len(rows)}행 ...")
    batch_size = 5000
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        stmt = pg_insert(MonthlyFundamental).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "metric"], set_={"value": stmt.excluded.value}
        )
        db.execute(stmt)
        db.commit()
        print(f"  {min(i + batch_size, len(rows))}/{len(rows)}")

    print("done.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("사용법: python scripts/load_monthly_fundamentals.py <경로> <metric명>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
