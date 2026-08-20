"""자산배분 백테스터 — 목표비중 시계열 + 가격 → NAV. 읽기 전용, DB 접근 없음.

**빠른 이유는 날짜를 돌지 않기 때문이다.** 리밸런싱 구간 하나가 루프 한 번이다.
구간 안에서는 수익률 슬라이스의 첫 행에 목표비중을 심고 `cumprod(axis=0)` 한 번으로
전 자산의 비중 드리프트를 동시에 굴린다. 27년 일별 × 7자산이라도 파이썬 루프는
리밸런싱 횟수(분기면 108회)만큼만 돈다.

    seg = asset_returns[s:e+1] + 1     # 구간 수익률 (1 + r)
    seg[0] = w                          # 첫 행을 목표비중으로 대체
    seg = np.cumprod(seg, axis=0)       # 자산별 평가액 경로가 한 번에 나온다
    port_val = np.nansum(seg, axis=1)   # 포트폴리오 평가액

거래비용은 구간 경계에서만 뗀다 — 구간 끝의 **드리프트된 비중**과 다음 목표비중의
차이가 회전율이고, 그 비용을 구간 마지막 날 수익률에서 뺀다.

같은 가격으로 여러 포트폴리오를 돌릴 때는 `Engine` 을 쓴다. `pct_change` 와 numpy
변환을 생성자에서 한 번만 하므로 파라미터 격자 탐색이 싸진다.

원본: `/Users/mckim/Documents/GitHub/backtester/backtester/backtest/worker.py` 의
`backtest_worker`. 계산 규칙은 그대로 옮겼고 아래만 다르다.

  - 지표 정의를 이 리포 규약에 맞췄다 (샤프 = 일평균수익률 × 252 / 연변동성,
    무위험이자율 미반영 — `app/services/backtest_service._compute_metrics` 와 동일)
  - 결과 배열을 미리 잡는다. 원본은 루프마다 `np.append` 로 재할당한다
  - 포트폴리오 시작이 가격 시작보다 뒤면 원본은 `iloc[-1:]` 로 **마지막 행 하나만**
    남는다(`searchsorted(...) - 1` 이 −1 이 되기 때문). 여기서는 0 으로 막았다
  - 멀티프로세싱을 쓰지 않는다 — 벡터화 후에는 포트폴리오 하나가 밀리초 단위라
    프로세스 생성 비용이 더 크다
"""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


# ── 지표 ────────────────────────────────────────────────────────────────────
def metrics(nav: pd.Series, riskfree: pd.Series | None = None) -> dict:
    """수익·위험 지표.

    **무위험수익률을 주면 샤프를 초과수익 기준으로 잰다.** 어느 것을 줄지는 자산이
    정한다 — 국내 펀드·ETF 면 KOFR, 미국 지수·ETF 면 SOFR(`series.load_riskfree`).
    통화가 다른 자산에 남의 무위험수익률을 빼면 초과수익이 환율만큼 왜곡된다.

    riskfree 를 안 주면 `backtest_service._compute_metrics` 와 같아진다 — 이 리포의
    주식 트랙이 무위험이자율 미반영이라 비교하려면 그 형태도 필요하다.
    """
    nav = nav.dropna()
    if len(nav) < 2:
        return {}
    v = nav.to_numpy(dtype=float)
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    r = v[1:] / v[:-1] - 1

    vol = r.std(ddof=1) * np.sqrt(TRADING_DAYS) if len(r) > 1 else np.nan
    peak = np.maximum.accumulate(v)
    out = dict(
        cumulative_return=v[-1] / v[0] - 1,
        cagr=(v[-1] / v[0]) ** (1 / years) - 1 if years > 0 else np.nan,
        annualized_volatility=vol,
        sharpe=(r.mean() * TRADING_DAYS / vol) if vol else np.nan,
        mdd=(v / peak - 1).min(),
        years=years,
    )

    if riskfree is not None:
        # 무위험수익률 지수를 NAV 캘린더에 맞춰 일별 수익률로 바꾼다. 지수가 없는 날은
        # 직전 값을 이어 0% 로 둔다 — 없는 날에 이자가 붙었다고 보지 않는다.
        rf = riskfree.reindex(nav.index.union(riskfree.index)).ffill().reindex(nav.index)
        rf_r = rf.pct_change().to_numpy()[1:]
        rf_r = np.nan_to_num(rf_r)
        excess = r - rf_r
        ex_vol = excess.std(ddof=1) * np.sqrt(TRADING_DAYS) if len(excess) > 1 else np.nan
        rf_cagr = (1 + rf_r).prod() ** (1 / years) - 1 if years > 0 else np.nan
        out.update(
            riskfree_cagr=rf_cagr,
            excess_cagr=out["cagr"] - rf_cagr,
            # 샤프는 정의상 초과수익의 평균/표준편차다. 분모도 초과수익으로 잰다.
            sharpe_rf=(excess.mean() * TRADING_DAYS / ex_vol) if ex_vol else np.nan,
        )
    return out


# ── 엔진 ────────────────────────────────────────────────────────────────────
class Engine:
    """가격을 한 번만 전처리해 두고 포트폴리오를 여러 번 돌린다.

    cost 는 실수 하나이거나 {자산: 비율, 'base': 기본값} 딕셔너리다.
    """

    def __init__(self, price_df: pd.DataFrame, cost: float | dict = 0.0,
                 riskfree: pd.Series | None = None):
        if not price_df.index.is_monotonic_increasing:
            price_df = price_df.sort_index()
        self.index = price_df.index
        self.columns = list(price_df.columns)
        # NaN 을 0 수익으로 둔다 — 상장 전 구간이나 휴장일을 '움직이지 않음'으로 본다.
        self.returns = price_df.pct_change().fillna(0).to_numpy(dtype=float)
        self.cost = self._resolve_cost(cost)
        self.riskfree = riskfree

    def _resolve_cost(self, cost) -> np.ndarray | float:
        if not isinstance(cost, dict):
            return float(cost)
        if "base" not in cost:
            raise ValueError("cost 딕셔너리에는 'base' 키가 있어야 합니다.")
        sr = pd.Series(index=self.columns, data=cost).fillna(cost["base"])
        return sr.to_numpy(dtype=float)

    def run(self, port_df: pd.DataFrame) -> dict:
        """{'nav', 'returns', 'weights', 'turnover', 'rebalances'}"""
        port = self._align(port_df)
        n = len(self.index)

        rebal_idx = np.searchsorted(self.index, port.index, side="right") - 1
        # 포트폴리오 첫 날짜가 가격 첫 날짜보다 앞서면 −1 이 된다. 0 으로 막는다.
        rebal_idx = np.clip(rebal_idx, 0, n - 1)
        bounds = np.column_stack((rebal_idx, np.append(rebal_idx[1:], n)))
        w_arr = port.to_numpy(dtype=float)

        start = int(rebal_idx[0])
        rets = np.full(n, np.nan)
        holdings = np.zeros_like(self.returns)
        turnover = 0.0

        # 최초 편입 비용 — 시작일 수익률에서 뺀다
        rets[start] = -float(np.nansum(self.cost * w_arr[0]))

        for i, ((s, e), w) in enumerate(zip(bounds, w_arr)):
            seg = self.returns[s:e + 1] + 1.0
            if len(seg) == 0:
                continue
            seg[0] = w
            seg = np.cumprod(seg, axis=0)
            holdings[s:s + len(seg)] = seg
            if len(seg) < 2:
                # 리밸런싱 두 건이 같은 거래일에 걸린 경우 — 수익률 구간이 없다
                continue

            port_val = np.nansum(seg, axis=1)
            seg_ret = port_val[1:] / port_val[:-1] - 1.0

            if i < len(w_arr) - 1:
                # 구간 끝의 드리프트된 비중 vs 다음 목표비중
                cur_w = np.nan_to_num(seg[-1] / np.nansum(seg[-1]))
                next_w = np.nan_to_num(w_arr[i + 1])
                delta = np.abs(next_w - cur_w)
                turnover += float(np.nansum(delta))
                seg_ret[-1] -= float(np.nansum(delta * self.cost))

            rets[s + 1:s + len(seg)] = seg_ret

        nav = pd.Series(np.nan, index=self.index, dtype=float)
        nav.iloc[start:] = np.cumprod(1.0 + rets[start:]) * 100.0

        weights = pd.DataFrame(holdings, index=self.index, columns=self.columns)
        weights = weights.div(weights.sum(axis=1), axis=0)

        return dict(nav=nav, returns=pd.Series(rets, index=self.index),
                    weights=weights, turnover=turnover, rebalances=len(w_arr))

    def run_many(self, port_dict: dict) -> dict:
        """{'nav': DataFrame, 'stats': DataFrame, 'detail': {이름: run() 결과}}"""
        detail = {name: self.run(p) for name, p in port_dict.items()}
        nav = pd.concat([r["nav"].rename(name) for name, r in detail.items()], axis=1)

        rows = {}
        for name, r in detail.items():
            m = metrics(r["nav"], self.riskfree)
            # 원본 turnover 는 |Δw| 의 합이라 매수·매도를 모두 센다. 연 단위 편도로
            # 환산해 둔다 — 회전율을 말할 때 보통 쓰는 단위다.
            m["turnover_pa"] = (r["turnover"] / 2) / m["years"] if m.get("years") else np.nan
            m["rebalances"] = r["rebalances"]
            rows[name] = m
        return dict(nav=nav, stats=pd.DataFrame(rows).T, detail=detail)

    def _align(self, port_df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in port_df.columns if c not in self.columns]
        if missing:
            raise ValueError(f"가격에 없는 자산: {', '.join(missing)}")

        port = port_df.reindex(columns=self.columns).fillna(0.0)
        if not port.index.is_monotonic_increasing:
            port = port.sort_index()
        # 가격 시작 이전의 리밸런싱은 **가장 마지막 하나만** 남긴다 — 그 비중으로
        # 시작하는 게 맞고, 그 앞의 것들은 반영할 가격이 없다.
        before = port.index <= self.index[0]
        if before.sum() > 1:
            port = port.iloc[before.sum() - 1:]
        port = port.loc[port.index <= self.index[-1]]
        if port.empty:
            raise ValueError("가격 구간과 겹치는 리밸런싱 날짜가 없습니다.")
        return port


def run_backtest(price_df: pd.DataFrame, port_dict: dict, cost: float | dict = 0.0,
                 period: tuple | None = None, riskfree: pd.Series | None = None) -> dict:
    """한 번만 돌릴 때 쓰는 얇은 껍데기. 반복 실행은 Engine 을 직접 쓴다."""
    if period:
        price_df = price_df.loc[pd.Timestamp(period[0]):pd.Timestamp(period[1])]
    return Engine(price_df, cost, riskfree).run_many(port_dict)
