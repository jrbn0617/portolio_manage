"""KIS채권평가 채권지수 수집 — kis-net.kr 실시간지수정보(화면 1130).

Nexacro 14 앱이라 화면 HTML 에는 값이 없다. 브라우저 통신을 가로채 실제 요청을
확보했고, 그 형식을 그대로 쓴다. 요청·응답 모두 Nexacro 데이터셋 XML 이다.

**`flag` 가 일중/일별을 가른다.** 화면의 라디오 버튼에 대응한다:

    flag=0   일중 지수변동 — baseDate 하루치를 분 단위로 (약 330행)
    flag=1   일자별 지수변동 — baseFrom~baseTo 를 일 단위로

우리는 flag=1 만 쓴다. 한 번의 요청으로 전 구간이 오므로 매번 전체를 다시 받아
덮어쓴다 — 지수당 2,900행 안팎이고 요청은 1회다.

사이트가 주는 이력은 2015년부터다(30년 지수는 2016-03-10 산출 개시). 그 이전은
소스 DB 에서 백필한다 — `scripts/backfill_kr_bond_indices.py`.
"""
import datetime
import html
import re

import requests

URL = "https://kis-net.kr/indexInfo/realtimeIndexInfoList01.do"
PAGE = "https://kis-net.kr/kisnet/index.html"
TIMEOUT = 90

# 우리 티커 → (KIS 지수코드, 이름, 소스 DB 티커)
INDICES = {
    "KIS10Y": ("BRKTBI10", "KIS 10Y KTB Index", "KR10YTR"),
    "KIS30Y": ("BRKTBC30", "KIS-키움 국고채30년 지수", "KR30YTR"),
    "KISCD": ("BRCDIX01", "KIS CD Index(총수익)", "KRCD91TR"),
}

_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Root xmlns="http://www.nexacroplatform.com/platform/dataset">'
    '<Parameters><Parameter id="JSESSIONID" /></Parameters>'
    '<Dataset id="ds_search"><ColumnInfo>'
    '<ConstColumn id="pageIndex" type="INT" size="30" value="1" />'
    '<ConstColumn id="pageSize" type="INT" size="30" value="10" />'
    '<ConstColumn id="pageUnit" type="INT" size="30" value="10" />'
    '<Column id="indexCode" type="STRING" size="256" />'
    '<Column id="baseDate" type="STRING" size="256" />'
    '<Column id="baseFrom" type="STRING" size="256" />'
    '<Column id="baseTo" type="STRING" size="256" />'
    '<Column id="flag" type="STRING" size="256" />'
    '<Column id="tempKey" type="STRING" size="256" />'
    '</ColumnInfo><Rows><Row>'
    '<Col id="indexCode">{code}</Col><Col id="baseDate">{end}</Col>'
    '<Col id="baseFrom">{start}</Col><Col id="baseTo">{end}</Col>'
    '<Col id="flag">1</Col>'
    "</Row></Rows></Dataset>"
    '<Dataset id="cds_commTranInfo"><ColumnInfo>'
    '<Column id="strSvcID" type="STRING" size="32" />'
    '<Column id="strURL" type="STRING" size="32" />'
    '<Column id="strInDatasets" type="STRING" size="32" />'
    '<Column id="strOutDatasets" type="STRING" size="32" />'
    '<Column id="browserType" type="STRING" size="32" />'
    "</ColumnInfo><Rows><Row>"
    '<Col id="strSvcID">search</Col>'
    '<Col id="strURL">/indexInfo/realtimeIndexInfoList01.do</Col>'
    '<Col id="strInDatasets">ds_search=ds_search&#32;cds_commTranInfo=cds_commTranInfo</Col>'
    '<Col id="strOutDatasets">ds_list01=output1&#32;ds_list02=output2&#32;ds_list03=output3</Col>'
    '<Col id="browserType">Chrome</Col>'
    "</Row></Rows></Dataset></Root>"
)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Content-Type": "text/xml; charset=UTF-8",
    "Referer": PAGE,
}
_DATASET = re.compile(r'<Dataset id="output2">(.*?)</Dataset>', re.S)
_ROW = re.compile(r"<Row>(.*?)</Row>", re.S)
_COL = re.compile(r'<Col id="\w+">([^<]*)</Col>')


def fetch_index(ticker: str, start: datetime.date | None = None,
                end: datetime.date | None = None) -> list[tuple]:
    """일별 [(date, 총수익지수, 순가격지수 or None)]. 오래된 날짜부터 정렬해 돌려준다.

    응답은 최신순이고 열은 순서대로
    일자 · 총수익지수 · 순가격지수 · 시장가격지수 · … 이다.

    **순가격지수가 빈 문자열로 오는 지수가 있다** — CD 지수처럼 총수익만 산출하는
    계열이다. 없는 것이 정상이므로 None 으로 두고, 총수익지수만 없을 때 실패로 본다.
    """
    if ticker not in INDICES:
        raise ValueError(f"등록되지 않은 지수: {ticker} (가능: {list(INDICES)})")
    code = INDICES[ticker][0]
    start = start or datetime.date(1990, 1, 1)
    end = end or datetime.date.today()

    body = _BODY.format(code=code, start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    r = requests.post(URL, data=body.encode("utf-8"), headers=_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = "utf-8"

    m = _DATASET.search(r.text)
    if not m:
        raise RuntimeError(f"{ticker}: 응답에 output2 가 없습니다 — 요청 형식을 확인하세요.")

    out = []
    for row in _ROW.findall(m.group(1)):
        v = [html.unescape(x) for x in _COL.findall(row)]
        if len(v) < 2 or not v[0] or not v[1]:
            continue
        clean = float(v[2]) if len(v) > 2 and v[2] else None
        out.append((datetime.date.fromisoformat(v[0]), float(v[1]), clean))
    if not out:
        raise RuntimeError(f"{ticker}: 일별 데이터가 비어 있습니다 (flag=1).")
    return sorted(out)
