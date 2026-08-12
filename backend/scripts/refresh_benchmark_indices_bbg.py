"""벤치마크 지수(코스피·코스피200·코스닥·코스닥150)를 블룸버그에서 받아 갱신한다 (cron 배치).

**TR 지수를 직접 계산한다.** 코스닥 계열은 TR 지수 티커가 없고, 블룸버그의
`TOT_RETURN_INDEX_GROSS_DVDS` 는 **요청 시작일 기준으로 리베이스**돼서 구간을 바꾸면 레벨이
통째로 달라진다(증분 갱신을 하면 경계에서 수익률이 왜곡된다). 그래서 리베이스가 없는 두 필드,

  PX_LAST               가격지수
  INDX_GROSS_DAILY_DIV  일별 배당포인트 (세전)

만 받아서 아래 식으로 총수익지수를 만든다:

    TR_t = TR_{t-1} x (P_t + D_t) / P_{t-1}

이 식이 맞는지 블룸버그 TOT_RETURN_INDEX_GROSS_DVDS 와 대조해 소수점까지 일치하는 것을
확인했다 (KOSPI2 2026-08-06: 1038.59 x (982.92+0.12163)/1038.59 = 983.0416 = BBG 값).

**매 실행마다 전 구간을 다시 받아 통째로 교체한다.** 배당포인트는 사후 정정되므로 증분으로
이어붙이면 과거가 틀어진 채 남는다. 4종목 x 약 3,200거래일이라 호출 1회로 끝난다.

저장 위치:
  prices.close      TR (배당 재투자) — 백테스트·레짐필터가 쓰는 값
  prices.raw_close  PX_LAST (가격지수)

TR의 기준값은 구간 첫날의 PX_LAST로 잡는다(블룸버그 관행과 동일). 지수 레벨 자체는
기준일에 따라 달라지지만 **수익률은 불변**이라 백테스트 결과에 영향이 없다.

사용법:
  python scripts/refresh_benchmark_indices_bbg.py --dry-run
  python scripts/refresh_benchmark_indices_bbg.py
  python scripts/refresh_benchmark_indices_bbg.py --from 2010-01-01
"""
import argparse
import datetime
import io
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.bbg import fetch_bbg_timeseries  # noqa: E402

# 블룸버그 티커 -> (우리 ticker, 이름)
INDEX_MAP = {
    "KOSPI Index": ("KOSPI", "코스피종합"),
    "KOSPI2 Index": ("KOSPI200", "코스피200"),
    "KOSDAQ Index": ("KOSDAQ", "코스닥종합"),
    "KOSDQ150 Index": ("KOSDAQ150", "코스닥150"),
}
PRICE_FIELD = "PX_LAST"
DIV_FIELD = "INDX_GROSS_DAILY_DIV"
DEFAULT_START = datetime.date(2014, 1, 1)
BATCH_SIZE = 5000


def build_total_return(px, div):
    """TR_t = TR_{t-1} x (P_t + D_t) / P_{t-1}. 첫날 TR = 첫날 P."""
    tr, prev_p, prev_tr = [], None, None
    for p, d in zip(px, div):
        if p is None or p != p:          # NaN = 휴장
            tr.append(None)
            continue
        if prev_p is None:
            prev_tr = p
        else:
            prev_tr = prev_tr * (p + (d if d == d else 0.0)) / prev_p
        tr.append(prev_tr)
        prev_p = p
    return tr


def main(start: datetime.date, end: datetime.date, dry_run: bool) -> dict:
    print(f"블룸버그 조회 {start} ~ {end} · {len(INDEX_MAP)}종목 · {PRICE_FIELD},{DIV_FIELD}")
    df = fetch_bbg_timeseries(list(INDEX_MAP), start, end, [PRICE_FIELD, DIV_FIELD])
    if df is None or df.empty:
        raise RuntimeError("블룸버그 응답이 비었습니다 — SSH/터미널 상태를 확인하세요.")

    db = SessionLocal()
    known = {t: i for t, i in db.query(Instrument.ticker, Instrument.id)
             .filter(Instrument.asset_type == "index").all()}

    summary, rows = {}, []
    for bbg, (ticker, name) in INDEX_MAP.items():
        pcol, dcol = f"{PRICE_FIELD}|{bbg}", f"{DIV_FIELD}|{bbg}"
        if pcol not in df.columns:
            print(f"  {bbg}: 응답에 {pcol} 없음 — 건너뜀")
            continue
        sub = df[[pcol] + ([dcol] if dcol in df.columns else [])].dropna(subset=[pcol])
        px = sub[pcol].tolist()
        dv = sub[dcol].tolist() if dcol in sub.columns else [0.0] * len(px)
        tr = build_total_return(px, dv)
        div_days = sum(1 for d in dv if d == d and d > 0)
        summary[ticker] = {"days": len(sub), "from": str(sub.index[0].date()),
                           "to": str(sub.index[-1].date()), "div_days": div_days,
                           "tr_gain": round(tr[-1] / tr[0] - 1, 4),
                           "px_gain": round(px[-1] / px[0] - 1, 4)}
        print(f"  {ticker:10s} {len(sub):>5,}일 {sub.index[0].date()} ~ {sub.index[-1].date()} · "
              f"배당발생 {div_days:>4,}일 · 가격 {summary[ticker]['px_gain']:>+8.2%} → "
              f"TR {summary[ticker]['tr_gain']:>+8.2%}")

        if dry_run:
            continue
        iid = known.get(ticker)
        if iid is None:
            inst = Instrument(ticker=ticker, name=name, asset_type="index")
            db.add(inst)
            db.flush()
            iid = inst.id
            known[ticker] = iid
        for d, p, t in zip(sub.index, px, tr):
            rows.append(dict(instrument_id=iid, date=d.date(), period="D",
                             close=round(t, 6), raw_close=round(p, 6)))

    if dry_run:
        print("\n--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return {"tickers": len(summary), "rows": 0, "detail": summary}

    # 배당포인트가 사후 정정되므로 전 구간을 덮어쓴다 (증분 아님)
    ids = [known[t] for t in (v[0] for v in INDEX_MAP.values()) if t in known]
    deleted = db.execute(text("""delete from prices where period='D' and instrument_id = any(:ids)
                                 and date >= :s"""), {"ids": ids, "s": start}).rowcount
    for i in range(0, len(rows), BATCH_SIZE):
        stmt = pg_insert(Price).values(rows[i:i + BATCH_SIZE])
        db.execute(stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "period"],
            set_={"close": stmt.excluded.close, "raw_close": stmt.excluded.raw_close}))
    db.commit()
    print(f"\n기존 {deleted:,}행 삭제 후 {len(rows):,}행 적재")

    for r in db.execute(text("""select i.ticker, count(*), min(p.date), max(p.date)
        from prices p join instruments i on i.id=p.instrument_id
        where i.asset_type='index' and p.period='D' group by 1 order by 1""")).fetchall():
        print(f"  {r[0]:10s} {r[1]:>6,}행 {r[2]} ~ {r[3]}")
    db.close()
    return {"tickers": len(summary), "rows": len(rows), "detail": summary}


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def run(trigger="manual", start=None, end=None, dry_run=False) -> str:
    from app.models.batch_run import BatchRun

    start = start or DEFAULT_START
    end = end or datetime.date.today()

    db = SessionLocal()
    batch = BatchRun(job_name="benchmark_indices_bbg", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf, real = io.StringIO(), sys.stdout
    sys.stdout = _Tee(real, buf)
    status = "running"
    try:
        batch.summary = str(main(start, end, dry_run))
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
    p.add_argument("--from", dest="start", type=datetime.date.fromisoformat, default=DEFAULT_START)
    p.add_argument("--to", dest="end", type=datetime.date.fromisoformat, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--trigger", default="manual")
    a = p.parse_args()
    if run(a.trigger, a.start, a.end, a.dry_run) == "failed":
        sys.exit(1)
