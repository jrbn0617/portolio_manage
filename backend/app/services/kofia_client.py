"""KOFIA 전자공시(dis.kofia.or.kr) 조회.

**호출 제한에 민감하다.** 전 종목 일별 기준가는 하루치가 한 번의 요청이므로,
백필할 때는 요청 사이에 넉넉한 간격을 둔다(운영 기준 10분에 1일치).
공시 목록은 1년치를 한 번에 받으므로 반복 호출이 필요 없다.

pfmSvcName 별 용도
  DISFundFTimeAnnSO   펀드공시검색 — 자산운용보고서 목록. 운용펀드/클래스 계층의 원천
  DISFundStdPriceSO   전 펀드 일별 기준가 (하루치)
  DISFundStdPrcStutSO 한 펀드의 기준가 전체 이력
  DISFundRdmpSO       결산·상환
  DISNewEstSO         신규 설정
"""
import datetime
from io import StringIO

import pandas as pd
import requests

BASE_URL = "https://dis.kofia.or.kr/proframeWeb/XMLSERVICES/"
_HEADERS = {
    "Host": "dis.kofia.or.kr",
    "Origin": "https://dis.kofia.or.kr",
    "Content-Type": "text/xml; charset=UTF-8",
}
TIMEOUT = 60


def _post(xml: str, referer: str) -> str:
    res = requests.post(BASE_URL, data=xml.encode("utf-8"),
                        headers={**_HEADERS, "Referer": referer}, timeout=TIMEOUT)
    res.raise_for_status()
    return res.text


def fetch_disclosure(base_dt: datetime.date | None = None, days: int = 365) -> pd.DataFrame:
    """펀드공시검색 — 자산운용보고서(공모펀드 2RF12 / MMF 2RF06) 목록.

    운용펀드-클래스 계층을 얻는 유일한 경로다. **응답 행 순서가 계층 정보이므로
    정렬하지 않고 그대로 돌려준다.**

    조회기간을 1년으로 잡아야 전부 나온다. 자산운용보고서를 한 번도 내지 않은 펀드
    (설정 후 1년 미만)는 목록에 없다 — 그 구간은 신규설정 목록으로 보완한다.
    """
    base_dt = base_dt or datetime.date.today()
    start_dt = base_dt - datetime.timedelta(days=days)

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
        <message>
          <proframeHeader>
            <pfmAppName>FS-DIS2</pfmAppName>
            <pfmSvcName>DISFundFTimeAnnSO</pfmSvcName>
            <pfmFnName>selectAnn</pfmFnName>
          </proframeHeader>
          <systemHeader></systemHeader>
            <DISFTimeAnnInsDTO>
            <uGb>1</uGb>
            <vStrtDt>{start_dt:%Y%m%d}</vStrtDt>
            <vEndDt>{base_dt:%Y%m%d}</vEndDt>
            <uCdList></uCdList>
            <gbOption>N</gbOption>
            <uRptList>'2RF12','2RF06'</uRptList>
            <tsCd>'2RF12','2RF06'</tsCd>
            <uRptAllYN>0</uRptAllYN>
            <companyCd></companyCd>
        </DISFTimeAnnInsDTO>
        </message>"""

    res = _post(xml, "https://dis.kofia.or.kr/websquare/index.jsp?w2xPath=/wq/fundann/"
                     "DISFundAnnSrch.xml&divisionId=MDIS01001000000000&serviceId=SDIS01001000000")
    df = pd.read_xml(StringIO(res), xpath=".//list")
    return df.rename(columns={"standardCd": "펀드코드", "uFundNm": "공시대상",
                              "koreanNm": "회사"})[["펀드코드", "공시대상", "회사"]]
