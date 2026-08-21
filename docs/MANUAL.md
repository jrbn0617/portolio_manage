# 사용 매뉴얼

[← README](../README.md) · 설계 배경은 [docs/plans/](./plans/00-overview.md) 참고

## 이 프로그램은 무엇인가
국내 주식/ETF/펀드/지수의 가격·배당 데이터를 모아두고, 그 위에서 돌린 알고리즘의 성과를
보는 개인용 도구다. 최상위가 둘이다 — **데이터 관리(`/data`)** 와 **알고리즘(`/algo`)**.

옛 주소(`/stocks`, `/upload`, `/batches` …)로 들어오면 하위 경로까지 그대로 `/data` 아래로
넘겨준다. 북마크는 고치지 않아도 된다.

---

## 데이터 관리 (`/data`)

### 대시보드 (`/data`)
지연된 데이터·수집 예정·최근 배치 실패 건수를 맨 위에 놓고, 그 아래 데이터별 최신 수신일과
최근 배치 실행 이력을 보여준다. **어디가 비었는지부터 보이게** 하는 게 목적이다.

### 자산군 (`/data/stocks`, `/data/etf`, `/data/funds`, `/data/indices`)
주식·ETF는 그 안에서 다시 나뉜다.

| 경로 | 화면 |
|---|---|
| `/data/stocks` | 종목 마스터 — 티커/이름 검색, 섹터·산업 필터, 직접 추가·삭제 |
| `/data/stocks/data` | 데이터 조회 — OHLCV 차트(로그스케일), 배당조정 수정주가, 배당 내역, CSV 내려받기 |
| `/data/stocks/index-members` | 지수 구성종목 |
| `/data/stocks/monthly-fundamentals` | 월간 펀더멘털 |
| `/data/etf`, `/data/etf/data` | ETF — 종목 마스터 · 데이터 조회 |
| `/data/funds` | 펀드 — 운용펀드·클래스·기준가 (테이블 구조가 달라 한 화면) |
| `/data/indices` | 지수 — 블룸버그 수신 티커 관리 |

### 데이터 업로드 (`/data/upload`)
CSV/Excel을 직접 올린다. 데이터 종류(가격/배당/거시지표/지수편입/실제종가)를 고르고 파일을
선택하면 성공/실패 건수와 실패 사유를 바로 보여준다. 컬럼명이 정확히 안 맞아도 흔한
별칭(예: `종목코드`→`ticker`)은 자동 인식한다.

### 배치 관리 (`/data/batches`)
자동 갱신 작업의 스케줄을 보여주고 **"지금 실행"** 버튼으로 즉시 돌릴 수 있다. 실행 이력
표에서 상태(성공/실패/실행 중)·소요 시간·요약을 보고, 행을 클릭하면 상세 로그가 펼쳐진다.
cron으로 돈 것과 여기서 수동으로 돌린 것이 구분 없이 같은 표에 쌓인다.

---

## 알고리즘 (`/algo`)

알고리즘별 **개발 단계**와 **성과 보고**를 한자리에서 본다.

| | 상태 | 보고 자료 |
|---|---|---|
| 알고리즘 #1 | 채택 | 월중 성과 · 월별 성과 이력 · 워크포워드 검증 · 사내 제안서 |
| 알고리즘 #2 | 폐기 권고 | 없음 (개발 문서만) |
| 배분 #1 | 이식 완료 | 개요·성과 |

**상태 문구의 원천은 화면이 아니라 문서다** (`docs/algorithms/`, `docs/allocation/`).
상태가 바뀌면 문서를 먼저 고치고 `AlgoDashboardPage.tsx` 의 `ALGOS` 를 맞춘다.

버튼은 분석 스크립트가 만들어 둔 HTML 파일을 여는 것이라, **파일이 만들어진 시각을 같이
적어 둔다** — 오래된 리포트를 최신인 줄 알고 보는 일이 없게 하려는 것이다. 아직 안 만든
리포트는 흐리게 표시되고 생성 명령이 함께 뜬다.

리포트 등록·생성 방법은 [docs/analysis-reports.md](./analysis-reports.md) 참고.

---

## 자동 배치 작업

매일 정해진 시각에 자동으로 실행되도록 cron에 등록돼 있다 (평일만).

| 작업 | 시각(KST) | 하는 일 |
|---|---|---|
| `daily_update` | 16:00 | KRX에서 **당일 및 직전 영업일**(정정 반영 재확인용) 가격/거래량/실제종가 수집, 상장주식수 변동(액면분할·감자 등) 감지 후 해당 종목만 재수집, 휴장일 캘린더 갱신, KOSPI/KOSDAQ 지수 편입 갱신, 월봉·배당조정지수 재계산 |
| `dividends_seibro` | 16:30 | SEIBRO(예탁결제원)에서 최근 1주일 배당 내역(배정기준일 포함) 조회·적재, 영향받은 종목 배당조정지수 재계산 |

두 작업 모두 `backend/scripts/` 아래 독립 스크립트로 존재하고(`daily_update.py`, `load_dividends_seibro.py`), cron으로 실행되든 배치 관리 화면에서 수동 실행되든 **완전히 동일한 코드 경로**를 타서 결과가 똑같이 `batch_runs` 테이블에 기록된다.

**배당(dividends)만 자동화가 부분적**이다 — SEIBRO 스크래핑은 최근 1주일치만 매일 이어붙이는 용도이고, 과거 히스토리는 별도로 준비한 파일을 업로드 화면에서 올려둔 상태다.

로그는 두 군데서 볼 수 있다: 배치 관리 화면(DB에 저장된 stdout 캡처), 또는 서버의 `backend/logs/cron.log` 파일(cron이 실행될 때마다 이어붙임).

---

## cron 등록 방법 (새 컴퓨터로 옮기거나 재설정할 때)

이 프로젝트를 새 환경에 설치했거나 cron 등록이 풀렸다면, 터미널에서 아래 두 줄을 실행한다.

```bash
crontab -l 2>/dev/null > /tmp/mckim_cron.txt
cat <<'EOF' >> /tmp/mckim_cron.txt
0 16 * * 1-5 cd /Users/mckim/Documents/GitHub/portolio_manage/backend && venv/bin/python3 scripts/daily_update.py cron >> logs/cron.log 2>&1
30 16 * * 1-5 cd /Users/mckim/Documents/GitHub/portolio_manage/backend && venv/bin/python3 scripts/load_dividends_seibro.py cron >> logs/cron.log 2>&1
EOF
crontab /tmp/mckim_cron.txt
crontab -l   # 등록 확인
```

- **macOS는 최초 1회 "전체 디스크 접근 권한" 팝업이 뜰 수 있다** — 터미널 앱에 권한을 허용해야 등록이 끝난다. AI 에이전트(Claude Code 등)는 이 팝업을 대신 눌러줄 수 없으므로, 이 등록 과정은 사람이 터미널에서 직접 실행해야 한다.
- 등록 확인은 `crontab -l`로. 두 줄이 보이면 성공.
- 이미 등록돼 있다면 `crontab -l`로 먼저 확인하고 중복 등록하지 않는다.

### 새 배치 작업을 추가하려면
1. `backend/scripts/`에 스크립트 작성 — `daily_update.py`/`load_dividends_seibro.py`의 `run(trigger)` 패턴(BatchRun 기록 + stdout 캡처)을 그대로 따라 하면 배치 관리 화면에 자동으로 뜬다.
2. `backend/app/api/routes/batches.py`의 `JOBS` 딕셔너리에 스크립트 경로/설명/cron 표현식을 등록.
3. 위 cron 등록 절차에 새 줄을 추가.

---

## 환경 설정 참고

- `backend/.env`의 `KRX_ID`/`KRX_PW` — pykrx가 KRX 데이터를 조회할 때 필요 (2025-12-27부터 로그인 필수). `data.krx.co.kr`에서 가입한 계정.
- `reference/` 폴더 — 사용자가 준비한 대용량 원본 파일(WISEfn 엑셀, 배당 파일 등) 보관용, git에는 커밋되지 않음(`.gitignore`).
- 배당 실제종가(`raw_closes`)는 배당수익률 계산의 기준값으로만 쓰이며 KRX 자동 갱신 대상이 아니다(분할 시에도 미조정 값을 보존해야 하므로).
