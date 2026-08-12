# 분석 스크립트

알고리즘 검증에 쓴 **1회성 분석 코드**를 모아둔다. `backend/scripts/`(적재·배치 등 운용
코드)와 성격이 다르다 — 여기 있는 건 논문의 부록 같은 것으로, 문서에 적힌 수치를 재현하고
"어떻게 그 결론에 도달했는지"를 남기는 게 목적이다.

## 실행 방법

```bash
cd backend
source venv/bin/activate
python3 analysis/algorithm2/interaction_test.py
```

경로는 `Path(__file__).resolve().parents[2]` 기준이라 리포지토리를 어디에 두든 동작한다.
산출 CSV는 `reference/`에 떨어지는데 이 디렉토리는 **gitignore 대상**이라 다른 PC에서는
직접 실행해서 다시 만들어야 한다.

## 결과가 실행 시점에 따라 달라진다

DB가 계속 갱신되므로(`daily_update` 평일 16:00 등) 같은 스크립트도 돌리는 날에 따라 수치가
조금씩 움직인다. 실제로 2026-08-11 16:00 배치가 돌자 형성일이 82→83개로 늘며
`interaction_test.py`의 교차항 t가 0.91→0.89로 바뀌었다. **문서의 수치는 작성 시점 스냅샷**이며,
재현 시 소수점이 다른 건 정상이다. 결론이 뒤집힐 정도의 차이가 나면 그건 조사할 일이다.

---

## algorithm2/ — 기관 수급 + 모멘텀

문서: [`docs/algorithms/algorithm2-overview.md`](../../docs/algorithms/algorithm2-overview.md),
[`algorithm2-experiments.md`](../../docs/algorithms/algorithm2-experiments.md)

| 스크립트 | 실험 | 산출 |
|---|---|---|
| `flow_signal_ic.py` | 1. 수급 신호 IC (108조합, Newey-West) | `수급신호_IC_요약.csv` |
| `flow_norm_recheck.py` | 3-1. 정규화 재검증 (시총 백필 후) | `수급_정규화_재검증.csv` |
| `short_signal_ic.py` | 2. 공매도 신호, 제도 구간별 | `공매도신호_IC_요약.csv` |
| `flow_combo_ic.py` | 4-1. 개인+기관 결합 (선형/더블소트) | `수급결합_*.csv` |
| `stock_flow_decile.py` | 4-3. 비중복 형성일 10분위 재검증 | `종목수급모멘텀_10분위.csv` |
| `top_sector_flow_stats.py` | 4-2. 월별 최상위 섹터의 수급 | `월별최상위섹터_수급통계.csv` |
| `sector_flow_momentum.py` | 4-2. 섹터 수급모멘텀 → 익월 수익률 | `섹터수급모멘텀_*.csv` |
| `flow_side_decomposition.py` | 5. 롱·숏 사이드 분해 (분위별 초과수익) | `수급_롱숏사이드_분해.csv` |
| `momentum_decile_flow.py` | 5. 모멘텀 분위별 수급 구성 + 익월수익 | `모멘텀분위_수급_익월수익률.csv` |
| `flow_momentum_backtest.py` | 6-1. 백테스트 v1 (분기보유, 주체별) | `수급모멘텀_백테스트_*.csv` |
| `momentum_backtest_v2.py` | 6-2. v2 (6개월·월간·종목수 비교) | `모멘텀백테스트v2_*.csv` |
| `momentum_backtest_filters.py` | 6-3. 필터 주체 비교 | `모멘텀필터비교_*.csv` |
| `momentum_backtest_stoploss.py` | 6-4. 손절 10/15/20% | `손절비교_*.csv` |
| `backtest_universe_weight.py` | 6-5, 6-6. 유니버스 × 가중방식 | `유니버스가중_비교_*.csv` |
| `backtest_sector_cap.py` | 6-6. 섹터 카운트캡·그룹가중캡 (유동시총) | `섹터제약_비교_*.csv` |
| `backtest_equal_sectorcap.py` | 6-6. 동일가중 + 카운트캡 튜닝 | `동일가중_섹터캡튜닝.csv` |
| **`backtest_cap_x_stop.py`** | **최종안** + 6-4. 섹터캡 × 손절 | `섹터캡x손절_*.csv` |
| `interaction_test.py` | 7. 상호작용 검증 (이중정렬 + FM 교차항) | `상호작용_이중정렬.csv` |

실험 8(벤치마크 재정의)·9(레짐필터 기각)는 여기가 아니라
[`backend/scripts/export_algorithm2_backtest_excel.py`](../scripts/export_algorithm2_backtest_excel.py)로
돌렸다. 제출 포맷(별첨2) 출력을 겸하는 스크립트라 `scripts/` 쪽에 뒀다 —
`--regime` 플래그로 레짐필터 on/off를 비교한다.

## discontinued_sector_rotation/ — 폐기된 섹터로테이션

문서 `algorithm2-experiments.md` 0번 섹션. 세 차례 시도해 모두 실패한 경로다.

| 스크립트 | 내용 |
|---|---|
| `sector_rs_persistence_ic.py` | 섹터 상대강도 지속성/IC (3필드 × lookback4 × forward3) |
| `sector_rs_persistence_ic_floatcap.py` | 위의 유동시총가중 변형 — 최적조합이 통째로 바뀜 |
| `sector_internal_correlation.py` | 섹터 내부 상관 + 분산분해(η²) |
| `industry_cohesion_gate_ic.py` | 응집도 게이트 — **역방향 결과** |
| `industry_momentum_decomposition.py` | 산업평균/잔차 성분분해 + Fama-MacBeth |
| `industry_composite_score_test.py` | FM계수 비율 복합스코어 — 최강 단일성분을 못 이김 |

---

## 알려진 문제

- **보일러플레이트 중복이 심하다.** 스크립트마다 DB 로딩·피벗·Newey-West·유니버스 해석을
  각자 복사해서 갖고 있다. 검증 과정에서 빠르게 만들어 쓴 코드라 공통 모듈로 정리하지
  않았다. 새 분석을 추가할 때 리팩터링할지 판단할 것.
- **`backtest_*.py` 의 백테스트 루프도 중복이다.** `app/services/backtest_service.py`의
  엔진을 쓰지 않고 각자 구현했는데, 월간 리밸런싱·수급필터 등 엔진이 지원하지 않는 조건이
  필요했기 때문이다. 알고리즘 #2를 실제로 채택한다면 엔진 쪽으로 옮기는 게 맞다.
- 일부 스크립트는 앞선 스크립트의 산출 CSV를 입력으로 받는다
  (`industry_cohesion_gate_ic.py` ← `섹터내상관성_industry_섹터별응집도.csv`).
  `reference/`가 비어 있으면 선행 스크립트를 먼저 돌려야 한다.
