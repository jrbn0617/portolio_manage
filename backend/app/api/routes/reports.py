"""분석 리포트 열람 — 스크립트가 만들어 둔 HTML을 그대로 내려준다.

리포트는 `reference/analysis/` 에 떨어진다(gitignore 대상). 웹앱에서 보려면 누군가는
그 파일을 읽어 줘야 하는데, **정적 마운트로 디렉터리를 통째로 열지 않는다** — 같은
폴더에 중간 산출 JSON 이 함께 있고, 나중에 무엇이 더 떨어질지 모른다. 아래 REPORTS 에
등록된 키만 내려주고, 경로는 코드에서 만든다(사용자 입력이 경로에 닿지 않는다).

생성은 이 API 가 하지 않는다 — `docs/analysis-reports.md` 의 스크립트가 만들고, 여기는
**마지막으로 만들어진 파일을 보여줄 뿐이다.** 그래서 `generated_at`(파일 mtime)을 같이
준다. 화면에서 "언제 만든 것인가"를 감출 수 없게 하려는 것이다.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/reports", tags=["reports"])

# analysis/algorithm1/mtd_viz.py 의 SP 와 같은 위치다(ALGO_OUT 은 스크립트 쪽 편의 기능이라
# 여기서는 보지 않는다 — 서버가 읽는 위치는 한 곳으로 고정한다).
OUT_DIR = Path(__file__).resolve().parents[4] / "reference" / "analysis"


@dataclass(frozen=True)
class Report:
    key: str
    label: str
    description: str
    filename: str
    track: str          # algorithm1 | allocation1
    kind: str           # performance | validation | document
    command: str        # 다시 만들려면 무엇을 돌리나 (화면에 그대로 보여 준다)


REPORTS: list[Report] = [
    Report("mtd", "월중 성과 (일별 갱신)",
           "직전 형성일 포트폴리오를 전일 종가까지 따라간 결과. 평일 19:00 배치가 갱신한다.",
           "mtd_viz.html", "algorithm1", "performance",
           "venv/bin/python analysis/algorithm1/mtd_viz.py"),
    Report("monthly", "월별 성과 이력",
           "마감된 달들의 확정 성과. 달마다 형성한 포트폴리오를 그 달 말까지 따라간 결과다.",
           "monthly_viz.html", "algorithm1", "performance",
           "venv/bin/python analysis/algorithm1/monthly_viz.py"),
    Report("walkforward", "워크포워드 검증",
           "설정값을 과거 구간에서만 고르고 다음 구간에 적용했을 때의 안정성.",
           "wf_viz.html", "algorithm1", "validation",
           "venv/bin/python analysis/algorithm1/build_wf_viz.py"),
    Report("proposal", "사내 제안서",
           "알고리즘 #1 개발 결과 보고. 로직 비공개 수준으로 변환돼 있다.",
           "proposal.html", "algorithm1", "document",
           "venv/bin/python analysis/algorithm1/build_proposal.py"),
    Report("cycle_switch", "배분 #1 개요·성과",
           "경기 사이클 스위치 전략의 국면 타임라인과 누적 성과.",
           "cycle_switch.html", "allocation1", "performance",
           "venv/bin/python analysis/allocation/cycle_switch_report.py"),
]

BY_KEY = {r.key: r for r in REPORTS}


def _meta(r: Report) -> dict:
    p = OUT_DIR / r.filename
    exists = p.is_file()
    return {
        "key": r.key, "label": r.label, "description": r.description,
        "track": r.track, "kind": r.kind, "command": r.command,
        "exists": exists,
        "generated_at": (datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
                         if exists else None),
        "size": p.stat().st_size if exists else None,
    }


@router.get("")
def list_reports() -> list[dict]:
    """등록된 리포트와 각각의 생성 시각. 아직 안 만든 것도 exists=false 로 함께 준다 —
    **목록에서 사라지면 '그런 리포트가 있다'는 사실까지 사라지기 때문이다.**"""
    return [_meta(r) for r in REPORTS]


@router.get("/{key}", response_class=HTMLResponse)
def get_report(key: str) -> HTMLResponse:
    r = BY_KEY.get(key)
    if r is None:
        raise HTTPException(404, f"등록되지 않은 리포트: {key}")
    p = OUT_DIR / r.filename
    if not p.is_file():
        raise HTTPException(
            404, f"아직 만들어지지 않았습니다. 생성: {r.command}")
    return HTMLResponse(_wrap(p.read_text(encoding="utf-8")))


def _wrap(html: str) -> str:
    """리포트 HTML 은 아티팩트 게시용이라 `<title>` 부터 시작하고 문서 골격이 없다.
    그대로 내려주면 브라우저가 쿼크스 모드로 렌더해 여백·글꼴이 어긋난다. 골격을 씌운다.

    이미 온전한 문서면 손대지 않는다 — 스크립트가 나중에 골격을 넣어도 이중으로
    감싸지 않게 하려는 것이다.
    """
    head = html.lstrip()[:120].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        return html
    # <title> 은 head 로 올린다. body 안의 title 은 브라우저가 무시해서 탭 이름이 URL 로 뜬다.
    m = re.match(r"\s*(<title>.*?</title>)", html, re.S | re.I)
    title, body = (m.group(1), html[m.end():]) if m else ("", html)
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'{title}</head><body>{body}</body></html>')
