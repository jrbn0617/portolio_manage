"""데이터 입수 경로 — 어떤 데이터가 어디서 언제 들어오는지.

출처·스케줄은 `batches.JOBS`가 유일한 원천이다. 여기서 문자열을 다시 적으면 배치를
고쳤을 때 화면이 조용히 어긋난다. 이 모듈은 "데이터 종류 → 어느 배치가 채우나"의
연결과, 종류별 최신 수신일만 담당한다. 배치 하나가 여러 종류를 채우기도 한다
(daily_update → 시세·수급·지수편입).

화면 상단에 항상 떠 있는 정보라 **읽기 전용**이고 빨라야 한다.

성능 주의 — `MAX(date)`를 그냥 쓰면 안 된다. 큰 테이블의 인덱스가 전부
`(instrument_id, date, ...)` 형태라 date 선행 인덱스가 없어서 풀스캔이 걸린다
(investor_trading 2,080만행 기준 1,134ms). 종목별 최대값을 LATERAL 로 뽑아
합치면 같은 인덱스를 타서 9ms 로 끝난다. 인덱스를 새로 만들지 않으려는 선택이다.
"""
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes.batches import JOBS
from app.db.session import get_db
from app.models.batch_run import BatchRun
from app.schemas.data_source import DataSourceRead, DataSourceRun

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


def _lateral(table: str, col: str, where: str = "", inst: str = "") -> str:
    """종목별 MAX를 LATERAL로 구한 뒤 합친다 — (instrument_id, ...) 인덱스를 타게 하는 형태."""
    cond = f" AND {where}" if where else ""
    inst_cond = f" WHERE {inst}" if inst else ""
    return (f"SELECT MAX(s.m) FROM instruments i CROSS JOIN LATERAL ("
            f"SELECT MAX(t.{col}) m FROM {table} t WHERE t.instrument_id=i.id{cond}) s{inst_cond}")


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    job_name: str  # batches.JOBS의 키
    sql: str
    date_label: str
    cadence: str = "daily"  # daily | monthly | manual
    note: str | None = None
    # 배당은 배정기준일이 사전 공시돼 최신값이 미래다 — 지연 판정에서 빼고 기준일 계산에도 안 쓴다.
    forward_dated: bool = False


# 화면에 나오는 순서 = 하루 중 배치가 도는 순서.
SOURCES: list[Source] = [
    Source("prices", "주식 시세 (일봉)", "daily_update",
           _lateral("prices", "date", "t.period='D'", "i.asset_type='stock'"), "시세일"),
    Source("investor_trading", "투자자 수급", "daily_update",
           _lateral("investor_trading", "date"), "거래일"),
    Source("index_memberships", "지수 편입종목·업종분류", "daily_update",
           "SELECT MAX(as_of_date) FROM index_memberships", "기준일"),
    Source("dividends", "주식 배당", "dividends_seibro",
           _lateral("dividends", "ex_date", inst="i.asset_type='stock'"), "배정기준일",
           note="배정기준일은 사전 공시되므로 미래 날짜가 나올 수 있다. 수집 시점은 우측 최근 실행을 본다.",
           forward_dated=True),
    Source("short_selling", "공매도", "refresh_short_selling",
           _lateral("short_selling", "date"), "거래일"),
    Source("etf_prices", "ETF 시세", "refresh_etf_prices",
           _lateral("prices", "date", "t.period='D'", "i.asset_type='etf'"), "시세일"),
    Source("etf_dividends", "ETF 분배금", "etf_dividends_seibro",
           _lateral("dividends", "ex_date", inst="i.asset_type='etf'"), "배정기준일",
           forward_dated=True),
    Source("benchmark", "벤치마크 지수", "benchmark_indices_bbg",
           _lateral("prices", "date", "t.period='D'", "i.asset_type='index'"), "지수일"),
    Source("shares_outstanding", "상장주식수", "shares_outstanding_pykrx",
           _lateral("monthly_fundamentals", "date", "t.metric='shares_outstanding_monthly'"), "기준월",
           cadence="monthly"),
    Source("fund_navs", "공모펀드 기준가", "fund_kofia_daily",
           "SELECT MAX(base_dt) FROM fund_navs", "기준일"),
    Source("fund_adjusted_navs", "공모펀드 수정기준가", "fund_kofia_daily",
           "SELECT MAX(base_dt) FROM fund_adjusted_navs", "기준일"),
    Source("monthly_fundamentals", "월간 펀더멘털 (재무·유동비율)", "monthly_fundamentals_upload",
           _lateral("monthly_fundamentals", "date", "t.metric<>'shares_outstanding_monthly'"), "기준월",
           cadence="manual"),
    Source("macro", "매크로 지표", "macro_upload",
           "SELECT MAX(date) FROM macro_indicators", "기준일", cadence="manual"),
]


def _due_time(cron: str | None, now: datetime) -> datetime | None:
    """cron 문자열에서 오늘의 예정 시각을 뽑는다. `분 시 ...` 순서다."""
    if not cron:
        return None
    minute, hour = cron.split()[0], cron.split()[1]
    if not (minute.isdigit() and hour.isdigit()):
        return None
    return now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


@router.get("", response_model=list[DataSourceRead])
def list_data_sources(db: Session = Depends(get_db)):
    sources = [s for s in SOURCES if JOBS[s.job_name].enabled]
    jobs = {s.job_name for s in sources}
    runs: dict[str, BatchRun] = {}
    for job in jobs:
        run = (db.query(BatchRun).filter(BatchRun.job_name == job)
               .order_by(BatchRun.started_at.desc()).first())
        if run:
            runs[job] = run

    dates = {s.key: db.execute(text(s.sql)).scalar() for s in sources}

    # 지연 판정의 기준일 = 일 단위 데이터 중 가장 앞선 날짜. 달력이 아니라 실제 데이터에서
    # 뽑으므로 휴장일·연휴를 따로 계산할 필요가 없다. 어느 하나라도 최신이면 그날이 기준이 된다.
    daily = [dates[s.key] for s in sources
             if s.cadence == "daily" and not s.forward_dated and dates[s.key]]
    reference = max(daily) if daily else None

    now = datetime.now()
    out = []
    for s in sources:
        job = JOBS[s.job_name]
        run = runs.get(s.job_name)
        last_date = dates[s.key]
        stale, reason, pending = False, None, False
        if run and run.status == "failed":
            stale, reason = True, f"최근 실행 실패 ({run.started_at.date()})"
        elif s.cadence == "daily" and not s.forward_dated and reference:
            if last_date is None:
                stale, reason = True, "데이터 없음"
            elif last_date < reference:
                due = _due_time(job.cron, now)
                if due and now < due:
                    pending, reason = True, f"오늘 {due:%H:%M} 수집 예정"
                else:
                    stale, reason = True, f"{reference} 대비 뒤처짐"
        elif last_date is None:
            stale, reason = True, "데이터 없음"

        out.append(DataSourceRead(
            key=s.key, label=s.label, source=job.source, schedule=job.schedule,
            job_name=s.job_name, last_date=last_date, date_label=s.date_label,
            cadence=s.cadence, note=s.note or job.description,
            last_run=DataSourceRun(status=run.status, started_at=run.started_at,
                                   finished_at=run.finished_at, error=run.error) if run else None,
            stale=stale, pending=pending, stale_reason=reason,
        ))
    return out
