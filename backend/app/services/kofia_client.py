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


def fetch_daily_price(base_dt: datetime.date) -> pd.DataFrame:
    """하루치 전 펀드 기준가. **1일 = 1요청**이라 백필할 때 요청 수가 곧 일수다.

    반환: base_dt, fund_code, name_kr, nav, tax_base_nav, aum
    응답이 비면 휴장일이거나 아직 공시 전이다 — 호출자가 판단한다."""
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
        <message>
          <proframeHeader>
            <pfmAppName>FS-DIS2</pfmAppName>
            <pfmSvcName>DISFundStdPriceSO</pfmSvcName>
            <pfmFnName>select</pfmFnName>
          </proframeHeader>
          <systemHeader></systemHeader>
            <DISCondFuncDTO>
            <tmpV30>{base_dt:%Y%m%d}</tmpV30>
            <tmpV3></tmpV3><tmpV4></tmpV4><tmpV7></tmpV7><tmpV5></tmpV5>
            <tmpV11></tmpV11><tmpV12></tmpV12><tmpV50></tmpV50><tmpV51></tmpV51>
        </DISCondFuncDTO>
        </message>"""
    res = _post(xml, "https://dis.kofia.or.kr/websquare/index.jsp?w2xPath=/wq/fundann/"
                     "DISFundStdPrice.xml&divisionId=MDIS01004001000000&serviceId=SDIS01004001000")
    df = pd.read_xml(StringIO(res), xpath=".//selectMeta")
    if df.empty:
        return df
    df = df.rename(columns={"tmpV14": "base_dt", "tmpV12": "fund_code", "tmpV2": "name_kr",
                            "tmpV5": "aum", "tmpV6": "nav", "tmpV7": "tax_base_nav"})
    keep = ["base_dt", "fund_code", "name_kr", "nav", "tax_base_nav", "aum"]
    return df[[c for c in keep if c in df.columns]]


def fetch_settlement(base_dt: datetime.date, days: int = 14) -> pd.DataFrame:
    """결산·상환·분배. 기간 조회라 하루 단위로 부를 필요가 없다."""
    start_dt = base_dt - datetime.timedelta(days=days)
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
        <message>
          <proframeHeader>
            <pfmAppName>FS-DIS2</pfmAppName>
            <pfmSvcName>DISFundRdmpSO</pfmSvcName>
            <pfmFnName>select</pfmFnName>
          </proframeHeader>
          <systemHeader></systemHeader>
            <DISCondFuncDTO>
            <tmpV30>{start_dt:%Y%m%d}</tmpV30>
            <tmpV31>{base_dt:%Y%m%d}</tmpV31>
            <tmpV12></tmpV12><tmpV3></tmpV3><tmpV5></tmpV5><tmpV4></tmpV4><tmpV7></tmpV7>
        </DISCondFuncDTO>
        </message>"""
    res = _post(xml, "https://dis.kofia.or.kr/websquare/index.jsp?w2xPath=/wq/fundann/"
                     "DISFundRdmp.xml&divisionId=MDIS01004004000000&serviceId=SDIS01004004000")
    df = pd.read_xml(StringIO(res), xpath=".//selectMeta")
    if df.empty:
        return df
    return df.rename(columns={
        "tmpV11": "fund_code", "tmpV3": "period_start_value", "tmpV4": "period_end_value",
        "tmpV5": "elapsed_days", "tmpV6": "inception_principal", "tmpV7": "nav",
        "tmpV8": "tax_base_nav", "tmpV9": "post_settlement_nav", "tmpV10": "settlement_type",
    })[["fund_code", "period_start_value", "period_end_value", "elapsed_days",
        "inception_principal", "nav", "tax_base_nav", "post_settlement_nav", "settlement_type"]]


def fetch_newly(base_dt: datetime.date, days: int = 14) -> pd.DataFrame:
    """신규 설정 펀드. 아직 자산운용보고서가 없어 공시목록에 안 잡히는 구간을 메운다."""
    start_dt = base_dt - datetime.timedelta(days=days)
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
        <message>
          <proframeHeader>
            <pfmAppName>FS-DIS2</pfmAppName>
            <pfmSvcName>DISNewEstSO</pfmSvcName>
            <pfmFnName>select</pfmFnName>
          </proframeHeader>
          <systemHeader></systemHeader>
            <DISCondFuncDTO>
            <tmpV30>{start_dt:%Y%m%d}</tmpV30>
            <tmpV31>{base_dt:%Y%m%d}</tmpV31>
            <tmpV12></tmpV12><tmpV3></tmpV3><tmpV5></tmpV5><tmpV4></tmpV4><tmpV7>1</tmpV7>
        </DISCondFuncDTO>
        </message>"""
    res = _post(xml, "https://dis.kofia.or.kr/websquare/index.jsp?w2xPath=/wq/fundann/"
                     "DISNewEst.xml&divisionId=MDIS01006001000000&serviceId=SDIS01006001000")
    df = pd.read_xml(StringIO(res), xpath=".//selectMeta")
    if df.empty:
        return df
    return df.rename(columns={
        "tmpV10": "fund_code", "tmpV1": "manage_company", "tmpV2": "fund_name",
        "tmpV3": "incept_dt", "tmpV4": "category", "tmpV5": "region",
        "tmpV8": "custodian", "tmpV9": "lead_dist",
    })[["fund_code", "manage_company", "fund_name", "incept_dt", "category",
        "region", "custodian", "lead_dist"]]
