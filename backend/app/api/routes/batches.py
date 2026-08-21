import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.batch_run import BatchRun
from app.schemas.batch_run import BatchRunRead, BatchScheduleRead

router = APIRouter(prefix="/batches", tags=["batches"])

BACKEND_DIR = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = BACKEND_DIR / "scripts"

KRX = "pykrx / KRX"
KOFIA = "KOFIA 전자공시"
SEIBRO = "SEIBRO"
BBG = "블룸버그 터미널"
DATAGUIDE = "DataGuide"
MACRO = "FRED / FINRA"
KOFR = "FRED / KOFR"
KIS = "KIS채권평가"
MANUAL = "수동 업로드"


@dataclass(frozen=True)
class Job:
    """스크립트는 daily_update.py와 동일한 run(trigger) 패턴(BatchRun 기록 + stdout 캡처)을
    구현하고 있어야 한다.

    trigger 인자 형태가 스크립트마다 다르다 — 위치인자(`manual`)를 쓰는 것과
    `--trigger manual` 플래그를 쓰는 것이 섞여 있다. 한쪽으로 통일하면 crontab도 같이
    고쳐야 해서, 여기서 스크립트별로 인자를 들고 있는다.
    """

    name: str
    description: str
    source: str
    cron: str | None
    schedule: str  # 사람이 읽는 형태
    script: str | None = None
    trigger_args: list[str] = field(default_factory=list)
    timezone: str = "Asia/Seoul"
    # 아직 쓰지 않는 데이터는 화면에서 감춘다. 지우지 않는 이유는 나중에 다시 열기 위해서다
    # — 정의를 지우면 어떤 경로로 들어오는 데이터였는지가 함께 사라진다.
    enabled: bool = True

    @property
    def runnable(self) -> bool:
        """수동 업로드로 들어오는 데이터는 돌릴 스크립트가 없다 — 실행 버튼도 없어야 한다."""
        return self.script is not None

    @property
    def path(self) -> Path:
        return SCRIPTS_DIR / self.script


# 하루 중 도는 순서. 마지막 두 건은 cron이 아니라 화면에서 파일을 올려 넣는다.
JOBS: dict[str, Job] = {
    j.name: j
    for j in [
        Job("daily_update",
            "KRX 장마감 후 가격/실제종가/상장주식수/휴장일/지수편입 갱신(당일+전영업일 재확인)"
            " + 파생데이터(월봉·배당조정지수) 재계산",
            KRX, "0 16 * * 1-5", "평일 16:00", "daily_update.py", ["manual"]),
        Job("dividends_seibro",
            "SEIBRO 배당내역전체검색에서 최근 1주일 배당(배정기준일 포함) 조회·적재"
            " + 영향 종목 배당조정지수 재계산",
            SEIBRO, "30 16 * * 1-5", "평일 16:30", "load_dividends_seibro.py", ["manual"]),
        Job("refresh_short_selling",
            "최근 5영업일 공매도 거래량·잔고 재수집",
            KRX, "0 17 * * 1-5", "평일 17:00", "refresh_short_selling.py", ["manual"]),
        Job("refresh_etf_prices",
            "ETF 시세 갱신 (거래일당 KRX 1회). 신규 상장 ETF의 instruments 등록도 이 배치가 한다"
            " — ETF 분배금 배치보다 반드시 먼저 돌아야 한다",
            KRX, "30 17 * * 1-5", "평일 17:30", "refresh_etf_prices.py", ["--trigger", "manual"]),
        Job("etf_dividends_seibro",
            "ETF 분배금 최근 7일 재조회. instruments에 없는 ETF는 건너뛴다",
            SEIBRO, "0 18 * * 1-5", "평일 18:00", "load_etf_dividends_seibro.py",
            ["--trigger", "manual"]),
        Job("benchmark_indices_bbg",
            "블룸버그 지수·환율 갱신. **수집 대상과 요청 방식은 지수관리 화면(bbg_indices)이 정한다**"
            " — 코스피 계열은 배당포인트가 사후 정정되므로 전 구간(2014~)을 통째로 갈아끼우고,"
            " 이미 총수익으로 나오는 해외지수·환율은 마지막 적재일 다음날부터만 받는다."
            " 사내 블룸버그 PC에 SSH로 붙으므로 그 PC가 꺼져 있으면 실패한다",
            BBG, "30 18 * * 1-5", "평일 18:30", "refresh_benchmark_indices_bbg.py",
            ["--trigger", "manual"]),
        Job("shares_outstanding_pykrx",
            "지난달 마지막 거래일 기준 상장주식수 적재",
            KRX, "0 6 1 * *", "매월 1일 06:00", "load_shares_outstanding_pykrx.py",
            ["--trigger", "manual"]),
        Job("fund_kofia_daily",
            "공모펀드 일별 기준가·결산·신규설정 수집 후 수정기준가 갱신."
            " KOFIA 는 전일 주식시장 종가를 반영해 당일 오전에 확정·공시하는데 시각이"
            " 일정하지 않다 — 11:00 에 돌렸더니 어떤 날은 나오고 어떤 날은 빈 응답이라"
            " 여유를 두고 13:00 으로 옮겼다(실측 2026-08-21)."
            " 공시가 늦어 빈 응답을 받아도 그날치는 다음 실행이 따라잡는다."
            " **직전 적재일을 매번 다시 받는다** — 공시 후 늦게 올라오는 펀드가 있어"
            " 한 번 받은 날을 다시 안 보면 그만큼 영영 빠진다(실측 8/20 이 26,224 →"
            " 26,234 행). 그래서 요청이 하루 2건이고 사이에 10분을 쉰다",
            KOFIA, "0 13 * * 1-5", "평일 13:00", "refresh_fund_kofia.py",
            ["--trigger", "manual"]),
        Job("kis_indices",
            "국내 채권지수 — KIS10Y(10년 국고채)·KIS30Y(국고채30년)·KISCD(CD 총수익)."
            " kis-net.kr 실시간지수정보(화면 1130)를 일자별(flag=1)로 부른다. 최근 2주만"
            " 다시 받아 덮어쓴다(전 구간은 --full) — 지수당 요청 1회씩이라 배치가"
            " 며칠 밀려도 다음 실행이 메운다. 2015 이전은 backfill_kr_bond_indices.py 로"
            " 채웠다(소스 DB). 장 마감 뒤 확정되므로 오후에 돈다",
            KIS, "10 16 * * 1-5", "평일 16:10", "refresh_kis_indices.py",
            ["--trigger", "manual"]),
        Job("macro_daily",
            "일별 매크로 지표 — FRED SOFRINDEX, KOFR KOFRINDEX. 매번 전 구간(2018~)을 다시 받아"
            " 덮어쓴다 — 각 2,100행 안팎에 요청은 1회씩이라, 배치가 며칠 밀려도 다음 실행이"
            " 알아서 메운다. KOFR 공시가 오전 10시 50분경이라 오후에 돈다."
            " 2018 이전 구간은 backfill_underlying_index.py 로 채웠다(소스 DB)",
            KOFR, "0 15 * * 1-5", "평일 15:00", "refresh_macro_daily.py",
            ["--trigger", "manual"]),
        Job("macro_monthly",
            "월간 매크로 지표 — FRED DGS10(10년물 월평균)·M2NS(M2 통화량), FINRA 마진부채."
            " 발표가 가장 늦은 게 FINRA(익월 중순)와 M2NS(익월 4주차)라 매월 25일에 돈다."
            " 세 계열 다 400행 미만이라 매번 전 구간을 다시 받아 덮어쓴다 — M2 는 사후"
            " 개정이 있어 증분으로 이어붙이면 과거가 틀어진 채 남는다."
            " 한 계열이 실패해도 나머지는 적재한다",
            MACRO, "0 7 25 * *", "매월 25일 07:00", "refresh_macro_monthly.py",
            ["--trigger", "manual"]),
        Job("monthly_fundamentals_upload",
            "재무·유동비율 등 월간 펀더멘털. DataGuide 요청 양식으로 받아 화면에서 올린다"
            " — 과거 시계열 백필은 pykrx로 수집하지 않는다",
            DATAGUIDE, None, "수동 요청"),
        # 매크로 지표는 아직 쓰지 않아 닫아 둔다 (2026-08-18). 쓸 때 enabled=True 로 되돌린다.
        Job("macro_upload", "매크로 지표. 화면에서 파일을 올려 넣는다", MANUAL, None, "수동 업로드",
            enabled=False),
    ]
}


@router.get("/schedule", response_model=list[BatchScheduleRead])
def list_schedule():
    return [
        BatchScheduleRead(job_name=j.name, description=j.description, source=j.source,
                          cron=j.cron, schedule=j.schedule, timezone=j.timezone,
                          runnable=j.runnable)
        for j in JOBS.values()
        if j.enabled
    ]


@router.get("/runs", response_model=list[BatchRunRead])
def list_runs(job_name: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    query = db.query(BatchRun)
    if job_name:
        query = query.filter(BatchRun.job_name == job_name)
    return query.order_by(BatchRun.started_at.desc()).limit(limit).all()


@router.get("/runs/{run_id}", response_model=BatchRunRead)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(BatchRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="실행 이력을 찾을 수 없습니다.")
    return run


@router.post("/{job_name}/run", status_code=202)
def trigger_job(job_name: str, db: Session = Depends(get_db)):
    job = JOBS.get(job_name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"등록되지 않은 배치입니다: {job_name}")
    if not job.runnable:
        raise HTTPException(status_code=400, detail="수동 업로드로 들어오는 데이터라 실행할 배치가 없습니다.")

    running = (
        db.query(BatchRun).filter(BatchRun.job_name == job_name, BatchRun.status == "running").first()
    )
    if running:
        raise HTTPException(status_code=409, detail="이미 실행 중인 배치가 있습니다.")

    # 각 스크립트가 자체적으로 run()에서 BatchRun 기록을 책임진다 — cron으로 실행하든
    # 여기서 수동으로 실행하든 동일한 경로를 타서 이력이 일관되게 남는다. 응답을 막지
    # 않도록 서브프로세스로 분리해 실행하고 즉시 반환한다. 프런트는 /batches/runs를
    # 폴링해 새로 생긴 실행 행의 진행 상태를 반영한다.
    subprocess.Popen(  # noqa: S603
        [sys.executable, str(job.path), *job.trigger_args],
        cwd=str(BACKEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"status": "started"}
