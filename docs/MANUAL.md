# 사용 매뉴얼

[← README](../README.md) · 설계 배경은 [docs/plans/](./plans/00-overview.md) 참고

## 이 프로그램은 무엇인가
국내 주식/ETF/펀드/지수의 가격·배당 데이터를 모아두고 조회하는 개인용 도구다 (전체 로드맵 3단계 중 1단계: 데이터 관리). 화면은 4개: **종목 마스터**, **데이터 업로드**, **데이터 조회**, **배치 관리**.

---

## 화면별 기능

### 종목 마스터 (`/`)
등록된 전 종목 목록. 티커/이름 검색, 섹터·산업 드롭다운 필터. 화면에서 직접 종목을 추가/삭제할 수 있지만, 실무적으로는 대부분 업로드나 배치로 자동 등록된다.

### 데이터 업로드 (`/upload`)
CSV/Excel 파일을 직접 올릴 때 쓰는 화면. 데이터 종류(가격/배당/거시지표/지수편입/실제종가)를 고르고 파일을 선택하면, 업로드 결과(성공/실패 건수, 실패 사유)를 바로 보여준다. 컬럼명이 정확히 안 맞아도 흔한 별칭(예: `종목코드`→`ticker`)은 자동 인식한다.

### 데이터 조회 (`/data`)
티커를 입력하면 OHLCV 차트(로그스케일), 배당조정 수정주가, 배당 내역을 볼 수 있고 CSV로 다운로드할 수 있다.

### 배치 관리 (`/batches`)
자동 갱신 작업의 스케줄을 보여주고, **"지금 실행"** 버튼으로 즉시 실행할 수도 있다. 아래 실행 이력 표에서 각 실행의 상태(성공/실패/실행 중), 소요 시간, 요약을 볼 수 있고, 행을 클릭하면 상세 로그가 펼쳐진다. cron으로 자동 실행된 것과 여기서 수동으로 실행한 것이 구분 없이 같은 표에 쌓인다.

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
