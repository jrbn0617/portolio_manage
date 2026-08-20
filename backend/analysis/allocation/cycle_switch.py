"""배분 #1 — 경기 사이클 스위치. 신호와 목표비중 생성까지. 읽기 전용.

스펙·배경은 `docs/allocation/allocation1-overview.md`. 원본은 별도 리포
`strategiest/strategiest/cycle_switch.py` 이고, 여기서는 데이터 원천만 우리 DB 로
바꿔 옮겼다(백테스트는 이식 범위 밖).

**원본과 달라진 것은 셋뿐이고 전부 결과를 바꾸지 않는다:**

1. 데이터 원천 — 소스 DB `underlying_index` → 우리 `prices` + `macro_indicators`.
   두 계열이 같은 값인지 네 시점에서 대조했고 금을 뺀 9종이 비율 1.00000 이었다.
2. `x[-1]` → `x.iloc[-1]` — pandas 2.2.3 에서 FutureWarning 이 뜬다("Series.__getitem__
   treating keys as positions is deprecated"). 위치 인덱싱이라 값은 같다.
3. 마진부채 시리즈를 복사해서 쓴다 — 원본은 `margin_debt *= m2_yoy` 로 **호출자의
   DataFrame 열을 변형한다**(pandas 2.2.3 실측). 지금은 뒤 신호가 그 열을 안 써서
   결과에 영향이 없지만, 신호를 추가하거나 순서를 바꾸면 조용히 틀어진다.

**금만 원본과 다른 계열이다.** 원본 `GOLD` 는 LBMA 런던 고시가 계열로 보이고 우리는
`XAU`(뉴욕 마감 현물)를 쓴다 — 2020-03 시점에 2.0% 벌어져 있다. 사용자 결정으로 XAU 를
쓰므로 금 스위치 판정이 원본과 갈릴 수 있다.

사용법:
  python analysis/allocation/cycle_switch.py                 # 최근 상태와 목표비중
  python analysis/allocation/cycle_switch.py --risk 1
  python analysis/allocation/cycle_switch.py --from 2020-01-01 --history
"""
import argparse
import itertools
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db.session import SessionLocal  # noqa: E402
from series import load_macro, load_series, monthly  # noqa: E402

# 원본 티커 → 우리 티커. 비율 1.00000 으로 동일함을 확인한 것들이다(금 제외).
TICKER_MAP = {
    "SPXNTR": "SPTR500N",        # S&P500 NTR
    "MSCIEAFENTR": "NDDUEAFE",   # MSCI EAFE NTR
    "MSCIEMNTR": "NDUEEGF",      # MSCI EM NTR
    "US30Y": "LT11TRUU",         # UST 20Y+ TR
    "US10Y": "LT09TRUU",         # UST 7-10Y TR
    "GOLD": "XAU",               # ⚠ 원본과 다른 계열 (모듈 docstring 참고)
    "USBIL": "LD20TRUU",         # T-Bill
}
MACRO = ["M2NS", "FINRA_MARGIN_DEBT", "DGS10"]

UNIVERSE = dict(
    stock=["SPXNTR", "MSCIEAFENTR", "MSCIEMNTR"],
    bond=["US30Y", "US10Y"],
    gold=["GOLD"],
    cash=["USBIL"],
)

# 키는 주식·금·채권 순서의 3자리. 값은 [주식, 금, 채권, 현금].
# 스위치는 자산을 껐다 켜는 게 아니라 가감한다 — 전부 off 인 '000' 에서도 주식이 60% 다.
weight_sheet = {
    "000": [0.6, 0.05, 0.05, 0.3],
    "010": [0.6, 0.2, 0.05, 0.15],
    "001": [0.6, 0.05, 0.2, 0.15],
    "011": [0.6, 0.2, 0.2, 0],
    "100": [0.76, 0.1, 0.1, 0.04],
    "110": [0.76, 0.15, 0.025, 0.065],
    "101": [0.76, 0.025, 0.175, 0.04],
    "111": [0.76, 0.095, 0.095, 0.05],
}


# ── 데이터 ──────────────────────────────────────────────────────────────────
def read_economic_data(db) -> pd.DataFrame:
    """m2ns · margin_debt · dgs10 (월말 인덱스).

    `macro_indicators` 는 적재 시점에 이미 월말로 통일돼 있어 원본의
    `+ pd.offsets.MonthEnd(1)` 에 해당하는 처리가 필요 없다.
    """
    df = load_macro(db, MACRO)
    return df.rename(columns={"M2NS": "m2ns", "FINRA_MARGIN_DEBT": "margin_debt",
                              "DGS10": "dgs10"})


def read_underlying_price(db, tickers: list[str] | None = None) -> pd.DataFrame:
    """월말 지수 레벨. 컬럼명은 **원본 티커**로 되돌려 이후 로직을 그대로 쓴다."""
    tickers = tickers or list(TICKER_MAP)
    ours = [TICKER_MAP[t] for t in tickers]
    df = monthly(load_series(db, ours))
    return df.rename(columns={v: k for k, v in TICKER_MAP.items()})[tickers]


# ── 스위치 ──────────────────────────────────────────────────────────────────
def apply_hysteresis(data: pd.Series, high_threshold: float,
                     low_threshold: float) -> pd.Series:
    """상단을 넘으면 1, 하단을 밑돌면 0, 사이면 직전 상태 유지. 초기 상태는 0이다."""
    states, current_state = [], 0
    for value in data:
        if value >= high_threshold:
            current_state = 1
        elif value <= low_threshold:
            current_state = 0
        states.append(current_state)
    return pd.Series(states, index=data.index)


def gen_stock_switch_signal(margin_debt: pd.Series, m2: pd.Series) -> pd.Series:
    """마진부채 × M2 전년동월비 = **유동성 가중 신용 팽창지수**.

    나누지 않고 곱하는 게 의도다. 레버리지를 지탱할 기초 체력이 함께 커지고 있는지를
    본다 — 신용잔고가 높은데 M2 증가율이 둔화되거나 마이너스로 가면 지표가 급격히
    꺾이고, 그건 유동성이라는 지지대가 사라진 채 빚만 쌓인 상태를 뜻한다.
    (채권 스위치는 반대로 M2 증가율로 나눈다 — 목적이 다르다.)
    """
    m2_yoy = m2 / m2.shift(12)
    # 원본은 `margin_debt *= m2_yoy` 로 호출자의 열을 변형한다. 복사해서 끊는다.
    weighted = margin_debt * m2_yoy
    z_score = weighted.rolling(12).apply(
        lambda x: (x.iloc[-1] - x.mean()) / x.std(ddof=1))
    return apply_hysteresis(z_score.dropna(), -0.5, -1.5)


def gen_gold_switch_signal(gold: pd.Series, dgs10: pd.Series) -> pd.Series:
    """금 모멘텀이 합성 채권 모멘텀을 **꾸준히** 이기는가 (최근 12개월 중 70% 초과).

    합성 채권은 총수익이 아니다 — 10년물 금리로 월복리 누적한 것이라 금리 변동에
    따른 가격 손익(듀레이션)이 없다. 사실상 '10년물 금리로 굴린 예금'이다.
    """
    def _calc_mom(x):
        return x / x.shift(12) + x / x.shift(6) + x / x.shift(3)

    us_bond_bm = ((dgs10 * 0.01 + 1) ** (1 / 12)).cumprod().dropna()

    df = pd.concat([_calc_mom(gold).rename("gold_mom"),
                    _calc_mom(us_bond_bm).rename("usb_mom")], axis=1).dropna(how="any")

    sr = df.eval("gold_mom > usb_mom").astype(float)
    # 히스테리시스가 없는 대신 이 12개월 70% 조건이 잦은 전환을 막는다.
    return (sr.rolling(12).mean().dropna() > 0.7).astype(float)


def gen_bond_switch_signal(bond: pd.Series, m2: pd.Series) -> pd.Series:
    """채권 모멘텀(6·12개월)을 M2 전년동월비로 나눈 값의 z-score.

    밴드가 0.2σ(−0.8 / −1.0)로 좁아 주식 스위치보다 훨씬 자주 뒤집힌다.
    """
    def _calc_mom(x):
        return x / x.shift(12) + x / x.shift(6)

    bond_mom = _calc_mom(bond) / (m2 / m2.shift(12))
    z_score = bond_mom.rolling(12).apply(
        lambda x: (x.iloc[-1] - x.mean()) / x.std(ddof=1))
    return apply_hysteresis(z_score.dropna(), -0.8, -1.0)


def calc_cycle_data(economic_data: pd.DataFrame, undl_df: pd.DataFrame,
                    start=None, end=None) -> pd.DataFrame:
    """세 스위치를 한 표로. 컬럼 순서가 곧 weight_sheet 키의 자릿수다."""
    df = economic_data.copy()
    # M2·마진부채는 익월 발표라 한 달 민다 — 미래 참조 방지.
    # DGS10 은 월평균이라 월말에 이미 확정돼 있어 밀지 않는다.
    df["m2ns"] = df["m2ns"].shift(1)
    df["margin_debt"] = df["margin_debt"].shift(1)

    out = pd.concat([
        gen_stock_switch_signal(df["margin_debt"], df["m2ns"]).rename("stock"),
        gen_gold_switch_signal(undl_df["GOLD"], df["dgs10"]).rename("gold"),
        gen_bond_switch_signal(undl_df["US30Y"], df["m2ns"]).rename("bond"),
    ], axis=1)
    # 세 신호의 워밍업 길이가 달라 앞쪽에 NaN 이 남는다(마진부채가 가장 늦게 시작해
    # 1998-01 이 유효 시작점이다). 끝쪽은 비지 않는다 — M2·마진부채를 한 달 밀어 쓰므로
    # 최신 월은 전월 발표치로 채워진다.
    out = out.dropna(how="any")
    return out.loc[start:end]


# ── 비중 ────────────────────────────────────────────────────────────────────
def asset_allocation(saa_df: pd.DataFrame, risk: int) -> pd.DataFrame:
    universe = list(sorted(set(itertools.chain.from_iterable(UNIVERSE.values()))))
    port_df = pd.DataFrame(index=saa_df.index, columns=universe, data=0, dtype=float)

    # 주식 — 미국:EAFE:EM = 7:2:1 (위험도 무관)
    port_df.loc[:, "SPXNTR"] = saa_df["stock"] * 0.7
    port_df.loc[:, "MSCIEAFENTR"] = saa_df["stock"] * 0.2
    port_df.loc[:, "MSCIEMNTR"] = saa_df["stock"] * 0.1

    # 채권 — 위험도가 높을수록 듀레이션을 줄인다
    long_share = {0: 0.8, 1: 0.65}.get(risk, 0.5)
    port_df.loc[:, "US30Y"] = saa_df["bond"] * long_share
    port_df.loc[:, "US10Y"] = saa_df["bond"] * (1 - long_share)

    port_df.loc[:, "GOLD"] = saa_df["gold"] * 1

    # 위험자산 전체를 줄이고 줄어든 만큼은 아래에서 현금이 받는다
    if risk == 1:
        port_df *= 0.8
    elif risk == 2:
        port_df *= 0.65

    port_df.loc[:, "USBIL"] = 1 - port_df.sum(axis=1)
    port_df = port_df.round(12)
    assert (port_df.sum(axis=1).round(12) == 1).all()
    return port_df


def gen_mp(cycle_data_df: pd.DataFrame, risk: int = 0, freq: str = "QE",
           start=None, end=None) -> pd.DataFrame:
    """리밸런싱 날짜별 목표비중.

    **신호는 월간인데 리밸런싱은 분기말이다** — 분기 중간에 스위치가 뒤집혀도 반영되지
    않는다. 분기말은 월말의 부분집합이라 인덱스가 그대로 맞는다.
    """
    idx = cycle_data_df.index
    reb_dates = pd.date_range(start or idx[0], end or idx[-1], freq=freq)
    reb_dates = reb_dates.intersection(idx)
    if len(reb_dates) == 0:
        raise RuntimeError("리밸런싱 날짜가 신호 인덱스와 하나도 겹치지 않습니다.")

    sig = cycle_data_df.loc[reb_dates].astype(int).astype(str).sum(axis=1)
    saa_df = pd.DataFrame([weight_sheet[s] for s in sig], index=reb_dates,
                          columns=["stock", "gold", "bond", "cash"], dtype=float)
    assert len(saa_df) == int(saa_df.sum(axis=1).sum())
    return asset_allocation(saa_df, risk)


def build(risk: int = 0, start=None, end=None, freq: str = "QE"):
    db = SessionLocal()
    try:
        econ = read_economic_data(db)
        undl = read_underlying_price(db)
        cycle = calc_cycle_data(econ, undl, start, end)
        return cycle, gen_mp(cycle, risk=risk, freq=freq, start=start, end=end)
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="배분 #1 — 경기 사이클 스위치")
    p.add_argument("--risk", type=int, default=0, choices=[0, 1, 2])
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--freq", default="QE", help="리밸런싱 주기 (기본 분기말)")
    p.add_argument("--history", action="store_true", help="전체 이력 출력")
    a = p.parse_args()

    cycle, mp = build(a.risk, a.start, a.end, a.freq)
    label = {"stock": "주식", "gold": "금", "bond": "채권"}

    print(f"신호 {cycle.index[0].date()} ~ {cycle.index[-1].date()}  ({len(cycle):,}개월)")
    print(f"리밸런싱 {len(mp):,}회 · risk={a.risk}\n")

    print("최근 스위치")
    for d, row in cycle.tail(6).iterrows():
        state = " ".join(f"{label[c]} {'ON ' if row[c] else 'off'}" for c in cycle.columns)
        key = "".join(str(int(row[c])) for c in cycle.columns)
        print(f"  {d.date()}  {state}   [{key}] {weight_sheet[key]}")

    print(f"\n목표비중 (최근 리밸런싱 {mp.index[-1].date()})")
    for t, w in mp.iloc[-1].items():
        if w > 0:
            print(f"  {t:<12} {TICKER_MAP[t]:<10} {w:7.2%}")

    if a.history:
        print("\n전체 이력")
        print(mp.to_string(float_format=lambda v: f"{v:.4f}"))
