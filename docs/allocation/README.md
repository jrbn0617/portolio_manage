# 자산배분 트랙

**주식 종목선정 트랙(`docs/algorithms/`)과 분리해서 관리한다.** 다루는 문제가 다르다 —
"어떤 종목을 고르나"가 아니라 "어느 자산에 얼마나"다. 유니버스도, 데이터도, 실패 방식도
겹치지 않는다.

| | 주식 트랙 | 자산배분 트랙 |
|---|---|---|
| 문서 | `docs/algorithms/` | **`docs/allocation/`** |
| 분석 스크립트 | `backend/analysis/algorithm1/`, `algorithm2/` | **`backend/analysis/allocation/`** |
| 이름 | 알고리즘 #1, #2 | **배분 #1, #2 …** (`allocation1-*`) |
| 유니버스 | 코스피/코스닥 개별종목 | 지수·환율 (`bbg_indices`, 화면: 지수관리) |
| 데이터 원천 | DataGuide 백필 + pykrx 일배치 | 블룸버그 (`benchmark_indices_bbg` 배치) |
| 가용 구간 | 2014~ (KRX 시세는 2015~) | **1999~** (아래 인벤토리) |

## 문서

| 파일 | 내용 |
|---|---|
| `allocation1-overview.md` | **배분 #1 — 경기 사이클 스위치.** 스펙·이식 검증 |
| `allocation1-experiments.md` | 배분 #1 실험 로그 (아직 없음) |

새 배분 알고리즘 문서는 `docs/algorithms/algorithm1-overview.md` 의 1~6 섹션 구조
(개요 → 전략 로직 → 파라미터 → 백테스트 성과 → 실험 히스토리 → 한계)를 따르고, 상세
실험 로그는 `allocationN-experiments.md` 로 분리한다.

## 데이터 인벤토리 (2026-08-20 기준)

`bbg_indices` 에 등록된 15종. 전부 `instruments(asset_type='index')` + `prices` 에 들어 있고
`prices.close` 가 우리가 쓸 값이다(코스피 계열만 배당포인트로 총수익을 직접 계산하고,
나머지는 이미 TR/NTR/현물이라 PX_LAST 그대로).

| 티커 | 자산 | 시작 |
|---|---|---|
| `XAU` | 금 현물 USD/oz | 1920-01-30 |
| `NDDUEAFE` | MSCI EAFE NTR (선진국 ex-US) | 1969-12-31 |
| `USDKRW` | 원달러 환율 | 1981-04-13 |
| `SPTR500N` | S&P500 NTR | 1989-12-29 |
| `LEGATRUU` | Bloomberg Global-Aggregate TR | 1990-01-01 |
| `LD20TRUU` | US T-Bill | 1991-12-31 |
| `LT09TRUU` | UST 7-10Y TR | 1992-01-31 |
| `LT11TRUU` | UST 20Y+ TR | 1992-02-28 |
| `NDUEEGF` | MSCI EM NTR | 1998-12-31 |
| `NDUEACWF` | MSCI ACWI NTR | 1998-12-31 |
| `KOSPI2T` | KOSPI200 TR | 2011-01-03 |
| `KOSPI` `KOSPI200` `KOSDAQ` `KOSDAQ150` | 국내 지수 TR | 2014-01-02 |

**가장 짧은 계열이 전체를 묶는다.** 조합에 따라 시작이 달라진다:

```
전부 (국내 포함)          2014-01-02   12년
국내를 KOSPI2T 로         2011-01-03   15년
해외만 (ACWI/EM 포함)     1999-01-01   27년
해외만 (ACWI/EM 제외)     1992-03-01   34년
```

**빠진 것** — 국내 채권 지수, 리츠, 원자재 광의(금 외), 인플레이션 연동채. 필요하면
지수관리 화면에서 티커를 추가하면 되고, 블룸버그 요청은 티커를 늘려도 1회다.
