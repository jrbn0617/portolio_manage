"""일별 매크로 지표 수집 (cron 배치) — FRED SOFRINDEX, KOFR KOFRINDEX.

**매번 전 구간(2018~)을 다시 받아 덮어쓴다.** 둘 다 2,100행 안팎이고 요청은 각 1회다.
배치가 며칠 밀려도 다음 실행이 알아서 메우므로 증분 관리가 필요 없다.

2018 이전 구간은 이 배치가 만들지 못한다 — 원천이 그때부터다.
`scripts/backfill_underlying_index.py` 가 과거를 채운다.

**한 계열이 실패해도 나머지는 적재한다.** FRED 는 키 만료, KOFR 는 응답 형식 변경으로
서로 무관하게 깨질 수 있다. 하나라도 실패하면 배치 상태는 failed 로 남기되, 성공한
계열은 이미 커밋돼 있다.

KOFR 는 공시 시각이 오전 10시 50분경이라(응답의 PUBN_DTTM 기준) 오전 배치는 피한다.

사용법:
  python scripts/refresh_macro_daily.py --dry-run
  python scripts/refresh_macro_daily.py
  python scripts/refresh_macro_daily.py --only KOFRINDEX
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
from app.models.macro_indicator import MacroIndicator  # noqa: E402
from app.services.macro_sources import DAILY_COLLECTORS  # noqa: E402


def upsert(db, name: str, rows: list[tuple]) -> int:
    stmt = pg_insert(MacroIndicator).values(
        [dict(indicator_name=name, date=d, value=v) for d, v in rows])
    db.execute(stmt.on_conflict_do_update(
        index_elements=["indicator_name", "date"],
        set_={"value": stmt.excluded.value}))
    return len(rows)


def main(dry_run: bool, only: list[str] | None = None) -> dict:
    db = SessionLocal()
    summary, failed = {}, []
    try:
        for name, desc, fetch in DAILY_COLLECTORS:
            if only and name not in only:
                continue
            try:
                rows = fetch()
            except Exception as exc:  # noqa: BLE001
                # 한 계열의 실패가 나머지를 막지 않는다 — 원천이 서로 무관하다.
                print(f"  {name:<20} 실패 — {exc}")
                failed.append(name)
                continue

            if not rows:
                print(f"  {name:<20} 응답이 비었습니다 — 건너뜀")
                failed.append(name)
                continue

            before = db.query(MacroIndicator).filter(
                MacroIndicator.indicator_name == name).count()
            if not dry_run:
                upsert(db, name, rows)
                db.commit()
            summary[name] = {"rows": len(rows), "from": str(rows[0][0]),
                             "to": str(rows[-1][0]), "last": rows[-1][1],
                             "new": len(rows) - before}
            print(f"  {name:<20} {len(rows):>4,}건  {rows[0][0]} ~ {rows[-1][0]}"
                  f"  최근값 {rows[-1][1]:>12,.5f}  (기존 {before:,}건)   {desc}")

        if dry_run:
            print("\n--dry-run 이므로 적재하지 않고 종료합니다.")
        if failed:
            raise RuntimeError(f"수집 실패: {', '.join(failed)}")
        return {"indicators": len(summary), "detail": summary}
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
    batch = BatchRun(job_name="macro_daily", trigger=trigger, status="running")
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
    p.add_argument("--only", action="append", help="특정 지표만 (여러 번 지정 가능)")
    p.add_argument("--trigger", default="manual")
    a = p.parse_args()
    if run(a.trigger, a.dry_run, a.only) == "failed":
        sys.exit(1)
