"""EV/EBITDA(Fwd.12M) 스크리닝 팩터용 재무/컨센서스 데이터를 reference/팩터.xlsx에서
monthly_fundamentals에 적재한다. 새 테이블 없이 기존 EAV 테이블에 metric만 추가한다.

파일 형식: 한 파일 안에 3개 필드가 각각 시트 "필드명 1".."필드명 5"로 쪼개져 있는
WISEfn 벌크 포맷(load_monthly_fundamentals.py와 같은 행 레이아웃 — 8행=Code,
9행=Name, 14행=필드라벨, 15행부터 데이터).

단위 변환: EBITDA(TTM)/EBITDA(Fwd.12M) 원본 단위는 "천원"이라, 시가총액 등 나머지
DB 데이터와 단위를 맞추기 위해 ×1000 해서 "원" 단위로 저장한다. EV/EBITDA(Fwd.12M)은
배수(단위 없음)라 그대로 저장.

주의(point-in-time): EBITDA(TTM)은 검증 결과 같은 분기 내 거의 전종목이 동시에
(분기말+1개월 시점) 일괄 갱신되는 패턴이 확인됐다 — 실제 공시일 기준이 아니라 사후에
재구성된 값일 가능성이 있다. 여기서는 원본 날짜를 그대로 저장하고, 스크리닝 엔진에서
조회할 때 별도 lag 파라미터를 적용해야 한다(저장 단계에서 임의로 날짜를 밀지 않음).
EBITDA(Fwd.12M) 컨센서스는 갱신 시점이 종목별로 분산되어 있어 point-in-time에 더
가까워 보이지만, 그래도 안전하게 lag를 적용하는 걸 권장.

사용법: python scripts/load_factor_fundamentals.py [../reference/팩터.xlsx]
"""
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

# 필드 시트명 접두어 -> (metric명, 원 단위 변환 배율)
FIELD_METRIC_MAP = {
    "EBITDA(TTM)": ("ebitda_ttm", 1000.0),
    "EBITDA(Fwd.12M)": ("ebitda_fwd_12m", 1000.0),
    "EV EBITDA(Fwd.12M)": ("ev_ebitda_fwd_12m", 1.0),
}


def _clean_ticker(code) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def _load_field_long(path: str, sheet_names: list[str], metric: str, scale: float) -> pd.DataFrame:
    frames = []
    for sheet in sheet_names:
        raw = pd.read_excel(path, sheet_name=sheet, header=None)
        codes = [_clean_ticker(c) for c in raw.iloc[CODE_ROW, 1:]]
        block = raw.iloc[DATA_START_ROW:, :].copy()
        block.columns = ["date"] + codes
        block["date"] = pd.to_datetime(block["date"], errors="coerce")
        block = block.dropna(subset=["date"])
        long = block.melt(id_vars="date", var_name="ticker", value_name="value")
        # "적전(N)"(적자전환)/"N/A" 같은 텍스트 플레이스홀더는 결측으로 처리 —
        # EBITDA(Fwd)가 음수라 배수 자체가 의미 없어지는 경우라 그냥 버린다.
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        long = long.dropna(subset=["value"])
        long["value"] = long["value"] * scale
        long["metric"] = metric
        frames.append(long)
    return pd.concat(frames, ignore_index=True)


def main(path: str):
    xl = pd.ExcelFile(path)
    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    for field_prefix, (metric, scale) in FIELD_METRIC_MAP.items():
        sheets = [s for s in xl.sheet_names if s.startswith(field_prefix)]
        if not sheets:
            print(f"경고: '{field_prefix}' 로 시작하는 시트를 찾지 못함 — 건너뜀")
            continue
        print(f"{metric} ({field_prefix}): 시트 {sheets}")
        df = _load_field_long(path, sheets, metric, scale)
        print(f"  파싱된 행수: {len(df)}")

        dup = df.duplicated(subset=["ticker", "date"]).sum()
        if dup:
            print(f"  중복 (ticker,date) {dup}건 — 첫 값만 남기고 제거")
            df = df.drop_duplicates(subset=["ticker", "date"], keep="first")

        unknown = set(df["ticker"].unique()) - set(instruments_by_ticker)
        if unknown:
            print(f"  경고: instruments에 없는 티커 {len(unknown)}건은 건너뜀 (예: {sorted(unknown)[:10]})")
            df = df[~df["ticker"].isin(unknown)]

        rows = [
            dict(instrument_id=instruments_by_ticker[r.ticker], date=r.date.date(), metric=r.metric, value=float(r.value))
            for r in df.itertuples(index=False)
        ]

        print(f"  upserting {len(rows)}행 ...")
        batch_size = 5000
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            stmt = pg_insert(MonthlyFundamental).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "date", "metric"], set_={"value": stmt.excluded.value}
            )
            db.execute(stmt)
        db.commit()
        print(f"  {metric} 완료")

    db.close()
    print("\n전체 완료")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/팩터.xlsx"
    main(target)
