"""백테스트 중 발견된 종목의 시계열 단절(상장폐지/인수합병) 사유를 입력한다.

run_backtest.py 실행 중 "[⚠ 시계열단절] ... 미분류" 경고가 뜬 종목에 대해
사용자가 사유를 조사한 뒤 이 스크립트로 입력하면 corporate_action_events에
저장되고, 이후 백테스트 재실행부터는 자동으로 반영된다(재입력 불필요).

사용법: python scripts/resolve_backtest_event.py <ticker>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.models.corporate_action_event import CorporateActionEvent
from app.models.dividend_adjusted_price import DividendAdjustedPrice
from app.models.instrument import Instrument


def main(ticker: str):
    db = SessionLocal()
    inst = db.query(Instrument).filter(Instrument.ticker == ticker).first()
    if inst is None:
        print(f"instruments에 {ticker} 없음")
        return

    last_date = (
        db.query(DividendAdjustedPrice.date)
        .filter(DividendAdjustedPrice.instrument_id == inst.id, DividendAdjustedPrice.period == "D")
        .order_by(DividendAdjustedPrice.date.desc())
        .first()
    )
    if last_date is None:
        print(f"{ticker} {inst.name}: 가격 데이터 자체가 없음")
        return
    last_date = last_date[0]

    existing = db.query(CorporateActionEvent).filter(CorporateActionEvent.instrument_id == inst.id).first()
    if existing:
        print(f"이미 등록된 분류가 있습니다: {existing.event_type}, {existing.note!r}. 다시 입력하면 덮어씁니다.")

    print(f"\n종목: {ticker} {inst.name}")
    print(f"마지막 데이터일: {last_date}")
    print("사유를 선택하세요: [1] 상장폐지  [2] 인수합병")
    choice = input("> ").strip()

    successor_id = None
    exchange_ratio = None
    disposal_value = None

    if choice == "1":
        event_type = "delisted"
        raw = input("최종 처분가치(주당, 원) [모르면 Enter=0]: ").strip()
        disposal_value = float(raw) if raw else 0.0
    elif choice == "2":
        event_type = "merger"
        successor_ticker = input("후속종목 티커: ").strip()
        successor = db.query(Instrument).filter(Instrument.ticker == successor_ticker).first()
        if successor is None:
            print(f"instruments에 {successor_ticker} 없음 — 먼저 등록 후 다시 시도하세요.")
            return
        successor_id = successor.id
        raw = input(f"교환비율 ({ticker} 1주당 {successor_ticker} 몇 주?): ").strip()
        exchange_ratio = float(raw)
    else:
        print("1 또는 2를 입력하세요.")
        return

    note = input("메모(사유/출처, 선택): ").strip() or None

    if existing:
        existing.event_type = event_type
        existing.last_data_date = last_date
        existing.successor_instrument_id = successor_id
        existing.exchange_ratio = exchange_ratio
        existing.disposal_value = disposal_value
        existing.note = note
    else:
        db.add(
            CorporateActionEvent(
                instrument_id=inst.id,
                event_type=event_type,
                last_data_date=last_date,
                successor_instrument_id=successor_id,
                exchange_ratio=exchange_ratio,
                disposal_value=disposal_value,
                note=note,
            )
        )
    db.commit()
    print(f"\n저장 완료: {ticker} {inst.name} -> {event_type}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("사용법: python scripts/resolve_backtest_event.py <ticker>")
        sys.exit(1)
    main(sys.argv[1])
