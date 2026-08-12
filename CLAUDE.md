# 프로젝트 작업 규칙

## 알고리즘 개발 시 — 먼저 `docs/algorithms/` 를 읽을 것

새 알고리즘을 설계하거나 기존 알고리즘을 손보기 전에 **`docs/algorithms/` 안의 문서를 먼저
읽는다.** 여기에 확정된 스펙, 실패한 시도, 그리고 "다시 볼 때 유의할 것"이 정리돼 있어서
같은 길을 두 번 파는 걸 막아준다.

| 파일 | 내용 |
|---|---|
| `algorithm1-overview.md` | 알고리즘 #1 확정 스펙 (EBITDA PEG + 모멘텀). **확정, 개발 종료** |
| `algorithm1-experiments.md` | 알고리즘 #1 실험 로그 13건 |
| `algorithm1-plain-overview.md` | 알고리즘 #1 비개발자용 설명 |
| `algorithm2-overview.md` | 알고리즘 #2 스펙 (기관 수급 + 모멘텀). **검증 완료, 채택 보류** |
| `algorithm2-experiments.md` | 알고리즘 #2 실험 로그. 폐기된 섹터로테이션 경로 포함 |
| `algorithm3-prereg.md` | 알고리즘 #3 (저변동·복권형 회피) **선등록. 백테스트 미실행** |

**알고리즘 #3은 선등록 문서다.** 2014~2018 백필이 도착하기 전에 가설·파라미터·판정기준을
확정해 얼려뒀다. 그 구간을 진짜 표본외로 쓰기 위한 것이므로 **결과를 보고 파라미터를 바꾸지
않는다.** 데이터 도착 후 그 문서의 9절 순서대로 실행한다.

특히 각 `*-experiments.md` 마지막의 **"다시 볼 때 유의할 것"** 섹션을 확인한다 — 주간
형성일 IC의 t 과대평가, 전체기간 평균의 함정, 벤치마크 선택 문제 등 이미 겪은 실수가 적혀 있다.

새 알고리즘 문서를 만들 때는 `algorithm1-overview.md`의 1~6 섹션 구조(개요 → 전략 로직 →
파라미터 → 백테스트 성과 → 실험 히스토리 → 한계)를 따르고, 상세 실험 로그는
`algorithmN-experiments.md`로 분리한다.

## 섹터 분류 필드 용어

`Instrument`의 세 분류 필드는 대화·문서에서 아래 이름으로 부른다 (코드/DB 컬럼명은 그대로).

| DB 컬럼 | 분류체계 | 부르는 이름 |
|---|---|---|
| `sector` | FactSet | **팩셋섹터** (factset_sector) |
| `industry` | FactSet | **팩셋산업** (factset_industry) |
| `krx_sector` | KRX 업종 | **케이산업** |

`sector`/`industry`가 둘 다 FactSet 체계라 "섹터"라고만 하면 어느 필드인지 모호해서 구분한다.

## 우선주는 종목코드 끝자리로 판별한다

**종목코드 맨 끝자리가 `0`이 아니면 우선주다.** 이름 정규식(`...우`, `...우B`)은 오탐이
많다 — `연우`, `포스코대우`, `미래에셋대우`, `이오플로우`, `성우`, `비나우`가 보통주인데도
걸렸다. 반대로 코드 규칙만 잡는 건 `두산퓨얼셀2우선주(신형)`(33626L)처럼 끝자리가 K/L/M인
신형 우선주다. 공통 구현은 `backend/app/services/instrument_rules.py`.

우선주는 **DataGuide 요청에 넣지 않고, 배치 적재도 하지 않으며, DB에 있으면 지운다**
(`scripts/purge_preferred_stocks.py` — 2026-08-12에 1,601종목 + 자식 20,061행 삭제).
KRX에서 종목 목록을 받는 배치는 `filter_common()`을 거친다.

## KRX(pykrx) 호출은 간격을 3초 이상 둘 것

**간격이 짧으면 실패하거나 빈 결과를 정상처럼 돌려준다.** 실측 사례 둘:
- 코스닥150 편입종목 2015-06-30이 150종목으로 나왔으나 재시도하니 0 — 실제 출범은 2015-07-13
- `get_market_sector_classifications`가 2015~2018 전 구간 0종목/ValueError → 3초로 늘리자 정상

**빈 결과를 "데이터 없음"으로 단정하지 말고 반드시 재시도로 확인한다.** 잘못 믿으면
"KRX가 과거 데이터를 안 준다"는 틀린 결론으로 이어진다(실제로 2015년까지 다 있다).

## 과거 데이터 백필은 DataGuide로만

**과거 시계열 백필을 pykrx/KRX로 수집하지 않는다.** DataGuide/WISEfn 요청 양식
(`reference/*_request.xlsx` → 응답 `*_response.xlsx`)으로 받아서 적재한다.

필요한 Item Code를 정리해서 알려주면 사용자가 직접 받아온다. 요청 양식 생성은
`backend/scripts/generate_backfill_data_request_template.py`,
`backend/scripts/generate_monthly_data_request_template.py` 참고.

pykrx는 **매일/매월 신규 데이터 자동 수집**(`daily_update.py`, `refresh_short_selling.py`,
`load_shares_outstanding_pykrx.py` 등 cron 배치)에만 쓴다.

**예외 — 시계열이 아닌 "시점별 명단·분류"는 KRX에서 받는다.** DataGuide 요청 양식으로
받을 수 있는지 불확실하고 호출량도 작다:
- 지수 편입종목·시장구분 (`backfill_index_memberships_2015.py`) — 2015-06/07까지 확보
- KRX 업종분류 (`backfill_krx_sector.py`) — 2015년까지 조회 가능

배당은 SEIBRO 수동 내보내기로 받는다(`load_dividends_seibro_export.py`). 확장자가 `.xls`
지만 실제로는 euc-kr HTML 테이블이고, **한 번에 10,000행에서 잘리므로**(배정기준일 내림차순
정렬 → 과거쪽이 잘림) 기간을 쪼개 여러 번 받아야 한다.

## 여러 세션이 동시에 작업할 때

- **DB는 세션 간 공유된다.** 대량 적재·변환(백필, 정규화)은 한 세션에서만 실행한다.
  cron 배치(평일 16:00 `daily_update`, 16:30 `load_dividends_seibro`, 17:00
  `refresh_short_selling`, 매월 1일 06:00 `load_shares_outstanding_pykrx`)와 겹치지 않게
  주의한다. 실행 이력은 `batch_runs` 테이블에서 확인.
- **브랜치를 나눈다.** `experiment/NN-slug`, `data/slug` 등으로 분리하고 main 직접 커밋은 피한다.
- **scratchpad는 세션 간 공유되지 않는다.** 재사용할 분석 스크립트는 리포지토리로 옮긴다.
- `reference/` 는 gitignore 대상이라 산출 CSV·엑셀은 다른 PC로 넘어가지 않는다.
