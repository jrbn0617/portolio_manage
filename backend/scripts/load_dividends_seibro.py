"""SEIBRO(한국예탁결제원 증권정보포털) '배당내역전체검색' 공개 페이지에서 최근 1주일치
배당 내역을 조회해 dividends 테이블에 적재한다.

공식 API가 아니라 공개 웹페이지를 브라우저 자동화(Playwright)로 조회하는 방식이다 —
WebSquare 프레임워크(JS 기반 폼 제출)라 단순 HTTP 요청으로는 재현이 안 된다.
과거 히스토리는 이미 사용자가 준비한 파일로 적재돼 있으므로, 이 스크립트는 매일
최근 1주일 구간만 조회해서 신규/정정분을 이어붙이는 용도다 (겹치는 구간은 upsert로
안전하게 덮어써진다).

사용법: python scripts/load_dividends_seibro.py
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted  # noqa: E402
from app.services.upload_service import _upsert_dividend  # noqa: E402

SEIBRO_URL = "https://seibro.or.kr"
MENU_HREF_FRAGMENT = "menuNo=285"  # 기업 > 배당정보 > 배당내역전체검색
PRESET_LABEL = "1주"  # 배당기준일 검색범위 프리셋 (직접 입력은 WebSquare 위젯이 반영 안 함)


def fetch_recent_dividends() -> pd.DataFrame:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(accept_downloads=True)
        page.goto(SEIBRO_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1000)
        page.evaluate(
            """(fragment) => {
                const modal = document.getElementById('_modal');
                if (modal) modal.style.display = 'none';
                const link = [...document.querySelectorAll('a')].find(
                    a => a.getAttribute('href') && a.getAttribute('href').includes(fragment)
                );
                if (link) link.click();
            }""",
            MENU_HREF_FRAGMENT,
        )
        page.wait_for_timeout(2000)

        page.select_option("#selectbox1_input_0", label=PRESET_LABEL)
        page.wait_for_timeout(500)
        date_from = page.input_value("#inputCalendar1_input")
        date_to = page.input_value("#inputCalendar2_input")
        print(f"조회 구간: {date_from} ~ {date_to}")

        page.click("#image1")  # 조회
        page.wait_for_timeout(2000)

        with tempfile.TemporaryDirectory() as tmp_dir:
            download_path = Path(tmp_dir) / "seibro_dividends.xlsx"
            with page.expect_download(timeout=15000) as dl_info:
                page.click("#ExcelDownload_img")
            dl_info.value.save_as(str(download_path))
            browser.close()
            tables = pd.read_html(download_path, encoding="euc-kr")

    return tables[0]


def main():
    print("SEIBRO에서 최근 1주일 배당 내역 조회 중...")
    raw = fetch_recent_dividends()
    print(f"{len(raw)}건 수신")
    if raw.empty:
        print("적재할 데이터가 없습니다.")
        return {"fetched": 0, "upserted": 0, "failed": 0, "recomputed": 0}

    # 응답 테이블은 2단 헤더(MultiIndex)라 이름이 불안정 — 확인된 열 위치로 추출한다.
    # 0=배정기준일 1=현금배당지급일 4=종목코드 5=종목명 10=주당배당금(일반,현금)
    df = pd.DataFrame(
        {
            "ex_date": raw.iloc[:, 0],
            "pay_date": raw.iloc[:, 1],
            "ticker": raw.iloc[:, 4].astype(str).str.strip(),
            "name": raw.iloc[:, 5].astype(str).str.strip(),
            "amount": raw.iloc[:, 10],
        }
    )
    df = df[df["ticker"].notna() & (df["ticker"] != "") & (df["ticker"].str.lower() != "nan")]

    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    touched_min_exdate: dict[int, object] = {}
    upserted = 0
    errors: list[str] = []
    for _, row in df.iterrows():
        try:
            with db.begin_nested():
                instrument_id, ex_date = _upsert_dividend(db, row, instruments_by_ticker)
            prev = touched_min_exdate.get(instrument_id)
            if prev is None or ex_date < prev:
                touched_min_exdate[instrument_id] = ex_date
            upserted += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row['ticker']} {row['name']}: {exc}")
    db.commit()

    print(f"적재 {upserted}건, 실패 {len(errors)}건")
    for e in errors[:20]:
        print(f"  - {e}")

    print("배당조정 지수 재계산 중...")
    for instrument_id, min_exdate in touched_min_exdate.items():
        with db.begin_nested():
            recompute_dividend_adjusted(db, instrument_id, from_date=min_exdate)
    db.commit()
    print(f"영향받은 종목 {len(touched_min_exdate)}개 재계산 완료.")

    return {"fetched": len(raw), "upserted": upserted, "failed": len(errors), "recomputed": len(touched_min_exdate)}


class _Tee:
    """print() 출력을 실제 stdout에도 내보내면서 문자열로도 모아 BatchRun.log에 저장한다."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def run(trigger: str = "manual") -> str:
    """main()을 BatchRun 이력 기록과 함께 실행한다. daily_update.py의 run()과 동일한 패턴.
    반환값은 최종 status 문자열 — DB 세션이 함수 종료 후 정리돼도 안전하게 쓸 수 있도록
    ORM 인스턴스가 아니라 값 자체를 반환한다."""
    import datetime
    import io
    import traceback

    from app.models.batch_run import BatchRun

    db = SessionLocal()
    batch = BatchRun(job_name="dividends_seibro", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = _Tee(real_stdout, buf)
    status = "running"
    try:
        summary = main()
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


if __name__ == "__main__":
    trigger_arg = sys.argv[1] if len(sys.argv) > 1 else "cron"
    if run(trigger=trigger_arg) == "failed":
        sys.exit(1)
