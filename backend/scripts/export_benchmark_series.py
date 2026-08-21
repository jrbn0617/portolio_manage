"""벤치마크 후보 계열을 총수익(TR) 기준으로 파일로 내린다 (읽기 전용).

**어디서 오는 값인지가 이 스크립트의 핵심이다.** 같은 "코스피"라도 TR/PR 이 다르고,
같은 "ACWI" 라도 통화가 다르면 완전히 다른 시계열이 된다. 그래서 계열마다 아래를
메타시트에 반드시 남긴다 — 총수익구분 / 통화 / 출처 / 산출방법.

계열별 TR 을 어디서 얻는지:

  KOSPI·KOSPI200   prices.close 가 이미 TR 이다. 블룸버그 PX_LAST 와 일별 배당포인트로
                   refresh_benchmark_indices_bbg.py 가 계산해 넣는다(PR 은 raw_close).
                   KOSPI200 은 블룸버그 공식 TR(KOSPI2T)이 따로 있어 대조용으로 함께 낸다.
  MSCI ACWI        NDUEACWF = Net TR, USD. **원화 환산은 여기서 만든다** (× USDKRW).
  S&P 500          SPTR500N = Net TR, USD. 마찬가지로 원화 환산을 함께 낸다.
  KODEX 2종        dividend_adjusted_prices.adj_close = 분배금 재투자, 첫날 100 기준.
  USDKRW           환율이라 TR 개념이 없다. 그대로 낸다.

  KAP 종합채권      KBPMABIN. 채권지수라 PX_LAST 가 곧 총수익이다.

**대용 BM 은 짝이 있어야 검증이 된다.** KODEX 200 은 KOSPI200 과, KODEX 종합채권은
KAP 과 맞대 본다. 한쪽만 있으면 시계열은 나와도 "대용으로 쓸 만한가"에는 답할 수 없다.

산출물 (reference/ 아래, gitignore 대상):
  벤치마크_TR시계열_<날짜>.xlsx   메타 · 요약 · 검증 · 검증_연도별 · 전구간 · 공통구간(100기준)
  benchmarks/<계열>.csv           계열별 원본

사용법:
  python scripts/export_benchmark_series.py
  python scripts/export_benchmark_series.py --from 2014-01-01
"""
import argparse
import datetime
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

OUT_DIR = BACKEND_DIR.parent / "reference"
CSV_DIR = OUT_DIR / "benchmarks"

# (컬럼명, 티커, 원천테이블, 총수익구분, 통화, 계열, 비고)
#   px_close  = prices.close      (지수 계열은 이미 TR)
#   px_raw    = prices.raw_close  (PX_LAST 원본 = PR)
#   adj       = dividend_adjusted_prices.adj_close (분배금 재투자, 첫날 100)
SERIES = [
    ("ACWI_NTR_USD",   "NDUEACWF", "px_close", "TR (Net)", "USD", "MSCI ACWI",
     "블룸버그 NDUEACWF. 세후(Net) 총수익. Gross TR·Local 통화는 미수집"),
    ("SP500_NTR_USD",  "SPTR500N", "px_close", "TR (Net)", "USD", "S&P 500",
     "블룸버그 SPTR500N. 세후(Net) 총수익. Gross TR(SPTR)은 미수집"),
    ("KOSPI_TR",       "KOSPI",    "px_close", "TR (Gross)", "KRW", "KOSPI",
     "PX_LAST + INDX_GROSS_DAILY_DIV 로 자체 계산 (세전)"),
    ("KOSPI_PR",       "KOSPI",    "px_raw",   "PR",         "KRW", "KOSPI",
     "블룸버그 PX_LAST 원본"),
    ("KOSPI200_TR",    "KOSPI200", "px_close", "TR (Gross)", "KRW", "KOSPI200",
     "PX_LAST + INDX_GROSS_DAILY_DIV 로 자체 계산 (세전)"),
    ("KOSPI200_PR",    "KOSPI200", "px_raw",   "PR",         "KRW", "KOSPI200",
     "블룸버그 PX_LAST 원본"),
    ("KOSPI200_TR_BBG", "KOSPI2T", "px_close", "TR (Gross)", "KRW", "KOSPI200",
     "블룸버그 공식 KOSPI2 TR. 자체 계산 대조용"),
    ("KOSDAQ150_TR",   "KOSDAQ150", "px_close", "TR (Gross)", "KRW", "KOSDAQ150",
     "PX_LAST + 일별 배당포인트로 자체 계산 (세전). **코스닥 계열은 블룸버그 공식 TR "
     "티커가 없어 대조할 대상이 없다** — 코스피200 처럼 검증된 값이 아니다"),
    ("KAP종합채권_TR",   "KBPMABIN", "px_close", "TR",         "KRW", "KAP 한국종합채권지수",
     "블룸버그 KBPMABIN. 이미 총수익으로 나온다. KODEX 종합채권(273130)의 벤치마크"),
    ("USDKRW",         "USDKRW",   "px_close", "환율",       "KRW/USD", "원달러 환율",
     "TR 개념 없음. 통화 환산 검증용"),
    ("KODEX200_TR",    "069500",   "adj",      "TR (분배금 재투자)", "KRW", "KODEX 200",
     "종가 기준. NAV 는 DB 에 없음"),
    ("KODEX종합채권_TR", "273130",  "adj",      "TR (분배금 재투자)", "KRW", "KODEX 종합채권(AA-이상)액티브",
     "종가 기준. NAV 는 DB 에 없음"),
]

# 원화 환산해서 함께 낼 USD 계열. (컬럼, 계열명)
KRW_CONV = [("ACWI_NTR_USD", "MSCI ACWI"), ("SP500_NTR_USD", "S&P 500")]

# 요청받았으나 DB 에 없는 계열. 빈 칸 대신 이름을 남긴다 — 빈 칸은 0 이나 누락으로 읽힌다.
MISSING = []

SQL = {
    "px_close": """SELECT p.date, p.close AS v FROM prices p JOIN instruments i ON i.id=p.instrument_id
                   WHERE i.ticker=:t AND p.period='D' AND p.close IS NOT NULL ORDER BY p.date""",
    "px_raw":   """SELECT p.date, p.raw_close AS v FROM prices p JOIN instruments i ON i.id=p.instrument_id
                   WHERE i.ticker=:t AND p.period='D' AND p.raw_close IS NOT NULL ORDER BY p.date""",
    "adj":      """SELECT a.date, a.adj_close AS v FROM dividend_adjusted_prices a
                   JOIN instruments i ON i.id=a.instrument_id
                   WHERE i.ticker=:t AND a.period='D' AND a.adj_close IS NOT NULL ORDER BY a.date""",
}


def load(db, ticker: str, kind: str) -> pd.Series:
    rows = db.execute(text(SQL[kind]), {"t": ticker}).all()
    if not rows:
        raise RuntimeError(f"{ticker} ({kind}) 에 데이터가 없습니다")
    s = pd.Series({r[0]: float(r[1]) for r in rows})
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def annualized(s: pd.Series) -> tuple:
    """(누적수익률, CAGR, 연율변동성). 결측은 제외한 실제 구간으로 잰다."""
    s = s.dropna()
    if len(s) < 2:
        return (np.nan,) * 3
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    total = s.iloc[-1] / s.iloc[0] - 1
    cagr = (1 + total) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    vol = s.pct_change().dropna().std() * np.sqrt(252)
    return total, cagr, vol


def main(start: datetime.date | None) -> None:
    db = SessionLocal()
    try:
        cols = {}
        meta = []
        for name, ticker, kind, tr, ccy, family, note in SERIES:
            s = load(db, ticker, kind)
            cols[name] = s
            meta.append({"컬럼": name, "계열": family, "티커": ticker, "총수익구분": tr,
                         "통화": ccy, "시작": s.index[0].date(), "종료": s.index[-1].date(),
                         "행수": len(s), "비고": note})
            print(f"  {name:<18} {ticker:<9} {s.index[0].date()} ~ {s.index[-1].date()}  {len(s):>6,}행")
    finally:
        db.close()

    wide = pd.DataFrame(cols).sort_index()

    # 원화 환산. **같은 날짜끼리 곱한다** — 이 프로젝트가 이미 쓰는 방식이다
    # (analysis/allocation/fund_picking.py 의 target_series). 해외지수는 미국 종가,
    # 환율은 한국 종가라 하루 안의 시점이 다르지만, 시차 보정은 비교 대상이 정해진 뒤에
    # 거는 것이지 시계열 자체에 미리 넣지 않는다. 환율이 없는 날은 직전값을 쓴다.
    fx = wide["USDKRW"].ffill()
    src = {m["컬럼"]: m for m in meta}
    for usd, family in KRW_CONV:
        krw = usd.replace("_USD", "_KRW")
        wide.insert(wide.columns.get_loc(usd) + 1, krw, wide[usd] * fx)
        v = wide[krw].dropna()
        meta.insert(next(i for i, m in enumerate(meta) if m["컬럼"] == usd) + 1,
                    {"컬럼": krw, "계열": family, "티커": f"{src[usd]['티커']} x USDKRW",
                     "총수익구분": src[usd]["총수익구분"], "통화": "KRW",
                     "시작": v.index[0].date(), "종료": v.index[-1].date(), "행수": len(v),
                     "비고": "USD 계열에 원달러를 같은 날짜로 곱해 이 스크립트가 산출. "
                             "지수는 미국 종가, 환율은 한국 종가라 하루 안의 시점이 다르다"})

    if start:
        wide = wide[wide.index >= pd.Timestamp(start)]

    # 공통구간 — 모든 계열이 값을 가진 첫날부터. 여기서만 서로 비교가 성립한다.
    both = wide.dropna(how="any")
    common = both / both.iloc[0] * 100 if len(both) else both

    summary = pd.DataFrame([
        dict(zip(["컬럼", "누적수익률", "CAGR", "연율변동성"],
                 (c, *annualized(wide[c]))))
        for c in wide.columns
    ])
    common_sum = pd.DataFrame([
        dict(zip(["컬럼", "공통_누적수익률", "공통_CAGR", "공통_연율변동성"],
                 (c, *annualized(common[c]))))
        for c in common.columns
    ]) if len(both) else pd.DataFrame()
    if len(common_sum):
        summary = summary.merge(common_sum, on="컬럼")

    # 검증 — 대용 BM 이 원지수를 얼마나 따라가는지, 자체 TR 이 공식 TR 과 맞는지
    checks = []
    for label, a, b in [
        ("KODEX 200 vs KOSPI200 TR", "KODEX200_TR", "KOSPI200_TR"),
        ("KODEX 종합채권 vs KAP 종합채권", "KODEX종합채권_TR", "KAP종합채권_TR"),
        ("자체 KOSPI200 TR vs 블룸버그 공식", "KOSPI200_TR", "KOSPI200_TR_BBG"),
    ]:
        d = wide[[a, b]].dropna()
        ra, rb = d[a].pct_change().dropna(), d[b].pct_change().dropna()
        diff = ra - rb
        checks.append({
            "비교": label, "구간": f"{d.index[0].date()} ~ {d.index[-1].date()}", "일수": len(d),
            "상관(일간)": round(ra.corr(rb), 6),
            "추적오차(연율)": round(diff.std() * np.sqrt(252), 6),
            "연평균 수익률차": round(annualized(d[a])[1] - annualized(d[b])[1], 6),
        })
    checks = pd.DataFrame(checks)

    # 연도별 분해. **일간 추적오차만 보면 대용 BM 을 과소평가한다** — KODEX 200 은 일간
    # 괴리(호가·괴리율)가 연 2%대로 커 보이지만 연간 수익률 차이는 보수 수준(0.1%p)에
    # 그친다. 둘을 나란히 놓아야 "대용으로 쓸 수 있나"에 답이 된다.
    def by_year(etf: str, idx: str, label: str) -> pd.DataFrame:
        d = wide[[etf, idx]].dropna()
        gap = d[etf].pct_change().dropna() - d[idx].pct_change().dropna()
        first, last = d.groupby(d.index.year).first(), d.groupby(d.index.year).last()
        t = pd.DataFrame({
            "비교": label,
            "추적오차(연율)": gap.groupby(gap.index.year).std() * np.sqrt(252),
            "ETF": last[etf] / first[etf] - 1,
            "지수": last[idx] / first[idx] - 1,
        })
        t["수익률차"] = t["ETF"] - t["지수"]
        t.index.name = "연도"
        return t.reset_index()

    yearly = pd.concat([by_year("KODEX200_TR", "KOSPI200_TR", "KODEX 200 vs KOSPI200"),
                        by_year("KODEX종합채권_TR", "KAP종합채권_TR", "KODEX 종합채권 vs KAP")],
                       ignore_index=True)

    miss = pd.DataFrame(MISSING, columns=["계열", "티커", "상태", "비고"])

    OUT_DIR.mkdir(exist_ok=True)
    CSV_DIR.mkdir(exist_ok=True)
    stamp = datetime.date.today().isoformat()
    xlsx = OUT_DIR / f"벤치마크_TR시계열_{stamp}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl", datetime_format="yyyy-mm-dd") as w:
        pd.DataFrame(meta).to_excel(w, sheet_name="메타", index=False)
        # 미수집이 없으면 아예 쓰지 않는다 — 빈 표의 머리글만 남으면 "여기 뭔가
        # 빠졌나" 하고 다시 들여다보게 된다.
        if len(miss):
            miss.to_excel(w, sheet_name="메타", index=False, startrow=len(meta) + 3)
        summary.to_excel(w, sheet_name="요약", index=False)
        checks.to_excel(w, sheet_name="검증", index=False)
        yearly.to_excel(w, sheet_name="검증_연도별", index=False)
        wide.to_excel(w, sheet_name="전구간")
        common.to_excel(w, sheet_name="공통구간_100기준")

    for c in wide.columns:
        wide[c].dropna().rename("value").to_csv(CSV_DIR / f"{c}.csv", index_label="date")

    print(f"\n엑셀  {xlsx}")
    print(f"CSV   {CSV_DIR}/  ({len(wide.columns)}개)")
    if len(both):
        print(f"공통구간  {both.index[0].date()} ~ {both.index[-1].date()}  {len(both):,}일")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=datetime.date.fromisoformat, default=None)
    main(ap.parse_args().start)
