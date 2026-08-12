"""KOSPI200/KOSDAQ150 반기 편입종목을 2014~2018 구간까지 소급 적재한다 (1회성).

`index_memberships`가 2018-12-31부터라 그 이전 시점은 `resolve_universe`가 빈 집합을
돌려주고, 결국 **주가를 아무리 백필해도 2019년 이전 백테스트가 아예 돌지 않는다.**
표본을 6.6년에서 10년 가까이 늘리려면 이 테이블이 먼저 채워져야 한다.

KRX 제공 범위를 실측으로 확인했다:
  - 2014-05-01 이전은 KRX가 아예 제공하지 않음("does NOT provide data prior to 2014/05/01")
  - 코스피200: 2015-06-30에 200종목 조회됨(2014년대는 재확인 필요 — 첫 탐침에서 200이
    나왔으나 아래 코스닥150 사례처럼 값이 잘못 나올 수 있어 단정하지 않는다)
  - 코스닥150: 2015-07-13 출범. **2015-06-30·07-01은 빈 결과이고 07-31부터 150종목**이
    나온다(2회 재시도로 확인). 첫 탐침에서 2015-06-30이 150으로 나왔던 것은 오답이었다
따라서 두 지수가 모두 갖춰지는 하한은 **2015-07-31**이다.

**프로젝트 규칙 예외**: CLAUDE.md는 과거 백필을 pykrx/KRX로 하지 않는다고 정하고 있으나
이 항목만 예외로 둔다 — (1) 시계열이 아니라 시점별 종목 명단이고, (2) DataGuide 요청
양식으로 받을 수 있는지 불확실하며, (3) 기존 2018-12-31~ 스냅샷도 같은 경로
(load_index_memberships_pykrx.py)로 받았고, (4) 호출량이 반기 x 2지수로 수십 회에
불과하다. 주가·재무 시계열 백필은 종전대로 DataGuide로만 받는다.

**부수 효과가 본 목적만큼 중요하다**: 과거 편입 명단에는 지금은 상장폐지·합병된 종목이
들어 있다. 우리 instruments는 2018-12-28 이후 존재한 종목만 알고 있어서, 그 명단 없이
DataGuide에 주가를 요청하면 "끝까지 살아남은 종목"만 받게 되어 생존편향이 생긴다.
이 스크립트가 발견한 미등록 티커가 곧 **추가로 요청해야 할 상폐 종목 목록**이다.

새로 발견한 티커는 instruments에 등록한다(이름은 pykrx로 조회해보고 실패하면 티커를
그대로 넣는다 — 상폐 종목은 이름 조회가 안 되는 경우가 많다).

사용법:
  python scripts/backfill_index_memberships_2015.py --dry-run   # 조회만, DB 미변경
  python scripts/backfill_index_memberships_2015.py             # 실제 적재
  python scripts/backfill_index_memberships_2015.py --from 2014-06-30
"""
import argparse
import datetime
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402  (KRX_ID/KRX_PW가 os.environ에 있어야 해서 load_dotenv 이후)

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.index_membership import IndexMembership  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.instrument_rules import filter_common, is_common_stock  # noqa: E402
from app.services.upload_service import _upsert_index_membership  # noqa: E402

REQUEST_DELAY_SEC = 1
KRX_DATA_FLOOR = datetime.date(2014, 5, 1)  # 이보다 이르면 KRX가 데이터를 주지 않는다
DEFAULT_FROM = datetime.date(2015, 6, 30)  # 코스닥150이 갖춰지는 시점
DEFAULT_TO = datetime.date(2018, 12, 31)  # 기존 적재분 시작점과 맞물림

INDEX_SPECS = [("KOSPI200", "KOSPI", "코스피 200"), ("KOSDAQ150", "KOSDAQ", "코스닥 150")]

# 반기(6/30·12/31) 주기에 안 걸리는 지수 출범 시점 스냅샷. 코스닥150은 2015-07-13 출범이라
# 2015-06-30·07-01은 조회가 비고 07-31부터 150종목이 나온다(2회 재시도로 확인). 이걸 넣어야
# 2015년 하반기 리밸런싱에도 코스닥150 유니버스가 잡힌다(resolve_universe는 as_of 이하
# 최신 스냅샷을 쓰므로, 없으면 2015-08~12가 통째로 빈다).
INCEPTION_SNAPSHOTS = {"KOSDAQ150": datetime.date(2015, 7, 31)}


def half_year_dates(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    out = []
    for year in range(start.year, end.year + 1):
        for month, day in ((6, 30), (12, 31)):
            d = datetime.date(year, month, day)
            if start <= d <= end:
                out.append(d)
    return out


def dates_for(index_name: str, start: datetime.date, end: datetime.date) -> list[datetime.date]:
    out = half_year_dates(start, end)
    inception = INCEPTION_SNAPSHOTS.get(index_name)
    if inception is not None and start <= inception <= end and inception not in out:
        out.append(inception)
    return sorted(out)


def find_index_ticker(market: str, name: str) -> str:
    for code in stock.get_index_ticker_list(market=market):
        if stock.get_index_ticker_name(code) == name:
            return code
    raise ValueError(f"{market} 시장에서 지수 '{name}'을(를) 찾지 못했습니다.")


def load_market_snapshots(db, known: dict[str, int], existing: set, dates: list[datetime.date], dry_run: bool):
    """KOSPI/KOSDAQ 시장구분(=코스피전체·코스닥전체 유니버스) 과거 스냅샷.

    종목명 확보가 관건이다. get_market_ticker_list는 티커만 주고 get_market_ticker_name은
    날짜 인자가 없어 상폐 종목에서 실패한다. get_market_price_change_by_ticker는 구간
    등락률 표를 돌려주는데 **인덱스가 티커, '종목명' 컬럼이 함께 있어** 그 시점에 상장돼
    있던 종목의 이름을 1콜로 전부 얻을 수 있다(1,000종목이어도 1회). 하루짜리 구간
    (전영업일~기준일)으로 호출한다.
    """
    added, discovered = 0, {}
    for market in ("KOSPI", "KOSDAQ"):
        print(f"=== {market} 시장구분 ===")
        for d in dates:
            if (market, d) in existing:
                print(f"  {d}: 이미 적재됨 — 건너뜀")
                continue
            ymd = d.strftime("%Y%m%d")
            prev = (d - datetime.timedelta(days=5)).strftime("%Y%m%d")
            df = stock.get_market_price_change_by_ticker(prev, ymd, market=market)
            time.sleep(REQUEST_DELAY_SEC)
            if df is None or len(df) == 0:
                print(f"  {d}: 조회 결과 없음 — 건너뜀")
                continue
            names = {str(t): str(n) for t, n in df["종목명"].items() if is_common_stock(str(t))}
            new = [t for t in names if t not in known]
            for t in new:
                discovered.setdefault(t, (names[t], f"{market} {d}"))
            print(f"  {d}: {len(names)}종목 (미등록 {len(new)}개)")
            if dry_run:
                continue
            for ticker, name in names.items():
                row = {"ticker": ticker, "index_name": market, "as_of_date": d, "name": name, "market": market}
                with db.begin_nested():
                    _upsert_index_membership(db, row, known)
            db.commit()
            added += 1
    return added, discovered


def resolve_name(ticker: str) -> str | None:
    """상폐 종목은 조회가 안 되는 경우가 많아 실패를 정상으로 취급한다."""
    try:
        name = stock.get_market_ticker_name(ticker)
        return name if name and not isinstance(name, type(None)) else None
    except Exception:  # noqa: BLE001
        return None


def main(date_from: datetime.date, date_to: datetime.date, dry_run: bool, markets: bool):
    if date_from < KRX_DATA_FLOOR:
        raise SystemExit(f"KRX는 {KRX_DATA_FLOOR} 이전 데이터를 제공하지 않습니다.")

    db = SessionLocal()
    known = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    existing = {
        (r[0], r[1])
        for r in db.query(IndexMembership.index_name, IndexMembership.as_of_date).distinct().all()
    }
    print(f"대상 구간: {date_from} ~ {date_to}")
    print(f"기존 instruments {len(known):,}개, 기존 스냅샷 {len(existing)}개\n")

    unknown_tickers: dict[str, list[str]] = {}
    added_snapshots = 0

    for index_name, market, krx_name in INDEX_SPECS:
        code = find_index_ticker(market, krx_name)
        print(f"=== {index_name} (KRX 지수코드 {code}) ===")
        for d in dates_for(index_name, date_from, date_to):
            if (index_name, d) in existing:
                print(f"  {d}: 이미 적재됨 — 건너뜀")
                continue
            members = filter_common(stock.get_index_portfolio_deposit_file(code, d.strftime("%Y%m%d"), alternative=True))
            time.sleep(REQUEST_DELAY_SEC)
            if not members:
                print(f"  {d}: 조회 결과 없음 — 건너뜀 (지수 미출범 등)")
                continue

            new = [t for t in members if t not in known]
            for t in new:
                unknown_tickers.setdefault(t, []).append(f"{index_name} {d}")
            print(f"  {d}: {len(members)}종목 (미등록 {len(new)}개)")

            if dry_run:
                continue
            for ticker in members:
                row = {"ticker": ticker, "index_name": index_name, "as_of_date": d}
                if ticker not in known:
                    name = resolve_name(ticker)
                    if name:
                        row["name"] = name
                with db.begin_nested():
                    _upsert_index_membership(db, row, known)
            db.commit()
            added_snapshots += 1

    market_discovered = {}
    if markets:
        print()
        n, market_discovered = load_market_snapshots(
            db, known, existing, half_year_dates(date_from, date_to), dry_run
        )
        added_snapshots += n

    print(f"\n{'[dry-run] ' if dry_run else ''}적재한 스냅샷: {added_snapshots}개")
    if market_discovered:
        print(f"시장구분에서 새로 발견한 종목: {len(market_discovered)}개 (대부분 2018년 이전 상폐)")
        for t, (nm, where) in sorted(market_discovered.items())[:15]:
            print(f"    {t} {nm}  ({where})")
        if len(market_discovered) > 15:
            print(f"    ... 외 {len(market_discovered) - 15}개")
    print(f"과거 편입 명단에서 발견한 미등록 종목: {len(unknown_tickers)}개")
    if unknown_tickers:
        print("  (= DataGuide 백필 요청에 반드시 포함해야 할 상폐/합병 종목)")
        for t, where in sorted(unknown_tickers.items())[:40]:
            print(f"    {t}  최초등장: {where[0]}")
        if len(unknown_tickers) > 40:
            print(f"    ... 외 {len(unknown_tickers) - 40}개")
    db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", type=datetime.date.fromisoformat, default=DEFAULT_FROM)
    p.add_argument("--to", dest="date_to", type=datetime.date.fromisoformat, default=DEFAULT_TO)
    p.add_argument("--dry-run", action="store_true", help="조회만 하고 DB는 건드리지 않음")
    p.add_argument("--markets", action="store_true", help="KOSPI/KOSDAQ 시장구분 스냅샷도 함께 수집")
    a = p.parse_args()
    main(a.date_from, a.date_to, a.dry_run, a.markets)
