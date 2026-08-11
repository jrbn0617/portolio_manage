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

## 과거 데이터 백필은 DataGuide로만

**과거 시계열 백필을 pykrx/KRX로 수집하지 않는다.** DataGuide/WISEfn 요청 양식
(`reference/*_request.xlsx` → 응답 `*_response.xlsx`)으로 받아서 적재한다.

필요한 Item Code를 정리해서 알려주면 사용자가 직접 받아온다. 요청 양식 생성은
`backend/scripts/generate_backfill_data_request_template.py`,
`backend/scripts/generate_monthly_data_request_template.py` 참고.

pykrx는 **매일/매월 신규 데이터 자동 수집**(`daily_update.py`, `refresh_short_selling.py`,
`load_shares_outstanding_pykrx.py` 등 cron 배치)에만 쓴다.

## 여러 세션이 동시에 작업할 때

- **DB는 세션 간 공유된다.** 대량 적재·변환(백필, 정규화)은 한 세션에서만 실행한다.
  cron 배치(평일 16:00 `daily_update`, 16:30 `load_dividends_seibro`, 17:00
  `refresh_short_selling`, 매월 1일 06:00 `load_shares_outstanding_pykrx`)와 겹치지 않게
  주의한다. 실행 이력은 `batch_runs` 테이블에서 확인.
- **브랜치를 나눈다.** `experiment/NN-slug`, `data/slug` 등으로 분리하고 main 직접 커밋은 피한다.
- **scratchpad는 세션 간 공유되지 않는다.** 재사용할 분석 스크립트는 리포지토리로 옮긴다.
- `reference/` 는 gitignore 대상이라 산출 CSV·엑셀은 다른 PC로 넘어가지 않는다.
