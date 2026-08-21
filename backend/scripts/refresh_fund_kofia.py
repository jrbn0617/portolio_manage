"""공모펀드 일별 수집 — KOFIA 직접 조회.

**요청 간격이 이 배치의 핵심 제약이다.** 전 펀드 기준가는 `하루치 = 1요청`이라
백필 일수가 곧 요청 수다. 호출 제한을 피하려고 요청 사이에 기본 10분을 쉰다.
따라서 하루 한 번 도는 정상 운영에서는 요청이 1건이고, 밀린 구간을 따라잡을 때만
오래 걸린다(20영업일이면 약 3시간).

하는 일
  1. 없는 거래일의 기준가를 오래된 날부터 채운다 (요청 사이 --delay)
  2. 결산·신규설정을 최근 구간으로 갱신 (기간 조회라 각 1요청)
  3. 새로 등장한 펀드코드를 funds 에 등록 (매핑은 비워 둔다)
  4. 이번에 값이 바뀐 펀드의 수정기준가만 다시 계산

수정기준가는 **바뀐 펀드만 전체 이력을 다시 계산**한다. price_fetcher 의 by_date
계산기가 어제 값을 승계하는 방식이라 어제 기준가가 없는 펀드의 누적계수를 1로
리셋해 버렸다 — 그 실패를 되풀이하지 않으려고 증분 승계를 쓰지 않는다.

사용법:
  python scripts/refresh_fund_kofia.py                    # 밀린 날짜 전부
  python scripts/refresh_fund_kofia.py --max-days 3       # 요청 3건까지만
  python scripts/refresh_fund_kofia.py --delay 60         # 간격 60초 (시험용)
  python scripts/refresh_fund_kofia.py --dry-run
"""
import argparse
import datetime
import io
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from psycopg2.extras import execute_values  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services.fund_classify import _SPECIAL_KEYWORDS, pension_type  # noqa: E402
from app.services.kofia_client import fetch_daily_price, fetch_newly, fetch_settlement  # noqa: E402
from app.services.market_calendar import is_market_holiday, resolve_batch_status  # noqa: E402

DEFAULT_DELAY_SEC = 600      # 10분 — 요청 간격
REBASE_LOCAL = 0.1           # compute_fund_adjusted_navs.REBASE 와 같은 값
LOOKBACK_DAYS = 14           # 결산·신규설정 재조회 구간
MAX_CATCHUP_DAYS = 60        # 한 번에 따라잡을 최대 일수

NAV_UPSERT = """
INSERT INTO fund_navs (fund_id, base_dt, nav, tax_base_nav, aum) VALUES %s
ON CONFLICT (fund_id, base_dt) DO UPDATE
SET nav=EXCLUDED.nav, tax_base_nav=EXCLUDED.tax_base_nav, aum=EXCLUDED.aum, updated_at=now()
"""
STL_UPSERT = """
INSERT INTO fund_settlements (fund_id, period_start_value, period_end_value, settlement_type,
    elapsed_days, inception_principal, nav, tax_base_nav, post_settlement_nav, ex_dividend_dt)
VALUES %s
ON CONFLICT (fund_id, period_end_value, settlement_type) DO UPDATE
SET period_start_value=EXCLUDED.period_start_value, elapsed_days=EXCLUDED.elapsed_days,
    inception_principal=EXCLUDED.inception_principal, nav=EXCLUDED.nav,
    tax_base_nav=EXCLUDED.tax_base_nav, post_settlement_nav=EXCLUDED.post_settlement_nav,
    ex_dividend_dt=EXCLUDED.ex_dividend_dt, updated_at=now()
"""


def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(v):
    s = str(v).strip().replace("-", "")
    return datetime.datetime.strptime(s, "%Y%m%d").date() if len(s) == 8 else None


def missing_dates(db, upto: datetime.date, max_days: int, reconfirm: bool = True) -> list:
    """**직전 적재일 + 그 다음 영업일부터 upto 까지.** 휴장일은 건너뛴다.

    직전 적재일을 다시 받는 이유 — KOFIA 는 공시 후에도 늦게 올라오는 펀드가 있다.
    실측 2026-08-20 은 11:00 에 26,224행이었는데 다음 날 다시 부르니 26,234행이었다.
    한 번 받은 날을 다시 안 보면 그 10행은 영영 안 들어온다. `daily_update` 가 KRX
    정정 때문에 전영업일을 매번 재확인하는 것과 같은 이유다.

    기준가 적재는 (fund_id, base_dt) 업서트라 다시 받아도 중복되지 않는다.
    대신 **요청이 하루 1건에서 2건으로 늘어난다**(각 36MB). 요청 간격이 이 배치의
    제약이므로 따라잡기 구간에서는 그만큼 더 걸린다.
    """
    last = db.execute(text("SELECT MAX(base_dt) FROM fund_navs")).scalar()
    if last is None:
        raise RuntimeError("fund_navs 가 비어 있습니다 — 백필을 먼저 하세요.")
    out = [last] if (reconfirm and not is_market_holiday(db, last)) else []
    d, fresh = last + datetime.timedelta(days=1), 0
    while d <= upto and fresh < max_days:
        if not is_market_holiday(db, d):
            out.append(d)
            fresh += 1
        d += datetime.timedelta(days=1)
    return out


def ensure_funds(db, raw, codes_names: dict) -> int:
    """새 펀드코드를 funds 에 등록한다. 계층 매핑은 공시목록을 봐야 하므로 비워 둔다
    (load_fund_master.py 를 주기적으로 돌리면 채워진다)."""
    known = {r[0] for r in db.execute(text("SELECT fund_code FROM funds"))}
    new = {c: n for c, n in codes_names.items() if c not in known}
    if not new:
        return 0
    with raw.cursor() as cur:
        execute_values(cur, """INSERT INTO funds (fund_code, name, is_manage_fund, special,
                                                  pension_type)
                               VALUES %s ON CONFLICT (fund_code) DO NOTHING""",
                       [(c, (n or c)[:200], False,
                         any(k in (n or "") for k in _SPECIAL_KEYWORDS), pension_type(n or ""))
                        for c, n in new.items()])
    raw.commit()
    print(f"  신규 펀드 등록 {len(new):,}건")
    return len(new)


def load_prices(db, raw, day: datetime.date) -> tuple:
    df = fetch_daily_price(day)
    if df.empty:
        return 0, set()
    codes_names = {str(r.fund_code).strip(): str(getattr(r, "name_kr", "") or "").strip()
                   for r in df.itertuples()}
    ensure_funds(db, raw, codes_names)
    fids = {r[0]: r[1] for r in db.execute(text("SELECT fund_code, id FROM funds"))}
    values, touched = [], set()
    for r in df.itertuples():
        code = str(r.fund_code).strip()
        fid = fids.get(code)
        if fid is None:
            continue
        nav = _num(r.nav)
        if nav is None:
            continue
        aum = _num(getattr(r, "aum", None))
        values.append((fid, day, nav, _num(getattr(r, "tax_base_nav", None)),
                       int(aum) if aum is not None else None))
        touched.add(fid)
    if values:
        with raw.cursor() as cur:
            execute_values(cur, NAV_UPSERT, values, page_size=5000)
        raw.commit()
    return len(values), touched


def load_settlements(db, raw, day: datetime.date, days: int) -> tuple:
    df = fetch_settlement(day, days)
    if df.empty:
        return 0, set()
    fids = {r[0]: r[1] for r in db.execute(text("SELECT fund_code, id FROM funds"))}
    seen, touched = {}, set()
    for r in df.itertuples():
        fid = fids.get(str(r.fund_code).strip())
        end = _date(r.period_end_value)
        if fid is None or end is None or not str(r.settlement_type).strip():
            continue
        stype = str(r.settlement_type).strip()
        # 배당락일 = 회계기말 다음 영업일
        ex = end + datetime.timedelta(days=1)
        while is_market_holiday(db, ex):
            ex += datetime.timedelta(days=1)
        elapsed = _num(r.elapsed_days)
        principal = _num(r.inception_principal)
        seen[(fid, end, stype)] = (
            fid, _date(r.period_start_value), end, stype,
            int(elapsed) if elapsed is not None else None,
            int(principal) if principal is not None else None,
            _num(r.nav), _num(r.tax_base_nav), _num(r.post_settlement_nav), ex)
        touched.add(fid)
    if seen:
        with raw.cursor() as cur:
            execute_values(cur, STL_UPSERT, list(seen.values()), page_size=5000)
        raw.commit()
    return len(seen), touched


def recompute_full(db, raw, fund_ids: set) -> int:
    """전체 이력을 다시 계산한다. 결산이 바뀐 펀드와 수정기준가가 아예 없는 펀드만."""
    if not fund_ids:
        return 0
    from scripts.compute_fund_adjusted_navs import (MAX_ADJ_NAV, MAX_FACTOR, SETTLEMENT_TYPES,
                                                    UPSERT, compute_one)
    ids = sorted(fund_ids)
    total = 0
    for i in range(0, len(ids), 300):
        ch = ids[i:i + 300]
        ph = ",".join(str(x) for x in ch)
        nav_df = pd.DataFrame(db.execute(text(
            f"SELECT fund_id, base_dt, nav FROM fund_navs WHERE fund_id IN ({ph}) "
            f"ORDER BY fund_id, base_dt")).all(), columns=["fund_id", "base_dt", "nav"])
        if nav_df.empty:
            continue
        stl_df = pd.DataFrame(db.execute(text(
            f"""SELECT fund_id, ex_dividend_dt, nav, post_settlement_nav FROM fund_settlements
                WHERE fund_id IN ({ph}) AND settlement_type IN :t AND ex_dividend_dt IS NOT NULL
                ORDER BY fund_id, period_end_value""").bindparams(t=SETTLEMENT_TYPES)).all(),
            columns=["fund_id", "ex_dividend_dt", "nav", "post"])
        nav_df["nav"] = nav_df["nav"].astype(float)
        if not stl_df.empty:
            stl_df["nav"] = stl_df["nav"].astype(float)
            stl_df["post"] = stl_df["post"].astype(float)
        stl_by = dict(tuple(stl_df.groupby("fund_id"))) if not stl_df.empty else {}
        values = []
        for fid, g in nav_df.groupby("fund_id"):
            s2 = stl_by.get(fid)
            stl = (s2.set_index("ex_dividend_dt")[["nav", "post"]] if s2 is not None
                   else pd.DataFrame(columns=["nav", "post"]))
            out = compute_one(g.set_index("base_dt")[["nav"]], stl)
            over = (out["adj_factor"].abs() >= MAX_FACTOR) | (out["adj_nav"].abs() >= MAX_ADJ_NAV)
            out = out[~over]
            values.extend((int(fid), d, round(float(r.nav), 4), round(float(r.adj_nav), 12),
                           round(float(r.adj_factor), 12)) for d, r in out.iterrows())
        if values:
            with raw.cursor() as cur:
                execute_values(cur, UPSERT, values, page_size=5000)
            raw.commit()
            total += len(values)
    return total


def extend_incremental(db, raw, since: datetime.date) -> tuple:
    """새로 들어온 날짜만 이어붙인다 — 전체 이력을 다시 계산하지 않는다.

    수정계수는 결산 락일에만 변하므로, 그 펀드의 **마지막으로 알려진 계수**에서 출발해
    새 날짜에 걸린 결산만 곱하면 된다.

    price_fetcher 의 by_date 계산기는 '어제 행'을 승계하다가, 어제 기준가가 없는 펀드를
    신규로 보고 계수를 1 로 리셋했다. 여기서는 어제가 아니라 **그 펀드의 최종 계수**를
    가져오므로 하루 빠져도 끊기지 않는다. 최종 계수가 아예 없는 펀드는 전체 계산으로 넘긴다.
    """
    from scripts.compute_fund_adjusted_navs import MAX_ADJ_NAV, MAX_FACTOR, UPSERT

    rows = db.execute(text("""
        SELECT v.fund_id, v.base_dt, v.nav FROM fund_navs v
        WHERE v.base_dt >= :since
          AND NOT EXISTS (SELECT 1 FROM fund_adjusted_navs a
                          WHERE a.fund_id = v.fund_id AND a.base_dt = v.base_dt)
        ORDER BY v.fund_id, v.base_dt"""), {"since": since}).all()
    if not rows:
        return 0, set()

    fids = sorted({r[0] for r in rows})
    ph = ",".join(str(x) for x in fids)
    last = {r[0]: (r[1], float(r[2])) for r in db.execute(text(f"""
        SELECT DISTINCT ON (fund_id) fund_id, base_dt, adj_factor FROM fund_adjusted_navs
        WHERE fund_id IN ({ph}) ORDER BY fund_id, base_dt DESC""")).all()}
    stl = {}
    for r in db.execute(text(f"""
            SELECT fund_id, ex_dividend_dt, nav, post_settlement_nav FROM fund_settlements
            WHERE fund_id IN ({ph}) AND settlement_type IN ('결산','분배')
              AND ex_dividend_dt >= :since AND post_settlement_nav > 0"""), {"since": since}):
        stl[(r[0], r[1])] = float(r[2]) / float(r[3])

    values, need_full, factors = [], set(), {}
    for fid, d, nav in rows:
        if fid not in last:
            need_full.add(fid)          # 기존 계수가 없다 → 전체 계산으로
            continue
        f = factors.get(fid, last[fid][1])
        f *= stl.get((fid, d), 1.0)
        factors[fid] = f
        adj = float(nav) * f * REBASE_LOCAL
        if abs(f) >= MAX_FACTOR or abs(adj) >= MAX_ADJ_NAV:
            continue
        values.append((int(fid), d, round(float(nav), 4), round(adj, 12), round(f, 12)))
    if values:
        with raw.cursor() as cur:
            execute_values(cur, UPSERT, values, page_size=5000)
        raw.commit()
    return len(values), need_full


def main(delay: int, max_days: int, lookback: int, dry_run: bool,
         until: datetime.date | None = None) -> dict:
    db = SessionLocal()
    raw = engine.raw_connection()
    summary = dict(days=0, nav_rows=0, settlements=0, newly=0, recomputed=0,
               empty_days=[], reconfirmed=None)
    try:
        last_loaded = db.execute(text("SELECT MAX(base_dt) FROM fund_navs")).scalar()
        days = missing_dates(db, until or datetime.date.today(), max_days)
        reconfirmed = bool(days) and days[0] == last_loaded
        summary["reconfirmed"] = str(last_loaded) if reconfirmed else None
        print(f"채울 거래일 {len(days)}일: {[str(d) for d in days[:5]]}"
              f"{' ...' if len(days) > 5 else ''}"
              f"{f' (첫 날 {last_loaded} 은 재확인)' if reconfirmed else ''}")
        if dry_run:
            return summary

        stl_touched = set()
        for i, d in enumerate(days):
            if i:
                print(f"  ({delay}초 대기 — KOFIA 호출 제한)", flush=True)
                time.sleep(delay)
            n, _ = load_prices(db, raw, d)
            summary["nav_rows"] += n
            summary["days"] += 1
            if n == 0:
                # 아직 공시 전이거나 휴장일이다. 다음 실행이 같은 날을 다시 집는다
                # (MAX(base_dt) 가 안 움직이므로) — 여기서 실패로 만들 일이 아니다.
                summary["empty_days"].append(str(d))
            tag = " (재확인)" if i == 0 and d == days[0] and reconfirmed else ""
            print(f"  [{i+1}/{len(days)}] {d} · 기준가 {n:,}행{tag}", flush=True)

        if days:
            time.sleep(delay)
            n, t = load_settlements(db, raw, days[-1], lookback)
            stl_touched |= t
            summary["settlements"] = n
            print(f"  결산 {n:,}건", flush=True)

            time.sleep(delay)
            nw = fetch_newly(days[-1], lookback)
            summary["newly"] = len(nw)
            print(f"  신규설정 {len(nw):,}건", flush=True)

        # 결산이 바뀐 펀드는 과거 계수까지 달라지므로 전체 이력을 다시 계산한다.
        n_full = recompute_full(db, raw, stl_touched)
        # 나머지는 새 날짜만 이어붙인다.
        n_inc, need_full = extend_incremental(db, raw, days[0] if days else datetime.date.today())
        n_full += recompute_full(db, raw, need_full)
        summary["recomputed"] = n_full + n_inc
        print(f"  수정기준가 · 증분 {n_inc:,}행 · 전체재계산 {n_full:,}행 "
              f"(결산변경 {len(stl_touched):,}펀드 + 계수없음 {len(need_full):,}펀드)")
    finally:
        raw.close()
        db.close()
    return summary


def run(trigger: str = "manual", delay: int = DEFAULT_DELAY_SEC, max_days: int = MAX_CATCHUP_DAYS,
        lookback: int = LOOKBACK_DAYS) -> str:
    """main()을 BatchRun 이력과 함께 실행한다 (daily_update.run 과 동일 패턴)."""
    from app.models.batch_run import BatchRun
    from scripts.daily_update import _Tee

    db = SessionLocal()
    batch = BatchRun(job_name="fund_kofia_daily", trigger=trigger, status="running")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    buf, real = io.StringIO(), sys.stdout
    sys.stdout = _Tee(real, buf)
    status = "running"
    try:
        batch.summary = str(main(delay, max_days, lookback, dry_run=False))
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
    p.add_argument("--delay", type=int, default=DEFAULT_DELAY_SEC, help="요청 간격(초)")
    p.add_argument("--max-days", type=int, default=MAX_CATCHUP_DAYS)
    p.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--until", type=datetime.date.fromisoformat, default=None,
                   help="이 날짜까지만 채운다 (기본: 오늘)")
    p.add_argument("--trigger", default=None, help="지정하면 BatchRun 이력을 남긴다")
    a = p.parse_args()
    if a.trigger:
        if run(a.trigger, a.delay, a.max_days, a.lookback) == "failed":
            sys.exit(1)
    else:
        print(main(a.delay, a.max_days, a.lookback, a.dry_run, a.until))
