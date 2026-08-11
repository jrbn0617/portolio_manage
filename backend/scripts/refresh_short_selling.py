"""최근 N영업일(기본 5)의 공매도 거래량/거래대금을 다시 받아 덮어쓴다.

KRX 공매도 수치는 당일 발표 뒤에도 며칠에 걸쳐 정정된다(2026-08-10 삼성전자를 하루 뒤
재조회했더니 공매도수량 1,208,499 -> 1,215,389로 약 0.6% 증가). daily_update.py는
"신규 거래일 + 직전 영업일 1일"만 다시 받으므로 그보다 늦게 반영되는 정정분이 남는다.
이 배치가 그 뒤를 훑어 확정치로 맞춘다.

수집 로직은 daily_update.fetch_short_selling을 그대로 재사용한다(이미 ON CONFLICT DO
UPDATE라 재실행해도 안전). 호출량은 N일 x 2시장 x 2함수 = 기본 20회, REQUEST_DELAY_SEC
간격까지 포함해 30초 남짓이라 KRX 세션(1시간) 만료 걱정은 없다.

daily_update.py(평일 16:00)가 끝난 뒤에 도는 걸 전제로 한다.

사용법:
  python scripts/refresh_short_selling.py            # 최근 5영업일
  python scripts/refresh_short_selling.py --days 10  # 최근 10영업일
  python scripts/refresh_short_selling.py cron       # cron 트리거로 기록(기본값)
"""
import argparse
import datetime
import sys
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price import Price  # noqa: E402
from scripts.daily_update import _Tee, fetch_short_selling  # noqa: E402

DEFAULT_LOOKBACK_DAYS = 5


def recent_trading_days(db, n: int) -> list[datetime.date]:
    """prices(D)에 실제로 적재된 최근 n개 거래일 (오름차순)."""
    rows = (
        db.query(Price.date)
        .filter(Price.period == "D")
        .distinct()
        .order_by(Price.date.desc())
        .limit(n)
        .all()
    )
    return sorted(r[0] for r in rows)


def main(days: int) -> dict:
    db = SessionLocal()
    targets = recent_trading_days(db, days)
    if not targets:
        raise RuntimeError("prices(D)에 데이터가 없습니다.")
    print(f"재수집 대상 거래일 {len(targets)}건: {targets}")

    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    for day in targets:
        fetch_short_selling(db, day, instruments_by_ticker)

    db.close()
    return {"days": len(targets), "from": str(targets[0]), "to": str(targets[-1])}


def run(trigger: str, days: int) -> str:
    """main()을 BatchRun 이력과 함께 실행한다 (daily_update.run과 동일 패턴)."""
    import io

    from app.models.batch_run import BatchRun

    db = SessionLocal()
    batch = BatchRun(job_name="refresh_short_selling", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = _Tee(real_stdout, buf)
    status = "running"
    try:
        summary = main(days)
        status = "success"
        batch.status = status
        batch.summary = str(summary)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        batch.status = status
        batch.error = f"{exc}\n{traceback.format_exc()}"
    finally:
        sys.stdout = real_stdout
        batch.log = buf.getvalue()
        batch.finished_at = datetime.datetime.now(datetime.timezone.utc)
        db.add(batch)
        db.commit()
    return status


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("trigger", nargs="?", default="cron", help="BatchRun에 기록할 트리거명 (기본: cron)")
    p.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS, help=f"재수집할 최근 영업일 수 (기본: {DEFAULT_LOOKBACK_DAYS})")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if run(trigger=args.trigger, days=args.days) == "failed":
        sys.exit(1)
