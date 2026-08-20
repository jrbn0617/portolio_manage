"""블룸버그 지수·환율을 받아 갱신한다 (cron 배치).

**수집 대상과 요청 방식은 `bbg_indices` 테이블이 정한다** (화면: 지수관리). 파일 이름에
benchmark 가 남아 있는 건 crontab 과 batch_runs 이력이 이 이름을 쓰고 있어서다 — 다루는
범위는 코스피 계열에 한정되지 않는다.

티커마다 요청 방식이 다르다:

  full   전 구간을 다시 받아 통째로 교체. 배당포인트가 사후 정정되는 코스피 계열용
  daily  마지막 적재일 다음날부터 오늘까지. 이미 총수익으로 나오는 해외지수·환율용

daily 를 "오늘 하루"가 아니라 "마지막 적재일 다음날부터"로 잡은 이유 — 블룸버그 시계열은
구간을 넓혀도 **요청 1회로 같다**. 배치가 하루 걸러도(터미널 PC가 꺼져 있으면 실패한다)
다음 실행이 알아서 메운다. 새로 등록해 이력이 하나도 없는 티커는 start_date 부터,
그것도 비어 있으면 당일치만 받는다.

**총수익지수를 직접 계산하는 경우 (compute_tr=True)** — 코스닥 계열은 TR 티커가 없고,
블룸버그의 `TOT_RETURN_INDEX_GROSS_DVDS` 는 **요청 시작일 기준으로 리베이스**돼서 구간을
바꾸면 레벨이 통째로 달라진다(증분 갱신을 하면 경계에서 수익률이 왜곡된다). 그래서
리베이스가 없는 두 필드,

  PX_LAST               가격지수
  INDX_GROSS_DAILY_DIV  일별 배당포인트 (세전)

만 받아서 아래 식으로 총수익지수를 만든다:

    TR_t = TR_{t-1} x (P_t + D_t) / P_{t-1}

이 식이 맞는지 블룸버그 TOT_RETURN_INDEX_GROSS_DVDS 와 대조해 소수점까지 일치하는 것을
확인했다 (KOSPI2 2026-08-06: 1038.59 x (982.92+0.12163)/1038.59 = 983.0416 = BBG 값).

기준값은 구간 첫날의 PX_LAST로 잡는다(블룸버그 관행과 동일). 지수 레벨 자체는 기준일에
따라 달라지지만 **수익률은 불변**이라 백테스트 결과에 영향이 없다. 이 계산은 전 구간을
한 번에 이어붙여야 성립하므로 **full 모드에서만 쓴다**.

저장 위치:
  prices.close      우리가 쓸 값 — compute_tr 이면 TR, 아니면 PX_LAST 그대로
  prices.raw_close  PX_LAST (원본)

사용법:
  python scripts/refresh_benchmark_indices_bbg.py --dry-run
  python scripts/refresh_benchmark_indices_bbg.py
  python scripts/refresh_benchmark_indices_bbg.py --ticker LEGATRUU --from 2010-01-01
"""
import argparse
import datetime
import io
import sys
import traceback
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.bbg_index import DAILY, FULL, BbgIndex  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.price import Price  # noqa: E402
from app.services.bbg import fetch_bbg_timeseries  # noqa: E402
from app.services.market_calendar import resolve_batch_status  # noqa: E402

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


def load_configs(db, tickers=None) -> list:
    q = db.query(BbgIndex).filter(BbgIndex.enabled.is_(True))
    if tickers:
        q = q.filter(BbgIndex.ticker.in_(tickers))
    rows = q.order_by(BbgIndex.sort_order, BbgIndex.ticker).all()
    if not rows:
        raise RuntimeError("수집 대상이 없습니다 — bbg_indices 를 확인하세요 (화면: 지수관리).")
    bad = [c.ticker for c in rows if c.compute_tr and c.refresh_mode != FULL]
    if bad:
        # TR 은 구간 첫날을 기준으로 이어붙이는 계산이라 증분으로 나눠 받으면 레벨이 어긋난다.
        raise RuntimeError(f"compute_tr 은 full 모드에서만 쓸 수 있습니다: {', '.join(bad)}")
    return rows


def last_loaded(db) -> dict:
    """지수별 마지막 적재일. daily 모드의 요청 시작일을 여기서 정한다."""
    rows = db.execute(text("""
        SELECT i.ticker, MAX(p.date) FROM prices p
        JOIN instruments i ON i.id = p.instrument_id
        WHERE i.asset_type = 'index' AND p.period = 'D' GROUP BY 1""")).all()
    return {r[0]: r[1] for r in rows}


def request_window(configs, last, today, override_start, override_end):
    """(start, end). daily 는 이력 다음날부터, full 은 start_date 부터."""
    end = override_end or today
    if override_start:
        return override_start, end
    starts = []
    for c in configs:
        if c.refresh_mode == FULL:
            starts.append(c.start_date or DEFAULT_START)
        else:
            prev = last.get(c.ticker)
            starts.append(prev + datetime.timedelta(days=1) if prev
                          else (c.start_date or today))
    return min(starts), end


def main(override_start, override_end, dry_run: bool, tickers=None) -> dict:
    db = SessionLocal()
    configs = load_configs(db, tickers)
    today = datetime.date.today()
    last = last_loaded(db)

    # 요청 방식과 필드가 같은 것끼리 묶어 한 번에 부른다 — 블룸버그 호출을 줄이려는 것이다.
    groups = defaultdict(list)
    for c in configs:
        groups[(c.refresh_mode, c.fields)].append(c)

    known = {t: i for t, i in db.query(Instrument.ticker, Instrument.id)
             .filter(Instrument.asset_type == "index").all()}
    summary, rows, full_ids, full_start = {}, [], [], None

    for (mode, fields), group in sorted(groups.items()):
        start, end = request_window(group, last, today, override_start, override_end)
        if start > end:
            print(f"[{mode}] 이미 {end} 까지 적재됨 — 건너뜀")
            continue
        field_list = [f.strip() for f in fields.split(",") if f.strip()]
        print(f"[{mode}] {start} ~ {end} · {len(group)}종목 · {fields}")
        df = fetch_bbg_timeseries([c.bbg_ticker for c in group], start, end, field_list)
        if df is None or df.empty:
            if mode == FULL:
                raise RuntimeError("블룸버그 응답이 비었습니다 — SSH/터미널 상태를 확인하세요.")
            # daily 는 휴장일이면 정상적으로 빈 응답이 온다.
            print("  응답 없음 (휴장일이거나 아직 미확정)")
            continue

        for c in group:
            pcol, dcol = f"{PRICE_FIELD}|{c.bbg_ticker}", f"{DIV_FIELD}|{c.bbg_ticker}"
            if pcol not in df.columns:
                print(f"  {c.ticker:10s} 응답에 {pcol} 없음 — 건너뜀")
                continue
            sub = df[[pcol] + ([dcol] if dcol in df.columns else [])].dropna(subset=[pcol])
            if sub.empty:
                print(f"  {c.ticker:10s} 값 없음 — 건너뜀")
                continue
            px = sub[pcol].tolist()
            if c.compute_tr:
                dv = sub[dcol].tolist() if dcol in sub.columns else [0.0] * len(px)
                vals = build_total_return(px, dv)
                div_days = sum(1 for d in dv if d == d and d > 0)
            else:
                vals, div_days = px, 0
            summary[c.ticker] = {"days": len(sub), "from": str(sub.index[0].date()),
                                 "to": str(sub.index[-1].date()), "div_days": div_days,
                                 "gain": round(vals[-1] / vals[0] - 1, 4) if len(vals) > 1 else 0.0}
            print(f"  {c.ticker:10s} {len(sub):>5,}일 {sub.index[0].date()} ~ "
                  f"{sub.index[-1].date()} · {summary[c.ticker]['gain']:>+8.2%}"
                  + (f" · 배당발생 {div_days:,}일" if c.compute_tr else ""))

            if dry_run:
                continue
            iid = known.get(c.ticker)
            if iid is None:
                inst = Instrument(ticker=c.ticker, name=c.name, asset_type="index")
                db.add(inst)
                db.flush()
                iid = known[c.ticker] = inst.id
            if mode == FULL:
                full_ids.append(iid)
                full_start = start if full_start is None else min(full_start, start)
            for d, p, v in zip(sub.index, px, vals):
                rows.append(dict(instrument_id=iid, date=d.date(), period="D",
                                 close=round(v, 6), raw_close=round(p, 6)))

    if dry_run:
        print("\n--dry-run 이므로 변경하지 않고 종료합니다.")
        db.close()
        return {"tickers": len(summary), "rows": 0, "detail": summary}

    # full 대상만 구간을 비우고 다시 넣는다. 배당포인트가 사후 정정되므로 증분이 아니다.
    deleted = 0
    if full_ids:
        deleted = db.execute(text("""delete from prices where period='D'
                                     and instrument_id = any(:ids) and date >= :s"""),
                             {"ids": full_ids, "s": full_start}).rowcount
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


def run(trigger="manual", start=None, end=None, dry_run=False, tickers=None) -> str:
    from app.models.batch_run import BatchRun

    db = SessionLocal()
    batch = BatchRun(job_name="benchmark_indices_bbg", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf, real = io.StringIO(), sys.stdout
    sys.stdout = _Tee(real, buf)
    status = "running"
    try:
        batch.summary = str(main(start, end, dry_run, tickers))
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
    p.add_argument("--from", dest="start", type=datetime.date.fromisoformat, default=None,
                   help="지정하면 모든 대상을 이 날짜부터 받는다 (기본: 모드별 자동)")
    p.add_argument("--to", dest="end", type=datetime.date.fromisoformat, default=None)
    p.add_argument("--ticker", action="append", help="특정 지수만 (여러 번 지정 가능)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--trigger", default="manual")
    a = p.parse_args()
    if run(a.trigger, a.start, a.end, a.dry_run, a.ticker) == "failed":
        sys.exit(1)
