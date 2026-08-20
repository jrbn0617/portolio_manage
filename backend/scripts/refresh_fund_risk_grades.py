"""펀드 투자위험등급 수집 — DART 투자설명서. 운용펀드만.

**같은 접수번호면 다시 읽지 않는다.** 투자설명서는 정정될 때만 나오므로, 한 번 읽어
두면 새 공시가 뜰 때까지 그대로 쓴다. 목록 조회(운용사당 1회)는 매번 하고, 문서 본문은
접수번호가 바뀐 펀드만 받는다.

호출량 — 운용사 수만큼 목록 조회 + 갱신된 펀드 수만큼 문서 조회. 27개 펀드를 처음
채우면 30회 안팎이고, 그 뒤로는 거의 0이다. DART Open API 는 일 20,000건이다.

**못 찾으면 비워 두지 않고 이유를 적는다.** 조용히 비어 있으면 다음에 원인을 다시 판다.

사용법:
  python scripts/refresh_fund_risk_grades.py --code K55301BO7424 --code KR5101521619
  python scripts/refresh_fund_risk_grades.py --from-picks universe.csv
  python scripts/refresh_fund_risk_grades.py --code ... --force
"""
import argparse
import datetime
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "analysis" / "allocation"))
load_dotenv(BACKEND_DIR / ".env")

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.fund_risk_grade import FundRiskGrade  # noqa: E402
from app.services import dart_client as dart  # noqa: E402

SLEEP = 0.4      # DART 부담을 줄이는 최소 간격
MAX_TRIES = 6    # 원본 없는 공시를 건너뛰며 볼 최대 건수


def pick_codes(universe: str, base: str, top: int) -> list[str]:
    """자산군별 상위 N개 펀드코드. fund_picking 의 규칙을 그대로 쓴다."""
    from fund_picking import load_universe, pick
    res = pick(load_universe(universe), base)
    out = []
    for name, r in res.items():
        for code in r["table"].head(top)["fund_code"].tolist():
            if code not in out:
                out.append(code)
    return out


def run(codes: list[str], force: bool = False) -> None:
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT fund_code, name, manage_company FROM funds
            WHERE fund_code = ANY(:c) AND is_manage_fund"""), {"c": codes}).all()
        found = {r[0] for r in rows}
        for miss in [c for c in codes if c not in found]:
            print(f"  {miss:<14} 운용펀드가 아니거나 DB 에 없음 — 건너뜀")

        cached = {r.fund_code: r for r in db.query(FundRiskGrade).filter(
            FundRiskGrade.fund_code.in_([r[0] for r in rows])).all()}

        # 운용사별로 묶어 목록 조회를 한 번만 한다
        by_company: dict[str, list] = {}
        for code, name, company in rows:
            by_company.setdefault(company, []).append((code, name))

        for company, funds in sorted(by_company.items()):
            corp = dart.company_code(company)
            if corp is None:
                for code, name in funds:
                    _save(db, code, note=f"DART 에서 운용사를 못 찾음: {company}")
                    print(f"  {code:<14} 운용사 미매칭 — {company}")
                continue

            reports = dart.list_prospectus(corp)
            time.sleep(SLEEP)
            print(f"  [{company}] corp_code={corp} · 투자설명서 {len(reports):,}건")

            for code, name in funds:
                matches = dart.find_matches(reports, name)
                if not matches:
                    _save(db, code, note=f"투자설명서에서 펀드명 매칭 실패: {name}")
                    print(f"    {code:<14} 매칭 실패  {name[:40]}")
                    continue

                prev = cached.get(code)
                # 목록의 최신 접수번호와 비교한다 — 우리가 읽어낸 건과 다를 수 있다.
                if not force and prev and prev.latest_rcept_no == matches[0]["rcept_no"] \
                        and prev.risk_grade is not None:
                    print(f"    {code:<14} 캐시 유지 {prev.risk_grade}등급 "
                          f"(접수 {prev.rcept_dt})  {name[:34]}")
                    continue

                # 원본이 없는 공시가 있어 최신순으로 내려가며 읽는다. 6건까지 보는 이유 —
                # 정정본이 연달아 원본 없이 접수된 펀드가 실제로 있었다(신한골드·한국투자퇴직연금).
                grade = label = hit = None
                skipped = 0
                for cand in matches[:MAX_TRIES]:
                    try:
                        xml = dart.fetch_document(cand["rcept_no"])
                    except dart.NoDocument:
                        skipped += 1
                        time.sleep(SLEEP)
                        continue
                    finally:
                        time.sleep(SLEEP)
                    grade, label = dart.parse_risk_grade(xml)
                    hit = cand
                    if grade:
                        break

                if hit is None:
                    _save(db, code, latest=matches[0]["rcept_no"],
                          note=f"원본 파일이 있는 투자설명서가 없음 ({skipped}건 시도)")
                    print(f"    {code:<14} 원본 없음  {name[:40]}")
                    continue

                _save(db, code, grade=grade, label=label, hit=hit,
                      dart_name=dart.fund_name_of(hit["report_nm"]),
                      latest=matches[0]["rcept_no"],
                      note=None if grade else "문서에서 투자위험등급을 못 찾음")
                mark = f"{grade}등급 {label or ''}".strip() if grade else "등급 못 읽음"
                tail = f" (원본없음 {skipped}건 건너뜀)" if skipped else ""
                print(f"    {code:<14} {mark:<16} 접수 {hit['rcept_dt']}  {name[:32]}{tail}")
        db.commit()
    finally:
        db.close()


def _save(db, code: str, grade=None, label=None, hit=None, dart_name=None, note=None,
          latest=None) -> None:
    row = db.query(FundRiskGrade).filter(FundRiskGrade.fund_code == code).first()
    if row is None:
        row = FundRiskGrade(fund_code=code)
        db.add(row)
    row.risk_grade, row.risk_label, row.note = grade, label, note
    row.dart_fund_name = dart_name
    row.latest_rcept_no = latest
    row.checked_at = datetime.datetime.now(datetime.timezone.utc)
    if hit:
        row.rcept_no = hit["rcept_no"]
        row.report_nm = hit["report_nm"]
        row.rcept_dt = datetime.date.fromisoformat(
            f"{hit['rcept_dt'][:4]}-{hit['rcept_dt'][4:6]}-{hit['rcept_dt'][6:]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--code", action="append", help="펀드코드 (여러 번 지정 가능)")
    p.add_argument("--from-picks", help="유니버스 CSV — 자산군별 상위 N개를 대상으로")
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--base", default="2026-07-31")
    p.add_argument("--force", action="store_true", help="캐시를 무시하고 다시 읽는다")
    a = p.parse_args()

    codes = list(a.code or [])
    if a.from_picks:
        codes += [c for c in pick_codes(a.from_picks, a.base, a.top) if c not in codes]
    if not codes:
        p.error("--code 또는 --from-picks 가 필요합니다.")

    print(f"대상 {len(codes)}개 펀드\n")
    run(codes, a.force)
