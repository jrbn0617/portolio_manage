"""DART 전자공시 Open API — 펀드 투자설명서에서 투자위험등급을 읽는다.

**화면 스크래핑이 아니라 공식 Open API 다.** dart.fss.or.kr 을 긁는 경로도 확인해 뒀지만
(단축코드 = `fund_code[-6:-1]` → `/corp/searchFundL.ax` → `/dsab005/search.ax`), 차단
위험이 없고 문서를 XML 원문 한 덩어리로 주는 이쪽이 낫다.

    GET /api/list.json      pblntf_ty=G(펀드공시), corp_code=운용사
    GET /api/document.xml   rcept_no       → ZIP 안에 XML 1개
    GET /api/corpCode.xml                  → 전체 법인 고유번호 (3.6MB)

**`corp_code` 는 운용사이지 펀드가 아니다.** 펀드는 `report_nm` 괄호 안에 들어 있다:

    [기재정정]투자설명서(집합투자증권)(한국밸류코리아리레이팅증권투자신탁(주식))

그래서 운용사로 목록을 받고 펀드명으로 골라낸다. 표기가 우리와 달라(공백·괄호·기호)
정규화해서 비교하고, **애매하면 고르지 않는다** — 엉뚱한 등급을 붙이는 것보다 낫다.

등급은 문서 앞머리에 있다:

    투자위험등급 2등급[ 높은 위험 ]
    1 2 3 4 5 6 / 매우높은위험 높은위험 다소높은위험 보통위험 낮은위험 매우낮은위험
"""
import datetime
import io
import os
import re
import zipfile

import requests

BASE = "https://opendart.fss.or.kr/api"
TIMEOUT = 180
PBLNTF_FUND = "G"          # 펀드공시

# 우리 운용사명 → DART corp_name. 표기가 다른 것만 적는다(실측 3건).
COMPANY_ALIAS = {
    "비엔케이자산운용": "BNK자산운용",
    "케이비자산운용": "KB자산운용",
}

# 표기가 문서마다 다르다 — 실측으로 "투자위험등급2등급[ 높은 위험 ]" 과
# "투자 위험 등급5등급(낮은 위험)" 이 둘 다 나왔다. 단어 사이 공백과 괄호 종류를 다 받는다.
_GRADE = re.compile(r"투자\s*위험\s*등급\s*(\d)\s*등급\s*[\[(]\s*([^\])\[(]{0,20}?)\s*[\])]")
_GRADE_LOOSE = re.compile(r"위험\s*등급\s*[:：]?\s*(\d)\s*등급")
# report_nm = "[기재정정]투자설명서(집합투자증권)(펀드명…)" — 앞의 말머리와 서류종류를
# 떼어내야 펀드명만 남는다. 탐욕 매칭으로 통째로 잡으면 "집합투자증권)(펀드명" 이 나온다.
_TITLE_PREFIX = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?투자설명서\s*\([^)]*\)\s*")
_NORM = re.compile(r"[\s\(\)\[\]{}·・,，.\-_/'\"]+")
# 우리(KOFIA) 이름 끝의 계층 표시 — DART 에는 없다
_OUR_SUFFIX = re.compile(r"\((?:모|운용|종류|종류형)\)\s*$")
# 일련번호 표기가 다르다 — 우리 " 1", DART "1호" / "제1호" / "(제1호)"
_SERIES = re.compile(r"제?\s*(\d+)\s*호")


def _key() -> str:
    k = os.environ.get("DART_API_KEY")
    if not k:
        raise RuntimeError("DART_API_KEY 환경변수가 없습니다 (backend/.env 확인)")
    return k


def normalize(name: str, loose: bool = False) -> str:
    """비교용 정규화. 기호를 없애기 전에 표기 차이부터 흡수한다.

    실측으로 걸린 것들 —
      우리 "…증권자투자신탁 1(주식)(모)"  vs  DART "…증권자투자신탁1호(주식)"
      우리 "…증권자투자신탁 1[주식]"      vs  DART "…증권자투자신탁(제1호)[주식]"

    loose 는 우리 이름에서 "증권"이 통째로 빠진 경우를 위한 것이다(실측 —
    "IBK퇴직연금가치형롱숏40자투자신탁" vs "…40증권자투자신탁"). 다른 펀드를 같은
    이름으로 만들 수 있어 기본으로 켜지 않고, 정상 매칭이 실패했을 때만 쓴다.
    """
    s = (name or "").strip()
    s = _OUR_SUFFIX.sub("", s)      # (모)·(운용)·(종류) 제거
    s = _SERIES.sub(r"\1", s)       # 1호 / 제1호 → 1
    s = _NORM.sub("", s).replace("주식회사", "").lower()
    return s.replace("증권", "") if loose else s


def fund_name_of(report_nm: str) -> str | None:
    """report_nm 에서 펀드명만. 바깥 괄호 한 겹까지 벗긴다."""
    body = _TITLE_PREFIX.sub("", report_nm or "").strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()
    return body or None


# ── 법인 고유번호 ────────────────────────────────────────────────────────────
_corp_cache: dict[str, str] | None = None


def corp_codes() -> dict[str, str]:
    """DART corp_name → corp_code. 3.6MB 라 프로세스에 한 번만 받는다."""
    global _corp_cache
    if _corp_cache is not None:
        return _corp_cache
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": _key()}, timeout=TIMEOUT)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml = z.read(z.namelist()[0]).decode("utf-8")
    out = {}
    for e in re.findall(r"<list>(.*?)</list>", xml, re.S):
        nm = re.search(r"<corp_name>(.*?)</corp_name>", e)
        cd = re.search(r"<corp_code>(.*?)</corp_code>", e)
        if nm and cd:
            out[nm.group(1).strip()] = cd.group(1).strip()
    _corp_cache = out
    return out


def company_code(company: str) -> str | None:
    """우리 운용사명 → DART corp_code. 별칭과 정규화 매칭까지 본다."""
    codes = corp_codes()
    for cand in (COMPANY_ALIAS.get(company, company), company):
        if cand in codes:
            return codes[cand]
    target = normalize(company)
    hits = [c for n, c in codes.items() if normalize(n) == target]
    return hits[0] if len(hits) == 1 else None


# ── 공시 목록 ────────────────────────────────────────────────────────────────
def list_prospectus(corp_code: str, years: int = 3) -> list[dict]:
    """운용사의 투자설명서 목록 (최신순). 페이지를 끝까지 넘긴다."""
    end = datetime.date.today()
    bgn = end - datetime.timedelta(days=365 * years)
    out, page = [], 1
    while True:
        r = requests.get(f"{BASE}/list.json", timeout=TIMEOUT, params={
            "crtfc_key": _key(), "corp_code": corp_code, "pblntf_ty": PBLNTF_FUND,
            "bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
            "page_no": page, "page_count": 100})
        r.raise_for_status()
        j = r.json()
        if j.get("status") == "013":       # 조회된 데이터가 없음
            break
        if j.get("status") != "000":
            raise RuntimeError(f"DART list.json 오류 {j.get('status')}: {j.get('message')}")
        out += [x for x in j.get("list", []) if "투자설명서" in x.get("report_nm", "")]
        if page >= int(j.get("total_page", 1)):
            break
        page += 1
    return sorted(out, key=lambda x: x["rcept_dt"], reverse=True)


def find_matches(reports: list[dict], fund_name: str) -> list[dict]:
    """펀드명이 맞는 투자설명서를 최신순으로. 못 좁히면 빈 목록.

    최신 한 건만 돌려주지 않는 이유 — 접수는 됐는데 **원본 파일이 없는 공시가 있다**
    (document.xml 이 status 014 로 답한다). 그때는 그다음 최신 건으로 내려가야 한다.
    """
    target = normalize(fund_name)
    exact = [r for r in reports if normalize(fund_name_of(r["report_nm"]) or "") == target]
    if exact:
        return exact
    # 표기 차이를 흡수하지 못했을 때 — 한쪽이 다른 쪽을 포함하는 경우만 받는다
    loose = [r for r in reports
             if (n := normalize(fund_name_of(r["report_nm"]) or "")) and
             (n in target or target in n)]
    names = {normalize(fund_name_of(r["report_nm"]) or "") for r in loose}
    if len(names) == 1:
        return loose
    # 마지막 수단 — "증권"을 뺀 비교. 후보 이름이 정확히 하나로 좁혀질 때만 받는다.
    t2 = normalize(fund_name, loose=True)
    cand = [r for r in reports
            if normalize(fund_name_of(r["report_nm"]) or "", loose=True) == t2]
    uniq = {fund_name_of(r["report_nm"]) for r in cand}
    return cand if len(uniq) == 1 else []


# ── 문서 본문 ────────────────────────────────────────────────────────────────
class NoDocument(Exception):
    """접수는 됐는데 원본 파일이 없는 공시 (status 014)."""


def fetch_document(rcept_no: str) -> str:
    """공시서류 원본(ZIP 안의 XML)을 텍스트로."""
    r = requests.get(f"{BASE}/document.xml", timeout=TIMEOUT,
                     params={"crtfc_key": _key(), "rcept_no": rcept_no})
    r.raise_for_status()
    if r.content[:2] != b"PK":
        if "<status>014</status>" in r.text:
            raise NoDocument(rcept_no)
        raise RuntimeError(f"{rcept_no}: ZIP 이 아닌 응답 — {r.text[:200]}")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = z.read(z.namelist()[0])
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_risk_grade(xml: str) -> tuple[int | None, str | None]:
    """(등급, 문구). 1=매우높은위험 … 6=매우낮은위험."""
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml))
    m = _GRADE.search(txt)
    if m:
        return int(m.group(1)), (m.group(2) or "").strip() or None
    m = _GRADE_LOOSE.search(txt)
    return (int(m.group(1)), None) if m else (None, None)
