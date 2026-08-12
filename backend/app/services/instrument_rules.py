"""종목 분류 규칙 — 적재/요청 전 단계에서 공통으로 쓴다.

우선주 판별은 **종목코드 끝자리로 한다**. 이름 정규식(`...우`, `...우B` 등)은 오탐이
많다 — 실측으로 `연우`, `포스코대우`, `미래에셋대우`, `이오플로우`, `성우`, `비나우`
6종목이 보통주인데도 우선주로 걸렸다. 반대로 코드 규칙에서 이름이 '우'로 끝나지 않는
경우는 `두산퓨얼셀2우선주(신형)`(33626L)처럼 신형 우선주 코드(끝자리 K/L/M)뿐이라
규칙이 더 정확하다.

한국 종목코드는 보통주가 끝자리 0, 우선주는 5·7·9 또는 신형 코드의 K/L/M 등으로 끝난다.

**이 규칙은 주식(`asset_type='stock'`)에만 적용한다.** ETF·리츠·지수는 코드 체계가 달라
같은 판정을 하면 안 된다. 호출하는 쪽에서 asset_type으로 먼저 걸러야 한다
(`purge_preferred_stocks.py`는 `asset_type='stock'`으로 대상을 좁힌다).
"""


def is_preferred(ticker: str | None) -> bool:
    """우선주면 True. 종목코드 끝자리가 '0'이 아니면 우선주로 본다."""
    if not ticker:
        return False
    return ticker.strip()[-1] != "0"


def is_common_stock(ticker: str | None) -> bool:
    return not is_preferred(ticker)


def filter_common(tickers):
    """티커 목록에서 우선주를 걸러낸다 (순서 유지)."""
    return [t for t in tickers if is_common_stock(t)]
