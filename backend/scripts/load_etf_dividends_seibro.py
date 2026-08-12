"""SEIBRO ETF '분배금지급현황'에서 분배금을 수집해 dividends에 적재한다 (cron 배치).

주식 배당(`load_dividends_seibro.py`)과 **메뉴가 다르다** — ETF는
`/IPORTAL/user/etf/BIP_CNTS06030V.xml` (menuNo=179, 권리행사정보 > 분배금지급현황).

**엑셀 다운로드 버튼이 없고 그리드가 가상화돼 있어 DOM 파싱이 안 된다.** 대신 화면이 쓰는
WebSquare 서비스 엔드포인트를 그대로 호출한다:

  POST https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp
  action=exerInfoDtramtPayStatPlistCnt  -> <LIST_CNT value="456"/>   (건수)
  action=exerInfoDtramtPayStatPlist     -> <data><result><ISIN .../></result></data>  (목록)

Playwright 로 페이지를 한 번 열어 세션을 얻은 뒤 그 컨텍스트로 POST 한다(쿠키 필요).

**주의 — 실측으로 확인한 것 셋**
1. `CMM_BTN_ABBR_NM` 은 필수다. 빼면 응답이 비어서 돌아온다(에러가 아니라 빈 결과라 조용히 실패한다).
2. **페이지당 30행이 상한이다.** END_PAGE를 500이나 5000으로 줘도 30행만 온다.
   START_PAGE/END_PAGE는 행 인덱스이므로 1-30, 31-60 … 으로 이어 받는다(중복 없음을 확인).
3. 응답 필드명이 MySQL `etf_kr_seibro_dividend` 와 동일하다 — 같은 원천이다.

**청산분배는 적재하지 않는다.** 이익분배는 배당락으로 가격이 떨어지니 되돌려주는 게 맞지만,
청산분배는 상장폐지하며 NAV를 그대로 지급하는 것이라 가격이 떨어지지 않는다. 되돌려주면
이중계상이고 배당조정 지수가 통째로 부풀어 오른다(292730 사례: 오차 98%).

티커는 ISIN에서 뽑는다 — `KR7069500007` -> `069500`, `KR70052D0006` -> `0052D0` (3~9번째).

사용법:
  python scripts/load_etf_dividends_seibro.py --dry-run
  python scripts/load_etf_dividends_seibro.py                  # 최근 7일 (cron)
  python scripts/load_etf_dividends_seibro.py --from 2026-07-10 --to 2026-08-12
"""
import argparse
import datetime
import io
import re
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.dividend import Dividend  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.derived_prices import recompute_dividend_adjusted  # noqa: E402

PAGE_URL = ("https://seibro.or.kr/websquare/control.jsp"
            "?w2xPath=/IPORTAL/user/etf/BIP_CNTS06030V.xml&menuNo=179")
ENDPOINT = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
TASK = "ksd.safe.bip.cnts.etf.process.EtfExerInfoPTask"
BTN = "total_search,openall,print,hwp,word,pdf,searchIcon,searchIcon,seach,searchIcon,seach,"
PAGE_SIZE = 30          # 서버 상한 (실측)
REQUEST_DELAY_SEC = 1.0
PROFIT_KIND = "이익분배"


def _req(action: str, start: int, end: int, frm: str, to: str) -> str:
    return (f'<reqParam action="{action}" task="{TASK}">'
            f'<MENU_NO value="179"/><CMM_BTN_ABBR_NM value="{BTN}"/>'
            f'<W2XPATH value="/IPORTAL/user/etf/BIP_CNTS06030V.xml"/>'
            f'<etf_sort_level_cd value="0"/><etf_big_sort_cd value=""/>'
            f'<START_PAGE value="{start}"/><END_PAGE value="{end}"/><etf_sort_cd value=""/>'
            f'<isin value=""/><mngco_custno value=""/><RGT_RSN_DTAIL_SORT_CD value=""/>'
            f'<fromRGT_STD_DT value="{frm}"/><toRGT_STD_DT value="{to}"/></reqParam>')


FIELD = re.compile(r'<(\w+) value="([^"]*)"/>')


def fetch(frm: datetime.date, to: datetime.date) -> list[dict]:
    f, t = frm.strftime("%Y%m%d"), to.strftime("%Y%m%d")
    headers = {"Content-Type": "text/xml; charset=UTF-8", "Referer": PAGE_URL}
    rows: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(PAGE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)

        cnt_xml = page.request.post(
            ENDPOINT, data=_req("exerInfoDtramtPayStatPlistCnt", 1, PAGE_SIZE, f, t), headers=headers).text()
        m = re.search(r'LIST_CNT value="(\d+)"', cnt_xml)
        total = int(m.group(1)) if m else 0
        print(f"조회 구간 {frm} ~ {to}: {total:,}건")

        for start in range(1, total + 1, PAGE_SIZE):
            body = page.request.post(
                ENDPOINT,
                data=_req("exerInfoDtramtPayStatPlist", start, start + PAGE_SIZE - 1, f, t),
                headers=headers).text()
            for block in body.split("<data ")[1:]:
                rows.append({k: v for k, v in FIELD.findall(block)})
            time.sleep(REQUEST_DELAY_SEC)
        browser.close()

    if total and len(rows) != total:
        print(f"  경고: 수신 {len(rows):,}행이 건수 {total:,}과 다릅니다 — 페이지네이션 확인 필요")
    return rows


def main(frm: datetime.date, to: datetime.date, dry_run: bool) -> dict:
    raw = fetch(frm, to)
    if not raw:
        print("적재할 데이터가 없습니다.")
        return {"fetched": 0, "upserted": 0, "skipped_liquidation": 0, "unknown": 0, "recomputed": 0}

    liq = sum(1 for r in raw if r.get("RGT_RSN_DTAIL_NM") != PROFIT_KIND)
    keep = [r for r in raw
            if r.get("RGT_RSN_DTAIL_NM") == PROFIT_KIND and float(r.get("ESTM_STDPRC") or 0) > 0]
    print(f"수신 {len(raw):,}행 · 이익분배 {len(keep):,} · 청산분배 등 제외 {liq:,}")

    db = SessionLocal()
    etf = {t: i for t, i in db.query(Instrument.ticker, Instrument.id)
           .filter(Instrument.asset_type == "etf").all()}

    # (종목, 배정기준일) 단위로 합산 — 같은 날 여러 건이 올 수 있다
    merged: dict[tuple[int, datetime.date], dict] = {}
    unknown: set[str] = set()
    for r in keep:
        ticker = r["ISIN"][3:9]
        iid = etf.get(ticker)
        if iid is None:
            unknown.add(f"{ticker} {r.get('KOR_SECN_NM','')}")
            continue
        ex = datetime.datetime.strptime(r["RGT_STD_DT"], "%Y%m%d").date()
        pay = (datetime.datetime.strptime(r["TH1_PAY_TERM_BEGIN_DT"], "%Y%m%d").date()
               if r.get("TH1_PAY_TERM_BEGIN_DT") else None)
        k = (iid, ex)
        if k in merged:
            merged[k]["amount"] += float(r["ESTM_STDPRC"])
        else:
            merged[k] = {"pay_date": pay, "amount": float(r["ESTM_STDPRC"])}

    if unknown:
        print(f"  instruments 에 없는 ETF {len(unknown)}종목 — 건너뜀 "
              f"(refresh_etf_prices.py 가 먼저 돌면 등록된다): {sorted(unknown)[:5]}")

    print(f"적재 대상 {len(merged):,}건 / 종목 {len({k[0] for k in merged}):,}개")
    if dry_run:
        for (iid, ex), v in sorted(merged.items(), key=lambda kv: kv[0][1], reverse=True)[:5]:
            tk = next(t for t, i in etf.items() if i == iid)
            print(f"    {tk} {ex} {v['amount']:,.2f}원")
        print("--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return {"fetched": len(raw), "upserted": 0, "skipped_liquidation": liq,
                "unknown": len(unknown), "recomputed": 0}

    touched: dict[int, datetime.date] = {}
    upserted = 0
    for (iid, ex), v in merged.items():
        existing = db.query(Dividend).filter(
            Dividend.instrument_id == iid, Dividend.ex_date == ex).one_or_none()
        if existing:
            if float(existing.amount) == v["amount"] and existing.pay_date == v["pay_date"]:
                continue
            existing.amount, existing.pay_date = v["amount"], v["pay_date"]
        else:
            db.add(Dividend(instrument_id=iid, ex_date=ex, pay_date=v["pay_date"], amount=v["amount"]))
        upserted += 1
        if iid not in touched or ex < touched[iid]:
            touched[iid] = ex
    db.commit()
    print(f"신규/변경 {upserted:,}건")

    failed = 0
    for iid, min_ex in touched.items():
        try:
            recompute_dividend_adjusted(db, iid, from_date=min_ex)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  배당조정 실패 instrument_id={iid}: {exc}")
            db.rollback()
    db.commit()
    print(f"배당조정 재계산 {len(touched):,}종목 (실패 {failed})")

    total = db.execute(text("""select count(*), min(d.ex_date), max(d.ex_date) from dividends d
        join instruments i on i.id = d.instrument_id where i.asset_type='etf'""")).fetchone()
    print(f"ETF 배당 현황: {total[0]:,}건 ({total[1]} ~ {total[2]})")
    db.close()
    return {"fetched": len(raw), "upserted": upserted, "skipped_liquidation": liq,
            "unknown": len(unknown), "recomputed": len(touched), "failed": failed}


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def run(trigger="manual", frm=None, to=None, days=7, dry_run=False) -> str:
    from app.models.batch_run import BatchRun

    to = to or datetime.date.today()
    frm = frm or (to - datetime.timedelta(days=days))

    db = SessionLocal()
    batch = BatchRun(job_name="etf_dividends_seibro", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf, real = io.StringIO(), sys.stdout
    sys.stdout = _Tee(real, buf)
    status = "running"
    try:
        batch.summary = str(main(frm, to, dry_run))
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
    p.add_argument("--from", dest="frm", type=datetime.date.fromisoformat)
    p.add_argument("--to", type=datetime.date.fromisoformat)
    p.add_argument("--days", type=int, default=7, help="--from 미지정 시 최근 N일")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--trigger", default="manual")
    a = p.parse_args()
    if run(a.trigger, a.frm, a.to, a.days, a.dry_run) == "failed":
        sys.exit(1)
