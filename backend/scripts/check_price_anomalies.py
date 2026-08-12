"""가격 시계열의 구조적 이상치를 전 종목 스캔한다.

백테스트 수익률을 오염시키는 두 가지 패턴을 찾는다. 둘 다 `corporate_action_events`
(시계열이 아예 끊긴 상장폐지/인수합병)로는 잡히지 않는다 — 데이터가 계속 이어지기
때문에 backtest_service.find_series_break()가 발동하지 않는다.

  1) 하루 만에 close가 ±50% 넘게 튄 구간
     KRX 가격제한폭이 ±30%라 정상 거래로는 나올 수 없다. 상승 점프는 미조정
     병합/감자(예: 052670 제일바이오 1,500:1)를, 하락 붕괴는 대부분 정리매매
     첫날(가격제한폭 미적용)을 뜻한다. 정리매매는 직후 시계열이 끝나므로
     '점프 후 남은 거래일 수'로 구분한다.

  2) close가 20거래일 이상 완전히 동일하게 반복된 구간
     거래정지 기간의 동결 기준가다. 실거래가 아니므로 그 구간에 신규 편입되면
     안 되고, 동결이 시계열 끝까지 이어지는 종목은 지금도 정지 중이다.
     스팩(기업인수목적회사)은 정상적으로 가격이 정체하므로 따로 표시한다.

  3) 수정주가(close)가 제한폭 ±30%를 넘게 움직인 날 — 권리락 반영 여부 판정
     정상적으로 조정된 권리락일은 raw_close만 크게 빠지고 close는 연속이며,
     수정계수(close/raw_close)가 그날 바뀐다. 여기서 찾는 건 그게 안 된 두 경우다.
       유형B(권리락 미반영): close와 raw가 같은 비율로 튀고 계수는 그대로.
                            권리락이 수정주가에 반영되지 않았다.
       유형A(계수 오적용):   close만 튀고 raw는 정상 범위. 수정계수가 잘못된
                            구간에 걸려 있다(예: 053580 웹케시 2019-01-25).

사용법:
    python scripts/check_price_anomalies.py            # 요약
    python scripts/check_price_anomalies.py --csv      # reference/ 에 CSV 저장
"""
import csv
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REFERENCE_DIR = BACKEND_DIR.parent / "reference"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402

JUMP_THRESHOLD_UP = 1.5
JUMP_THRESHOLD_DOWN = 1 / 1.5
FLAT_MIN_DAYS = 20
# 정리매매는 길어야 7거래일이라, 점프 후 이만큼도 안 남았으면 상장폐지 경로로 본다.
TERMINAL_DAYS = 12
# KRX 가격제한폭 ±30%. 부동소수점 여유를 두고 살짝 바깥부터 본다.
# 하한은 1/1.305가 아니라 0.695다 — 1/1.305는 -23.4%라 정상 하한가(-30%)까지 다 걸린다.
LIMIT_UP = 1.305
LIMIT_DOWN = 0.695
# 수정계수(close/raw)가 이 비율 안에서 움직이면 "안 바뀐 것"으로 본다.
FACTOR_TOLERANCE = 0.005
# 상장 초기 기준가 형성 구간(공모가 대비 60~400% 등)은 제한폭 밖 변동이 정상이라 건너뛴다.
SKIP_FIRST_ROWS = 5

JUMP_SQL = text("""
with p as (
  select instrument_id, date, close,
         lag(close) over w as prev_close,
         lag(date)  over w as prev_date
  from prices where period = 'D'
  window w as (partition by instrument_id order by date)
),
jumps as (
  select *, close / prev_close as ratio from p
  where prev_close is not null and prev_close > 0
    and (close / prev_close > :up or close / prev_close < :down)
)
select i.ticker, i.name, i.market,
       j.prev_date, j.prev_close, j.date, j.close, j.ratio,
       (j.date - j.prev_date) as day_gap,
       (select count(*) from prices q
         where q.instrument_id = j.instrument_id and q.period = 'D' and q.date > j.date) as days_after
from jumps j
join instruments i on i.id = j.instrument_id
order by j.ratio desc
""")

FLAT_SQL = text("""
with p as (
  select instrument_id, date, close,
         row_number() over (partition by instrument_id order by date)
           - row_number() over (partition by instrument_id, close order by date) as grp
  from prices where period = 'D'
),
runs as (
  select instrument_id, close, min(date) as start_date, max(date) as end_date, count(*) as n_days
  from p group by instrument_id, close, grp
),
lastday as (
  select instrument_id, max(date) as last_date from prices where period = 'D' group by instrument_id
),
latest_mem as (
  select instrument_id, max(as_of_date) as last_mem from index_memberships group by instrument_id
)
select i.ticker, i.name, i.market, r.close, r.start_date, r.end_date, r.n_days,
       (r.end_date = l.last_date) as ongoing,
       m.last_mem
from runs r
join instruments i on i.id = r.instrument_id
join lastday l on l.instrument_id = r.instrument_id
left join latest_mem m on m.instrument_id = r.instrument_id
where r.n_days >= :min_days
order by r.n_days desc
""")


ADJ_SQL = text("""
with p as (
  select instrument_id, date, close, raw_close,
         row_number()   over w as rn,
         lag(close)     over w as prev_close,
         lag(raw_close) over w as prev_raw,
         lag(date)      over w as prev_date
  from prices where period = 'D'
  window w as (partition by instrument_id order by date)
),
moves as (
  select *,
         close / prev_close as close_move,
         raw_close / prev_raw as raw_move,
         (close / raw_close) / (prev_close / prev_raw) as factor_change
  from p
  where rn > :skip_rows
    and prev_close > 0 and prev_raw > 0
    and close > 0 and raw_close > 0
),
first_mem as (
  select instrument_id, min(as_of_date) as first_listed from index_memberships group by instrument_id
)
select i.ticker, i.name, i.market,
       m.prev_date, m.date, m.prev_close, m.close, m.prev_raw, m.raw_close,
       m.close_move, m.raw_move, m.factor_change,
       (m.date - m.prev_date) as day_gap,
       f.first_listed, e.last_data_date as resolved_from,
       (select count(*) from prices q
         where q.instrument_id = m.instrument_id and q.period = 'D' and q.date > m.date) as days_after
from moves m
join instruments i on i.id = m.instrument_id
left join first_mem f on f.instrument_id = m.instrument_id
left join corporate_action_events e on e.instrument_id = m.instrument_id
where (m.close_move > :up or m.close_move < :down)
order by i.ticker, m.date
""")


def is_spac(name: str) -> bool:
    return "기업인수목적" in name or "스팩" in name


def classify_adjustment(row) -> str:
    """수정계수 관점에서 이 ±30% 초과 이동을 분류한다.

    정상 조정된 권리락은 raw만 크게 빠지고 close는 연속이라 애초에 여기 안 걸린다.
    close가 제한폭을 넘었다는 건 조정이 안 됐거나 잘못 걸렸다는 뜻이다.
    어디에도 안 맞으면 버리지 말고 C로 남긴다 — 계수가 바뀌긴 했는데 제한폭 초과를
    상쇄하지 못한 '부분 조정'이 여기 들어온다.
    """
    # 상장 전 구간(코넥스·장외)은 DataGuide가 같은 종목코드로 이어붙여 준다. 상장일에
    # 기준가가 새로 형성되므로 제한폭을 넘는 계단이 생기는 게 정상이고, 재요청해도 같은
    # 값이 온다. index_memberships 최초 스냅샷 이전이면 아직 미상장으로 본다.
    # (스냅샷이 2018-12-31부터라, 그 이전 상장 종목의 2018-12-28~30 구간도 여기 걸린다.
    #  가격 데이터도 2018-12-28부터라 실제 영향은 2~3거래일뿐이다.)
    if row.first_listed is not None and row.date < row.first_listed:
        return "제외_상장전구간"

    # corporate_action_events에 청산일이 등록돼 있으면 그 이후 시세는 백테스트가 이미
    # 무시한다(상폐 후 K-OTC 등). 다시 올릴 필요 없다.
    if row.resolved_from is not None and row.date >= row.resolved_from:
        return "제외_이벤트등록됨"

    factor_change = float(row.factor_change)
    raw_move = float(row.raw_move)
    if abs(factor_change - 1.0) < FACTOR_TOLERANCE:
        # 계수 그대로 = close와 raw가 함께 튐 = 권리락이 반영 안 됨
        return "B_권리락미반영"
    if LIMIT_DOWN <= raw_move <= LIMIT_UP:
        # raw는 정상 범위인데 close만 튐 = 계수가 엉뚱한 구간에 적용됨
        return "A_계수오적용"
    return "C_부분조정"


def main(write_csv: bool = False):
    db = SessionLocal()
    jumps = db.execute(JUMP_SQL, {"up": JUMP_THRESHOLD_UP, "down": JUMP_THRESHOLD_DOWN}).all()
    flats = db.execute(FLAT_SQL, {"min_days": FLAT_MIN_DAYS}).all()
    adj_rows = db.execute(
        ADJ_SQL, {"up": LIMIT_UP, "down": LIMIT_DOWN, "skip_rows": SKIP_FIRST_ROWS}
    ).all()
    max_mem = db.execute(text("select max(as_of_date) from index_memberships")).scalar()
    db.close()

    ups = [r for r in jumps if float(r.ratio) > 1]
    downs = [r for r in jumps if float(r.ratio) < 1]
    downs_terminal = [r for r in downs if r.days_after <= TERMINAL_DAYS]
    downs_alive = [r for r in downs if r.days_after > TERMINAL_DAYS]

    ongoing = [r for r in flats if r.ongoing]
    ongoing_real = [r for r in ongoing if not is_spac(r.name)]
    in_universe = [r for r in ongoing_real if r.last_mem == max_mem]

    print(f"[1] 하루 ±50% 초과 점프 {len(jumps)}건")
    print(f"    상승 {len(ups)}건 (미조정 병합/감자 의심)")
    print(f"    하락 {len(downs)}건 = 정리매매→상폐 {len(downs_terminal)}건 "
          f"+ 이후에도 계속 거래 {len(downs_alive)}건(확인 필요)")
    print(f"\n{'티커':<8}{'종목명':<18}{'점프일':<12}{'직전종가':>10}{'종가':>10}{'배수':>9}{'이후일수':>8}")
    for r in ups + downs_alive:
        print(f"{r.ticker:<8}{r.name[:16]:<18}{str(r.date):<12}{float(r.prev_close):>10,.0f}"
              f"{float(r.close):>10,.0f}{float(r.ratio):>9.2f}{r.days_after:>8}")

    print(f"\n[2] 종가 {FLAT_MIN_DAYS}거래일 이상 동결 {len(flats)}건 "
          f"(현재도 동결 중 {len(ongoing)}건, 스팩 제외 {len(ongoing_real)}건)")
    print(f"    이 중 최신 유니버스({max_mem})에 아직 편입돼 있음: {len(in_universe)}건")
    print(f"\n{'티커':<8}{'종목명':<18}{'시장':<12}{'동결가':>10}{'동결시작':<13}{'일수':>6}")
    for r in in_universe:
        print(f"{r.ticker:<8}{r.name[:16]:<18}{(r.market or '-')[:10]:<12}{float(r.close):>10,.0f}  "
              f"{str(r.start_date):<13}{r.n_days:>6}")

    # [3] 수정주가 판정 — 정리매매→상폐 경로는 실제 시세라 제외한다.
    classified, excluded = [], []
    for r in adj_rows:
        if r.days_after <= TERMINAL_DAYS:
            continue
        kind = classify_adjustment(r)
        (excluded if kind.startswith("제외_") else classified).append((kind, r))
    pre_listing = [(k, r) for k, r in excluded if k == "제외_상장전구간"]
    resolved = [(k, r) for k, r in excluded if k == "제외_이벤트등록됨"]

    type_b = [r for k, r in classified if k == "B_권리락미반영"]
    type_a = [r for k, r in classified if k == "A_계수오적용"]
    type_c = [r for k, r in classified if k == "C_부분조정"]
    by_ticker: dict[str, list] = {}
    for _, r in classified:
        by_ticker.setdefault(r.ticker, []).append(r)

    print(f"\n[3] 수정주가 의심 {len(classified)}건 / {len(by_ticker)}종목 "
          f"(close가 ±30% 초과 이동, 정리매매 제외)")
    print(f"    유형B 권리락 미반영: {len(type_b)}건")
    print(f"    유형A 계수 오적용:   {len(type_a)}건")
    print(f"    유형C 부분조정:      {len(type_c)}건")
    print(f"    (제외: 상장 전 구간 경계 {len(pre_listing)}건 / "
          f"{len({r.ticker for _, r in pre_listing})}종목, "
          f"이벤트 등록됨 {len(resolved)}건 / {len({r.ticker for _, r in resolved})}종목)")
    print(f"\n{'티커':<8}{'종목명':<18}{'건수':>4}  {'최초':<12}{'최종':<12}{'유형'}")
    for ticker in sorted(by_ticker, key=lambda t: -len(by_ticker[t])):
        rows = by_ticker[ticker]
        kinds = {k for k, r in classified if r.ticker == ticker}
        print(f"{ticker:<8}{rows[0].name[:16]:<18}{len(rows):>4}  "
              f"{str(min(r.date for r in rows)):<12}{str(max(r.date for r in rows)):<12}"
              f"{'+'.join(sorted(k[0] for k in kinds))}")

    if write_csv:
        REFERENCE_DIR.mkdir(exist_ok=True)
        adj_path = REFERENCE_DIR / "가격이상_수정주가.csv"
        with adj_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["유형", "티커", "종목명", "시장", "직전일", "해당일", "직전종가", "종가",
                        "직전실종가", "실종가", "종가변동", "실종가변동", "계수변화", "경과일", "이후거래일수"])
            for kind, r in classified:
                w.writerow([kind, r.ticker, r.name, r.market, r.prev_date, r.date,
                            r.prev_close, r.close, r.prev_raw, r.raw_close,
                            round(float(r.close_move), 4), round(float(r.raw_move), 4),
                            round(float(r.factor_change), 4), r.day_gap, r.days_after])
        print(f"\n저장: {adj_path}")

        jump_path = REFERENCE_DIR / "가격이상_점프.csv"
        flat_path = REFERENCE_DIR / "가격이상_동결.csv"
        with jump_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["티커", "종목명", "시장", "직전일", "직전종가", "점프일", "종가", "배수", "경과일", "점프후거래일수"])
            for r in jumps:
                w.writerow([r.ticker, r.name, r.market, r.prev_date, r.prev_close,
                            r.date, r.close, round(float(r.ratio), 4), r.day_gap, r.days_after])
        with flat_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["티커", "종목명", "시장", "동결가", "시작", "종료", "일수", "현재도동결", "최종편입일"])
            for r in flats:
                w.writerow([r.ticker, r.name, r.market, r.close, r.start_date,
                            r.end_date, r.n_days, r.ongoing, r.last_mem])
        print(f"\n저장: {jump_path}\n      {flat_path}")


if __name__ == "__main__":
    main(write_csv="--csv" in sys.argv)
