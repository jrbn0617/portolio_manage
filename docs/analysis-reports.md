# 보고서 산출 스크립트 목록

화면으로 볼 수 있는 산출물(HTML)을 만드는 스크립트를 모아 둔다. **어제 뭐로 만들었더라**를
찾는 데 걸리는 시간을 줄이는 게 목적이다.

전부 **읽기 전용**이다 — DB에 쓰지 않고 외부 API도 부르지 않는다.

## 공통 사항

```bash
cd backend
venv/bin/python analysis/algorithm1/<스크립트>.py
```

산출물은 `reference/analysis/` 에 떨어진다. `ALGO_OUT` 환경변수로 바꿀 수 있다.

```bash
ALGO_OUT=/tmp/out venv/bin/python analysis/algorithm1/mtd_viz.py
```

**세션 스크래치패드에 쓰지 않는 이유** — 스크래치패드는 세션 간 공유되지 않아서(CLAUDE.md)
다음에 열면 사라져 있다. `reference/` 는 gitignore 대상이라 다른 PC로도 넘어가지 않는다.
**산출물이 아니라 스크립트가 원본**이고, 필요하면 다시 돌린다.

---

## HTML 보고서 4종

### 1. 월중(MTD) 성과 — `algorithm1/mtd_viz.py`

직전 형성일 포트폴리오를 **전일 종가까지** 따라간 결과. **매일 돌리는 것을 전제로 한다.**

```bash
venv/bin/python analysis/algorithm1/mtd_viz.py                     # 전 영업일 기준
venv/bin/python analysis/algorithm1/mtd_viz.py --as-of 2026-08-18  # 기준일 지정
venv/bin/python analysis/algorithm1/mtd_viz.py --form 2026-07-31   # 형성일 지정
venv/bin/python analysis/algorithm1/mtd_viz.py --today             # 당일 종가까지
```

| | |
|---|---|
| 산출 | `mtd_viz.json` · `mtd_viz.html` |
| 입력 | DB 직접 조회 (엑셀·선행 스크립트 불필요) |
| 내용 | 누적 수익률 추이 · 종목별 기여도 · 보유 종목 · 손실 제한 발동 · 업종 구성 |

**손댈 게 없다.** 기준일은 전 영업일, 형성일은 그 직전 월말 거래일을 자동으로 잡으므로
달이 바뀌어도 인자를 바꾸지 않는다. 제목의 연·월도 기준일에서 뽑는다.

**왜 당일이 아니라 전일인가** — 같은 날 두 번 돌리면 같은 결과가 나와야 하기 때문이다.
당일 종가를 쓰면 언제 돌렸느냐에 따라 값이 달라진다: 장중에는 미완성 종가가 섞이고,
코스피 총수익은 평일 18:30 `benchmark_indices_bbg` 배치가 채우므로 그 전에는 아예 없다.
하루 물리면 양쪽이 모두 확정돼 있다. 당일치를 보고 싶으면 `--today`.

달력으로 어제를 계산하지 않는다 — 주말·휴장일을 직접 다뤄야 하고, 거래일이어도 적재가
실패했으면 빈 날이 된다. **실제 시세가 있는 날 중에서 고르면** 둘 다 걸리지 않는다.

지수 배치가 밀려 벤치마크가 그래도 짧으면, 두 계열이 모두 있는 날까지로만 초과성과를
계산하고 그림에서 벤치마크 선이 먼저 끝난다 — 버그가 아니다.

재현 로직은 `mtd_performance.py` 의 `build()` · `stock_paths()` 를, 기준일·형성일 판정은
`resolve_as_of()` · `resolve_formation()` 을 그대로 부른다. 콘솔 숫자만 필요하면
`mtd_performance.py` 를 같은 인자로 돌리면 된다(HTML 없음).

### 2. 배분 #1 개요·성과 — `allocation/cycle_switch_report.py`

경기 사이클 스위치 전략의 개요와 성과 분석. 스펙은 `docs/allocation/allocation1-overview.md`.

```bash
venv/bin/python analysis/allocation/cycle_switch_report.py
venv/bin/python analysis/allocation/cycle_switch_report.py --from 1999-12-31 --cost 0.002
```

| | |
|---|---|
| 산출 | `cycle_switch.json` · `cycle_switch.html` |
| 입력 | DB 직접 조회 (`prices` + `macro_indicators`) |
| 내용 | 현재 국면 · 누적/낙폭 · 스위치 28년 타임라인 · 국면별 성과 · 연도별 · 배분 추이 |

계산은 `cycle_switch.py`(신호·비중)와 `backtest.py`(엔진)를 그대로 부른다 — 리포트가
따로 계산하지 않으므로 표와 그림이 어긋나지 않는다. HTML 생성만 `cycle_switch_html.py`
로 나눠 뒀다.

### 3. 사내 제안서 — `algorithm1/build_proposal.py`

A4 인쇄를 전제로 한 알고리즘 제안서. 브라우저 인쇄로 PDF를 뽑는다.

```bash
venv/bin/python analysis/algorithm1/proposal_data.py    # 먼저 (데이터)
venv/bin/python analysis/algorithm1/build_proposal.py   # 그다음 (HTML)
```

| | |
|---|---|
| 산출 | `proposal.html` |
| 입력 | `proposal_data.json` |

**선행 스크립트가 넷이다.** `proposal_data.py` 가 아래를 읽어 하나로 합친다:

| 스크립트 | 산출 |
|---|---|
| `ops_stats.py` | `ops_stats.json` — 손절 건수, 거래비용 연환산 부담 |
| `diagnose_2019.py` | `diag2019.json` — 2019 구간 진단 |
| `momentum_regime_by_year.py` | `momentum_regime.json` — 연도별 모멘텀 국면 |

그리고 제출용 백테스트 엑셀을 직접 읽는다:
`reference/알고리즘1_코스피전체_백테스트_2015-2026.xlsx` 의 `시계열` 시트.
**경로가 `proposal_data.py` 34행에 절대경로로 박혀 있다** — 다른 PC에서는 고쳐야 한다.

로직 노출 수준은 "기법명까지만"이다. 임계치·기간·순위 규칙은 쓰지 않는다 —
`docs/algorithm-specs/00-작성규칙.md` 와 `00-변환가이드.md` 를 먼저 읽을 것.

### 4. 워크포워드 결과 — `algorithm1/build_wf_viz.py`

실험 22(워크포워드) · 23(손절 8% 검토) 결과 시각화.

| | |
|---|---|
| 산출 | `wf_viz.html` |
| 입력 | `wf_viz.json` · `stop8_viz.json` |

> **지금 이대로는 다시 못 돌린다.** 두 입력 JSON을 만드는 스크립트가 리포에 없다 —
> 세션 스크래치패드에서 만들고 옮기지 않은 채로 세션이 끝났다. 리포에 있는
> `walkforward.py` 는 `walkforward.json` 을, `stoploss_8_vs_10.py` 는 `stop8v10.json` 을
> 만들어서 **파일명도 형식도 다르다.**
>
> 지금은 `reference/analysis/` 에 남아 있는 JSON 덕분에 HTML만 다시 만들 수 있다. 수치를
> 갱신하려면 두 스크립트의 출력을 `build_wf_viz.py` 가 기대하는 형태로 맞추는 작업이
> 먼저 필요하다.

---

## HTML 없이 콘솔·JSON만

보고서로 바로 쓰지는 않고, 위 3종의 재료가 되거나 그때그때 확인용으로 돌린 것들이다.

| 스크립트 | 산출 | 용도 |
|---|---|---|
| `algorithm1/mtd_performance.py` | 콘솔 | 월중 성과 숫자만. `mtd_viz.py` 의 계산 원천 |
| `allocation/cycle_switch.py --backtest` | 콘솔 | 배분 #1 성과 요약 |
| `algorithm1/walkforward.py` | `walkforward.json` | 시간분할 재추정 (실험 22) |
| `algorithm1/stoploss_8_vs_10.py` | `stop8v10.json` | 손절 8% vs 10% (실험 23, 기각) |
| `algorithm1/ops_stats.py` | `ops_stats.json` | 손절 건수·거래비용 |
| `algorithm1/diagnose_2019.py` | `diag2019.json` | 2019 구간 진단 |
| `algorithm1/momentum_regime_by_year.py` | `momentum_regime.json` | 연도별 모멘텀 국면 |
| `algorithm1/holdout_oos.py` | 콘솔 | **표본 외 검증 — 1회성.** 홀드아웃 규칙 확인 후에만 |
| `algorithm1/cap_count_grid.py` 등 | 콘솔 | 격자 탐색. `algorithm1-experiments.md` 참조 |
| `algorithm2/*.py` | 콘솔 | 알고리즘 #2 실험. `algorithm2-experiments.md` 참조 |

---

## 새로 만들 때

1. **데이터 산출과 HTML 생성을 나눈다** — `proposal_data.py` → `build_proposal.py` 형태.
   HTML을 손볼 때마다 백테스트를 다시 돌리지 않아도 되고, 수치를 손으로 옮기다 틀리는 일도
   없어진다. 짧은 것은 `mtd_viz.py` 처럼 한 파일에 둬도 된다.
2. **산출 경로는 `ALGO_OUT` 규약을 따른다** (위 공통 사항의 3줄을 그대로 복사).
3. **JSON을 만드는 쪽도 반드시 리포에 넣는다.** 위 4번이 그래서 막혔다.
4. HTML을 아티팩트로 게시하면 링크로 공유·재열람이 된다. 스크립트를 다시 돌리면 파일이
   갱신되므로, 같은 파일 경로로 다시 게시하면 같은 링크가 유지된다.
