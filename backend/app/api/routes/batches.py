import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.batch_run import BatchRun
from app.schemas.batch_run import BatchRunRead, BatchScheduleRead

router = APIRouter(prefix="/batches", tags=["batches"])

BACKEND_DIR = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = BACKEND_DIR / "scripts"

# job_name -> (스크립트 경로, 스케줄 설명). 스크립트는 daily_update.py와 동일한
# run(trigger) 패턴(BatchRun 기록 + stdout 캡처)을 구현하고 있어야 한다.
JOBS: dict[str, dict] = {
    "daily_update": {
        "script": SCRIPTS_DIR / "daily_update.py",
        "description": "KRX 장마감 후 가격/실제종가/상장주식수/휴장일/지수편입 갱신(당일+전영업일 재확인) + 파생데이터(월봉·배당조정지수) 재계산",
        "cron": "0 16 * * 1-5",
        "timezone": "Asia/Seoul",
    },
    "dividends_seibro": {
        "script": SCRIPTS_DIR / "load_dividends_seibro.py",
        "description": "SEIBRO 배당내역전체검색에서 최근 1주일 배당(배정기준일 포함) 조회·적재 + 영향 종목 배당조정지수 재계산",
        "cron": "30 16 * * 1-5",
        "timezone": "Asia/Seoul",
    },
}


@router.get("/schedule", response_model=list[BatchScheduleRead])
def list_schedule():
    return [
        BatchScheduleRead(job_name=name, description=j["description"], cron=j["cron"], timezone=j["timezone"])
        for name, j in JOBS.items()
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
        [sys.executable, str(job["script"]), "manual"],
        cwd=str(BACKEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"status": "started"}
