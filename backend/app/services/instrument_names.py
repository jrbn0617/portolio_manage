"""종목명 최신화 — 이미 수집 중인 데이터에 딸려 오는 이름을 반영한다.

**KRX에 이름을 따로 물어보지 않는다.** `get_market_ticker_name`은 종목당 1회 호출이라
2,700종목이면 호출 제한에 걸린다(CLAUDE.md "KRX 호출 간격"). 대신 매일 받는 응답에
이미 들어 있는 이름을 쓴다 — SEIBRO 배당의 `종목명`, ETF 분배금의 `KOR_SECN_NM` 등.

왜 필요한가 — 기존 배치는 **신규 등록할 때만** 이름을 넣고 그 뒤로 갱신하지 않았다.
그래서 사명이 바뀌어도 DB에는 옛 이름이 남았다(삼성엔지니어링→삼성E&A,
HSD엔진→한화엔진, 에이치디건설기계→HD현대건설기계). 등록 시점에 이름 조회가
실패한 종목은 `name == ticker`인 채로 남아 있기도 하다.
"""
from sqlalchemy.orm import Session

from app.models.instrument import Instrument


def _clean(name: object) -> str:
    return " ".join(str(name).split()) if name is not None else ""


def refresh_instrument_names(db: Session, names: dict[str, str], source: str,
                             log=print) -> dict:
    """티커→이름 매핑으로 instruments.name을 최신화한다. commit은 호출자가 한다.

    반영하지 않는 경우 —
      · 빈 값 / 'nan' : 파싱 실패
      · 이름 == 티커  : 조회 실패 시 쓰이는 placeholder라, 이걸로 덮으면 이름을 잃는다
    이름이 바뀌면 사명 변경이거나 placeholder 보정이라 한 건씩 남긴다."""
    wanted = {}
    for ticker, raw in names.items():
        name = _clean(raw)
        if not name or name.lower() == "nan" or name == str(ticker).strip():
            continue
        wanted[str(ticker).strip()] = name
    if not wanted:
        return {"checked": 0, "renamed": 0, "changes": []}

    rows = (db.query(Instrument.id, Instrument.ticker, Instrument.name)
            .filter(Instrument.ticker.in_(list(wanted))).all())

    changes = []
    for iid, ticker, current in rows:
        new = wanted[ticker]
        if _clean(current) == new:
            continue
        db.query(Instrument).filter(Instrument.id == iid).update({"name": new})
        changes.append(f"{ticker} {current!r} -> {new!r}")

    if changes:
        log(f"  종목명 최신화 {len(changes)}건 ({source})")
        for c in changes[:20]:
            log(f"    {c}")
        if len(changes) > 20:
            log(f"    ... 외 {len(changes) - 20}건")
    return {"checked": len(rows), "renamed": len(changes), "changes": changes}
