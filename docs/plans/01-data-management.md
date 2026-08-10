# 1단계 — 데이터 관리

[← 전체 개요](./00-overview.md)

## 목표
국내 주식 / 국내 ETF / 국내 펀드 / 기초지수(BM)에 대한 가격(OHLCV, 일봉·월봉), 배당 내역, 환율·금리 등 거시경제 지표를 **CSV/Excel 파일 업로드**로 적재하고 조회할 수 있는 시스템을 구축한다.

재무제표는 이번 단계 범위에서 제외한다. 사용자가 실제로 필요로 하는 항목이 생겼을 때, 그 항목만 유연하게 적재할 수 있는 구조로 남겨둔다 (표준 재무제표 스키마를 미리 다 만들지 않는다).

수집 방식은 MVP에서는 **수동 파일 업로드만** 지원한다. pykrx/FinanceDataReader 같은 자동 수집이나 스케줄러는 지금 만들지 않되, 나중에 붙이기 쉽도록 서비스 레이어를 분리해둔다.

## 데이터 범위
| 구분 | 내용 |
|---|---|
| 대상 자산 | 국내 주식, 국내 ETF, 국내 펀드, 기초지수(BM) |
| 가격 데이터 | OHLCV, 일봉/월봉 |
| 배당 데이터 | 배당 내역 |
| 거시지표 | 환율, 금리 등 |
| 재무제표 | 제외 (추후 필요 항목만 유연하게 추가) |

## DB 스키마 초안 (PostgreSQL)

- **`instruments`** — 종목/ETF/펀드/지수 마스터
  - `id`, `ticker`, `name`, `asset_type` (`stock`/`etf`/`fund`/`index`), `market`, `created_at`
- **`prices`** — OHLCV
  - `id`, `instrument_id` (FK), `date`, `period` (`D`/`M`), `open`, `high`, `low`, `close`, `volume`
  - `(instrument_id, date, period)` unique 제약으로 upsert 지원
- **`dividends`** — 배당 내역
  - `id`, `instrument_id` (FK), `ex_date`, `pay_date`, `amount`
- **`macro_indicators`** — 환율/금리 등 거시지표 (범용 시계열)
  - `id`, `indicator_name` (예: `USDKRW`, `KOR_BASE_RATE`), `date`, `value`
  - 지표명을 자유롭게 추가할 수 있는 구조라 종류가 늘어나도 스키마 변경 불필요
- **`upload_batches`** — 업로드 이력
  - `id`, `data_type`, `file_name`, `uploaded_at`, `row_count`, `error_count`, `status`

> 재무제표는 스키마에 포함하지 않는다. 필요 시점에 `macro_indicators`와 유사한 유연한(EAV 또는 JSONB) 구조로 별도 추가한다.

## 백엔드 구조 (`backend/`)
```
backend/
├── app/
│   ├── main.py
│   ├── db/            # 세션, 엔진, Alembic 연동
│   ├── models/         # SQLAlchemy 모델
│   ├── schemas/         # Pydantic 스키마
│   ├── api/routes/       # instruments, uploads, prices, dividends, macro
│   └── services/         # 업로드 파싱/검증/적재 로직 (소스 무관하게 재사용 가능하도록)
└── alembic/            # 마이그레이션
```

- Alembic으로 스키마 마이그레이션 관리
- 종목 마스터 CRUD API
- 업로드 API
  - 데이터 타입별(가격/배당/거시지표) 업로드 엔드포인트
  - pandas로 CSV/Excel 파싱 → 필수 컬럼/날짜 포맷 검증 → 미등록 ticker 처리 → upsert
  - 업로드 결과(성공/실패 건수)를 `upload_batches`에 기록하고 응답으로 반환
- 조회 API: ticker, 기간 등으로 필터링해 가격/배당/거시지표 조회

## 프론트엔드 구조 (`frontend/`)
- 종목 마스터 관리 화면 (목록 조회, 추가/수정)
- 데이터 업로드 화면: 데이터 타입 선택 → 파일 선택 → 미리보기 → 업로드 → 결과(성공/실패 건수) 확인
- 데이터 조회 화면: 종목별 가격 추이 기본 차트, 배당/거시지표 조회

## 향후 확장 포인트 (지금 구현하지 않음)
- pykrx/FinanceDataReader 기반 자동 수집 버튼
- 스케줄러(cron 등)를 통한 주기적 자동 갱신
- 재무제표: 필요 시점에 유연한 스키마로 추가

## 검증 방법
1. `backend/` FastAPI 서버 기동 후 Swagger UI(`/docs`)에서 API 동작 확인
2. 샘플 가격/배당/거시지표 CSV 업로드 → DB에 정상 적재되는지 확인 (중복 업로드 시 upsert 확인)
3. `frontend/`에서 업로드 화면 → 조회 화면까지 end-to-end 동작 확인

## 알려진 이슈 / 개선 백로그 (미착수)

### `dividend_adjusted_prices`의 D/M 계열 불일치 — M을 D에서 파생시키도록 변경 필요
- **현상**: `period='M'`과 `period='D'`가 같은 날짜에 대해 미세하게(소수점 이하 수준) 값이 다름.
  예: 삼성전자(005930) 2026-06-30 — M: 1035.8550, D: 1035.8701.
- **원인**: `backend/app/services/derived_prices.py`의 `_recompute_dividend_adjusted_for_period`가
  D/M을 독립적으로 각자 복리계산함. M은 월봉(그 달 마지막 거래일 종가)만 갖고 배당도 월 단위로
  뭉쳐서(`(year, month)` 단위) 반영하는 반면, D는 일 단위로 정확한 배당락일에 반영. 매 단계
  소수점 4자리 반올림(`round(idx_value, 4)`)이 누적되며 두 경로가 벌어짐.
- **참고**: 원종가(`prices`)의 M봉은 이미 `recompute_monthly_bar`에서 D의 그 달 마지막 거래일
  종가를 그대로 가져다 쓰는 방식(다운샘플링)이라 이 문제가 없음 — 배당조정가(`dividend_adjusted_prices`)의
  M봉만 이 패턴을 안 따르고 독립 재계산하는 게 불일치 원인.
- **제안**: M을 독립 복리계산하지 않고, D의 그 달 마지막 거래일 `adj_close` 값을 그대로 읽어와
  M에 upsert하는 방식으로 변경(`recompute_monthly_bar`와 동일 패턴). `_recompute_dividend_adjusted_for_period`의
  월단위 배당 lookup 분기(`d if period=="D" else (d.year, d.month)`)는 M 쪽에서 불필요해져 제거 가능.
  호출부(`recompute_dividend_adjusted`)가 이미 D→M 순서로 호출하므로 구조 변경 없이 반영 가능.
- **영향**: 전종목·전기간 M 배당조정가 재계산(force_full) 필요. `compute_momentum` 등 M을 쓰는
  모든 계산(백테스트 엔진 전체)이 미세하게(소수점 이하 수준) 재현값이 바뀜 — 기존 실험 결론에는
  영향 없음, 정밀도만 개선.
- **발견 경위**: 2026-08-10, 삼성전자 BM차감 모멘텀 계산 중 CSV(M기준)와 즉석계산(D기준) 값이
  미세하게 달라 발견. 알고리즘#2 마무리 후 진행 예정.
