"""펀드 마스터 백필 — funds 테이블.

세 소스를 합친다.
  1. KOFIA 공시목록 분류 (app.services.fund_classify) — 운용펀드/클래스 계층. **유일한 정답**
  2. 소스 DB의 fund_kr_kofia_master_fund_map — 최근 1년 공시가 없어 (1)에 안 잡히는 과거 펀드
  3. 소스 DB의 fund_kr_kofia_newly — 이름·운용사·설정일 등 속성, 그리고 (1)(2) 어디에도
     없는 신생 펀드(설정 후 1년 미만이라 자산운용보고서를 아직 안 냄)

**newly 로 부모를 추론하지 않는다.** 이름 접두어 매칭은 51.97%, `(모)` 접미사를 반영해도
55.05% 였다 — 클래스 이름끼리 서로 접두어가 되어(`...C` 가 `...C-e` 의 접두어) 형제를
부모로 잡는다. newly 에는 부모 필드가 아예 없다. 매핑 없는 펀드는 master_fund_code 를
NULL 로 두고, 1년 뒤 공시목록에 나타나면 재분류에서 자동으로 채워진다.

사용법:
  python scripts/load_fund_master.py --dry-run          # 무엇이 들어갈지만 출력
  python scripts/load_fund_master.py                    # 적재
  python scripts/load_fund_master.py --cache PATH.pkl   # 공시목록을 캐시에서 (KOFIA 재호출 안 함)
"""
import argparse
import datetime
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.db.base import Base  # noqa: E402,F401
from app.db.fund_source import fund_source_query  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.fund import Fund  # noqa: E402
from app.services.fund_classify import classify_funds  # noqa: E402
from app.services.kofia_client import fetch_disclosure  # noqa: E402

BATCH_SIZE = 2000


def _newly_attributes() -> pd.DataFrame:
    """펀드별 속성. 같은 펀드가 여러 설정일로 들어있으면 최신 것을 쓴다."""
    rows = fund_source_query("""
        SELECT fund_code, fund_name, manage_company, incept_dt, category,
               region, custodian, lead_dist
        FROM fund_kr_kofia_newly
    """)
    df = pd.DataFrame(rows, columns=["fund_code", "fund_name", "manage_company", "incept_dt",
                                     "category", "region", "custodian", "lead_dist"])
    return (df.sort_values("incept_dt").drop_duplicates("fund_code", keep="last")
            .set_index("fund_code"))


def _existing_map() -> pd.Series:
    rows = fund_source_query(
        "SELECT fund_code, master_fund FROM fund_kr_kofia_master_fund_map")
    return pd.DataFrame(rows, columns=["fund_code", "master_fund"]).set_index("fund_code")["master_fund"]


def _alive_codes(since: datetime.date) -> set:
    """최근 기준가가 있는 펀드 — 이미 사라진 펀드까지 newly 에서 끌어오지 않으려는 필터."""
    rows = fund_source_query(
        "SELECT DISTINCT fund_code FROM fund_kr_kofia_daily_price WHERE base_dt >= :since",
        {"since": since})
    return {r[0] for r in rows}


def build_master(disclosure_df: pd.DataFrame) -> pd.DataFrame:
    class_df, manage_df = classify_funds(disclosure_df)
    records = {}

    # (1) 공시목록 분류 — 계층의 정답
    for code, r in manage_df.iterrows():
        records[code] = dict(fund_code=code, name=r["full_name"], master_fund_code=code,
                             is_manage_fund=True, class_str=None, special=bool(r["special"]))
    for code, r in class_df.iterrows():
        records[code] = dict(fund_code=code, name=r["fund_name"], master_fund_code=r["manage_code"],
                             is_manage_fund=False, class_str=(r["class_str"] or None),
                             special=bool(r["special"]))

    # (2) 최근 1년 공시가 없는 과거 펀드 — 매핑은 기존 map 을 그대로 신뢰한다
    for code, master in _existing_map().items():
        if code in records:
            continue
        records[code] = dict(fund_code=code, name=None, master_fund_code=master,
                             is_manage_fund=(code == master), class_str=None, special=False)

    # (3) 어디에도 없는 신생 펀드 — 매핑 없이 속성만
    attrs = _newly_attributes()
    alive = _alive_codes(datetime.date.today() - datetime.timedelta(days=365))
    for code in sorted((alive & set(attrs.index)) - set(records)):
        records[code] = dict(fund_code=code, name=None, master_fund_code=None,
                             is_manage_fund=False, class_str=None, special=False)

    df = pd.DataFrame(list(records.values()))

    # 속성 채우기 — 이름은 공시목록 것을 우선하고, 없으면 newly 로 메운다
    joined = df.join(attrs, on="fund_code", rsuffix="_newly")
    joined.index = df.index
    df["name"] = df["name"].fillna(joined["fund_name"]).fillna(df["fund_code"])
    for col in ("manage_company", "incept_dt", "category", "region", "custodian", "lead_dist"):
        df[col] = joined[col]
    df["term_dt"] = None
    return df.astype(object).where(pd.notnull(df), None)


def upsert(db, df: pd.DataFrame) -> int:
    cols = ["fund_code", "name", "master_fund_code", "is_manage_fund", "class_str", "special",
            "manage_company", "category", "region", "custodian", "lead_dist", "incept_dt", "term_dt"]
    rows = df[cols].to_dict("records")
    done = 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        stmt = pg_insert(Fund).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["fund_code"],
            set_={c: stmt.excluded[c] for c in cols if c != "fund_code"})
        db.execute(stmt)
        db.commit()
        done += len(chunk)
        print(f"  {done:,}/{len(rows):,}")
    return done


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--cache", type=Path, default=None,
                   help="공시목록 pickle 경로 (있으면 KOFIA 를 호출하지 않는다)")
    a = p.parse_args()

    if a.cache and a.cache.exists():
        print(f"공시목록 캐시 사용: {a.cache}")
        disclosure = pd.read_pickle(a.cache)
    else:
        print("KOFIA 공시목록 조회 중... (1년치 1회 요청)")
        disclosure = fetch_disclosure()
        if a.cache:
            disclosure.to_pickle(a.cache)
    print(f"  {len(disclosure):,}행 · 고유 펀드 {disclosure['펀드코드'].nunique():,}\n")

    df = build_master(disclosure)
    print(f"적재 대상 {len(df):,}건")
    print(f"  운용펀드            {int(df['is_manage_fund'].sum()):,}")
    print(f"  클래스              {int((~df['is_manage_fund'].astype(bool)).sum()):,}")
    print(f"  매핑 없음(NULL)     {int(df['master_fund_code'].isna().sum()):,}")
    print(f"  이름 있음           {int(df['name'].notna().sum()):,}")
    print(f"  설정일 있음         {int(df['incept_dt'].notna().sum()):,}")
    print(f"  class_str 있음      {int(df['class_str'].notna().sum()):,}")
    print(f"  special             {int(df['special'].astype(bool).sum()):,}")

    if a.dry_run:
        print("\n--dry-run 이므로 적재하지 않습니다. 샘플 5건:")
        for _, r in df.head(5).iterrows():
            print(f"  {r['fund_code']}  master={r['master_fund_code']}  "
                  f"mng={r['is_manage_fund']}  cls={r['class_str']}  {str(r['name'])[:40]}")
        return

    db = SessionLocal()
    try:
        print("\n적재 중...")
        n = upsert(db, df)
        print(f"완료: {n:,}건")
    finally:
        db.close()


if __name__ == "__main__":
    main()
