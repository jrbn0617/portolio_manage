"""주식병합(액면병합·감자)이 벤더 수정주가에 반영되지 않은 종목을 직접 소급조정한다.

DataGuide 수정주가(S100300)가 병합을 반영하지 않고 오는 경우가 있다. 052670 제일바이오는
2026-02-09 1,500:1 병합 후 변경상장인데, 재요청해서 받은 응답도 병합 전 2,080원 /
병합 후 625,000원을 그대로 줘서 하루 +29,948% 짜리 가짜 수익률이 남는다.

이 스크립트는 기준일 **이전** 구간의 open/high/low/close 에 병합비율을 곱해 계열을
잇는다. raw_close(실제 체결가)와 market_cap 은 그 시점의 사실이므로 건드리지 않는다.
조정 후 월봉과 배당조정 수정종가를 force_full 로 다시 계산한다.

  python scripts/apply_reverse_split_adjustment.py 052670 2026-02-09 1500 [--dry-run]

인자: <티커> <병합 효력 반영 거래일(이 날부터 신주 기준)> <병합비율(구주 N주 -> 신주 1주)>

되돌릴 때는 같은 명령에 비율을 역수로 주지 말고 DataGuide 원본을 다시 적재할 것 —
반올림 때문에 정확히 복원되지 않는다.
"""
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted  # noqa: E402

ADJUSTED_COLUMNS = ("open", "high", "low", "close")


def main(ticker: str, effective_date: date, ratio: float, dry_run: bool = False):
    db = SessionLocal()
    inst = db.query(Instrument).filter(Instrument.ticker == ticker).first()
    if inst is None:
        print(f"instruments에 {ticker} 없음")
        return

    before = db.execute(
        text("""
          select period, count(*) as n, min(date) as d0, max(date) as d1
          from prices where instrument_id = :i and date < :d group by period order by period
        """),
        {"i": inst.id, "d": effective_date},
    ).all()
    if not before:
        print(f"{ticker}: {effective_date} 이전 데이터 없음 — 할 일 없음")
        return

    print(f"{ticker} {inst.name}: {effective_date} 이전 구간에 ×{ratio:g} 적용")
    for r in before:
        print(f"  period={r.period}  {r.n}행  ({r.d0} ~ {r.d1})")

    sample = db.execute(
        text("""
          select date, close, raw_close from prices
          where instrument_id = :i and period = 'D' and date < :d
          order by date desc limit 1
        """),
        {"i": inst.id, "d": effective_date},
    ).first()
    after_first = db.execute(
        text("""
          select date, close from prices
          where instrument_id = :i and period = 'D' and date >= :d order by date limit 1
        """),
        {"i": inst.id, "d": effective_date},
    ).first()

    if sample and after_first:
        old = float(sample.close) * ratio
        new = float(after_first.close)
        print(f"\n  조정 후 연결: {sample.date} {old:,.0f} → {after_first.date} {new:,.0f} "
              f"({new / old - 1:+.2%})")
        print(f"  (조정 전에는 {float(sample.close):,.0f} → {new:,.0f} = "
              f"{new / float(sample.close) - 1:+,.0%})")

    if dry_run:
        print("\n[dry-run] 변경하지 않음")
        db.close()
        return

    sets = ", ".join(f"{c} = {c} * :r" for c in ADJUSTED_COLUMNS)
    result = db.execute(
        text(f"update prices set {sets} where instrument_id = :i and date < :d"),
        {"r": ratio, "i": inst.id, "d": effective_date},
    )
    db.commit()
    print(f"\n  prices {result.rowcount}행 수정 (raw_close·market_cap 은 원본 유지)")

    recompute_dividend_adjusted(db, inst.id, force_full=True)
    db.commit()
    print("  배당조정 수정종가 재계산 완료")
    db.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 3:
        print(__doc__)
        sys.exit(1)
    main(args[0], date.fromisoformat(args[1]), float(args[2]), dry_run="--dry-run" in sys.argv)
