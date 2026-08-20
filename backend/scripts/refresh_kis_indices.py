"""국내 채권지수 수집 (cron 배치) — kis-net.kr 의 KIS10Y·KIS30Y·KISCD.

**매번 전 구간(2015~)을 다시 받아 덮어쓴다.** 지수당 2,900행 안팎이고 요청은 각 1회다.
배치가 며칠 밀려도 다음 실행이 알아서 메우므로 증분 관리가 필요 없다.

2015 이전 구간은 이 배치가 만들지 못한다 — 사이트가 그때부터다.
`scripts/backfill_kr_bond_indices.py` 가 소스 DB 에서 과거를 채운다.

저장 위치는 `instruments(asset_type='index')` + `prices` 다. 국내 채권지수는 한국
거래일을 타므로 주식과 캘린더가 같아 안전하다 — 해외지수를 prices 에 넣었을 때
한국 휴장일이 딸려 오던 문제가 여기서는 생기지 않는다.

    prices.close      총수익지수 — 우리가 쓸 값
    prices.raw_close  순가격지수

**한 지수가 실패해도 나머지는 적재한다.** 하나라도 실패하면 배치 상태는 failed 로
남기되, 성공한 것은 이미 커밋돼 있다.

당일 종가는 장 마감 뒤에 확정되므로 오후 늦게 돈다.

사용법:
  python scripts/refresh_kis_indices.py --dry-run
  python scripts/refresh_kis_indices.py
  python scripts/refresh_kis_indices.py --only KIS10Y
"""
import argparse
import datetime
import io
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.kis_index import INDICES, fetch_index  # noqa: E402
from app.services.market_calendar import resolve_batch_status  # noqa: E402

BATCH_SIZE = 5000


def upsert(db, iid: int, rows: list[tuple]) -> None:
    values = [dict(instrument_id=iid, date=d, period="D", close=round(tr, 6),
                   raw_close=None if cp is None else round(cp, 6)) for d, tr, cp in rows]
    for i in range(0, len(values), BATCH_SIZE):
        stmt = pg_insert(Price).values(values[i:i + BATCH_SIZE])
        db.execute(stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "period"],
            set_={"close": stmt.excluded.close, "raw_close": stmt.excluded.raw_close}))


def main(dry_run: bool, only: list[str] | None = None) -> dict:
    db = SessionLocal()
    summary, failed = {}, []
    try:
        for ticker, (code, name, _) in INDICES.items():
            if only and ticker not in only:
                continue
            try:
                rows = fetch_index(ticker)
            except Exception as exc:  # noqa: BLE001
                # 지수마다 별개 요청이라 하나의 실패가 나머지를 막지 않는다.
                print(f"  {ticker:<8} 실패 — {exc}")
                failed.append(ticker)
                continue

            inst = (db.query(Instrument)
                    .filter(Instrument.ticker == ticker, Instrument.asset_type == "index").first())
            before = 0
            if inst is None:
                if not dry_run:
                    inst = Instrument(ticker=ticker, name=name, asset_type="index")
                    db.add(inst)
                    db.flush()
            else:
                before = (db.query(Price)
                          .filter(Price.instrument_id == inst.id, Price.period == "D").count())

            if not dry_run:
                upsert(db, inst.id, rows)
                db.commit()
            summary[ticker] = {"rows": len(rows), "from": str(rows[0][0]),
                               "to": str(rows[-1][0]), "last": rows[-1][1]}
            print(f"  {ticker:<8} {len(rows):>6,}건  {rows[0][0]} ~ {rows[-1][0]}"
                  f"  최근 {rows[-1][1]:>12,.4f}  (기존 {before:,}행)   {code} {name}")

        if dry_run:
            print("\n--dry-run 이므로 적재하지 않고 종료합니다.")
        if failed:
            raise RuntimeError(f"수집 실패: {', '.join(failed)}")
        return {"indices": len(summary), "detail": summary}
    finally:
        db.close()


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def run(trigger="manual", dry_run=False, only=None) -> str:
    from app.models.batch_run import BatchRun

    db = SessionLocal()
    batch = BatchRun(job_name="kis_indices", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf, real = io.StringIO(), sys.stdout
    sys.stdout = _Tee(real, buf)
    status = "running"
    try:
        batch.summary = str(main(dry_run, only))
        status = "success"
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        batch.error = f"{exc}\n{traceback.format_exc()}"
    finally:
        sys.stdout = real
        status = resolve_batch_status(db, status)
        batch.status = status
        batch.log = buf.getvalue()
        batch.finished_at = datetime.datetime.now(datetime.timezone.utc)
        db.add(batch)
        db.commit()
        db.close()
    return status


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", action="append", help="특정 지수만")
    p.add_argument("--trigger", default="manual")
    a = p.parse_args()
    if run(a.trigger, a.dry_run, a.only) == "failed":
        sys.exit(1)
