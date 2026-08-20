"""자산군별 추종 펀드 선정. 읽기 전용.

주어진 **유니버스(펀드코드 목록) 안에서만** 고른다. 자산군마다 참조 지수·시차·상관
하한이 다르고, 통과한 것 중 최근 1년 수익률(롱숏은 샤프)이 가장 높은 것을 뽑는다.

**시차(LAG)가 핵심이다.** 국내 공모펀드의 기준가는 해외 시장을 며칠 늦게 반영한다.
맞추지 않으면 진짜 추종 펀드가 밀려난다 — 실측으로 삼성미국S&P500인덱스UH 의 주간
상관이 LAG0 에서 0.48, LAG2 에서 0.78 이었다. 미국·유럽이 2영업일, 일본·신흥국·국내가
1영업일이다(시차가 클수록 한국 시각과 멀다).

상관이 0.98 같은 값이 나오지는 않는다. 주 단위로 묶어도 반영 시차가 정수 일이 아니고
주 경계에서 잘리기 때문이다. **임계값은 그 현실에 맞춰진 값**이라 계산 방식을 바꾸면
같이 바꿔야 한다 — 원본 `strategiest/tests/fund/fund_picking` 과 같은 방식을 쓴다:

    주간 수익률 = (일별 수익률 + 1).resample('W').prod() - 1     (마지막-마지막 아님)
    52주 창, 수익률은 창 첫날 대비 마지막날, 변동성은 일별 std × √260

사용법:
  python analysis/allocation/fund_picking.py --universe universe.csv
  python analysis/allocation/fund_picking.py --universe u.csv --base 2026-07-31 --show 10
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db.session import SessionLocal  # noqa: E402
from series import load_series  # noqa: E402

# 유니버스 전체에 거는 제외 규칙 — 구조가 특수해 지수 추종으로 볼 수 없는 것들.
EXCLUDE_NAME = "EMP|TDF|OCIO|디딤|커버드콜|엄브렐러|월배당|혼합자산|TIF|목표전환|파생"
MIN_AUM = 6000        # 설정원본 백만원 = 60억
WEEKS = 52
TRADING_DAYS = 260    # 원본과 동일 (252 아님)

# 자산군별 규칙. lag 가 리스트면 **펀드마다 상관이 높은 쪽을 쓴다** — 한 자산군 안에
# 시장이 섞여 있기 때문이다. EAFE 는 유럽(2영업일)과 아시아(1영업일)가, EM 은 동유럽
# 신흥국(2영업일)과 아시아 신흥국(1영업일)이 함께 들어 있다.
RULES = {
    "US":    dict(undl="SPTR500N", krw=True,  lag=2,      corr_cut=0.80, rank="return",
                  category_in=["주식형", "주식파생형"], name_not_in=["혼합"]),
    "EAFE":  dict(undl="NDDUEAFE", krw=True,  lag=[1, 2], corr_cut=0.70, rank="return",
                  category_in=["주식형", "주식파생형", "재간접형"], name_not_in=["혼합"]),
    # region 을 해외로 묶는 이유 — 한국이 MSCI EM 구성국이라 국내 주식형 펀드가 상관
    # 0.78 로 통과한다. 그러면 EM 과 KR 슬리브가 같은 펀드를 집어 익스포저가 겹친다.
    "EM":    dict(undl="NDUEEGF",  krw=True,  lag=[1, 2], corr_cut=0.75, rank="return",
                  category_in=["주식형", "주식파생형", "재간접형"], name_not_in=["혼합"],
                  region_in=["해외"]),
    "KR":    dict(undl="KOSPI2T",  krw=False, lag=1,      corr_cut=0.90, rank="return",
                  category_in=["주식형", "주식파생형"], name_not_in=["혼합"]),
    "GOLD":  dict(undl="XAU",      krw=True,  lag=1,      corr_cut=0.70, rank="return",
                  category_in=["주식형", "주식파생형", "특별자산", "재간접형"],
                  name_in=["골드"]),
    "KR30Y": dict(undl="KIS30Y",   krw=False, lag=1,      corr_cut=0.90, rank="return",
                  category_in=["채권형", "재간접형"], name_not_in=["주식"]),
    "KR10Y": dict(undl="KIS10Y",   krw=False, lag=1,      corr_cut=0.90, rank="return",
                  category_in=["채권형", "재간접형"], name_not_in=["주식"]),
    # MMF 가 아니라 **초단기채**를 뽑는다(사용자 결정) — 국내 채권형 중 이름에 초단기가
    # 들어간 것. 이름에 MMF 가 있는 것은 뺀다.
    #
    # **상관 컷을 걸지 않는다.** 현금성 자산은 수익률이 거의 직선이라 주간 변동의 대부분이
    # 노이즈이고, 그 노이즈는 CD 지수와 무관하다. 실측으로 초단기채 6종의 CD 지수 대비
    # 주간 상관이 0.27~0.38 이라 0.70 컷을 아무도 못 넘겼다. 반면 1년 수익률은
    # 3.05~3.12% 로 CD 지수와 거의 같다 — 수준은 잘 따라간다. corr 는 표시만 한다.
    "MMF":   dict(undl="KISCD",    krw=False, lag=1,      corr_cut=None, rank="return",
                  category_in=["채권형"], region_in=["국내"],
                  name_in=["초단기"], name_not_in=["MMF"]),
    # 추종할 지수가 없다 — 이름으로 고르고 위험 대비 수익으로 줄 세운다.
    "LS":    dict(undl=None, krw=False, lag=1, corr_cut=None, rank="sharpe",
                  name_in=["롱숏"]),
}


# ── 데이터 ──────────────────────────────────────────────────────────────────
def load_universe(path: str) -> list[str]:
    """펀드코드 CSV. 열 이름은 universe / fund_code 중 아무거나."""
    df = pd.read_csv(path)
    for col in ("universe", "fund_code", "code"):
        if col in df.columns:
            return df[col].dropna().astype(str).str.strip().tolist()
    return df.iloc[:, 0].dropna().astype(str).str.strip().tolist()


def load_risk_grades(db, codes: list[str]) -> pd.DataFrame:
    """DART 투자설명서에서 읽어 둔 투자위험등급 (scripts/refresh_fund_risk_grades.py).
    아직 조회하지 않은 펀드는 비어 있다 — 선정에는 쓰지 않고 표시만 한다."""
    rows = db.execute(text("""
        SELECT fund_code, risk_grade, risk_label, rcept_dt
        FROM fund_risk_grades WHERE fund_code = ANY(:c)"""), {"c": codes}).all()
    return pd.DataFrame(rows, columns=["fund_code", "risk_grade", "risk_label",
                                       "risk_dt"]).set_index("fund_code")


def load_funds(db, codes: list[str], start, end):
    """(정보 DataFrame, 수정기준가 DataFrame, 설정원본 Series). 운용펀드만."""
    info = pd.DataFrame(db.execute(text("""
        SELECT f.fund_code, f.id, f.name, f.category, f.region, f.manage_company
        FROM funds f WHERE f.fund_code = ANY(:c) AND f.is_manage_fund"""),
        {"c": codes}).all(),
        columns=["fund_code", "id", "name", "category", "region", "company"])
    if info.empty:
        raise RuntimeError("유니버스에 해당하는 운용펀드가 없습니다.")
    ids = info["id"].tolist()

    rows = db.execute(text("""
        SELECT fund_id, base_dt, adj_nav FROM fund_adjusted_navs
        WHERE fund_id = ANY(:i) AND base_dt BETWEEN :s AND :e"""),
        {"i": ids, "s": start, "e": end}).all()
    nav = pd.DataFrame(rows, columns=["fid", "d", "v"])
    nav["v"] = nav["v"].astype(float)
    nav = nav.pivot(index="d", columns="fid", values="v").sort_index()
    nav.index = pd.to_datetime(nav.index)

    aum = pd.DataFrame(db.execute(text("""
        SELECT f.id, n.aum FROM funds f
        JOIN LATERAL (SELECT aum FROM fund_navs WHERE fund_id = f.id AND aum IS NOT NULL
                      AND base_dt <= :e ORDER BY base_dt DESC LIMIT 1) n ON TRUE
        WHERE f.id = ANY(:i)"""), {"i": ids, "e": end}).all(),
        columns=["id", "aum"]).set_index("id")["aum"].astype(float)

    return info.set_index("id"), nav, aum


def target_series(db, rule: dict, start, end) -> pd.Series | None:
    """참조 지수. 해외 자산은 원화 환산한다 — 언헤지 펀드의 기준가에는 환효과가
    이미 들어 있으므로, 비교 대상도 원화여야 같은 것을 재는 것이 된다."""
    if rule["undl"] is None:
        return None
    cols = [rule["undl"]] + (["USDKRW"] if rule["krw"] else [])
    px = load_series(db, cols, start, end).ffill()
    s = px[rule["undl"]]
    return (s * px["USDKRW"]).dropna() if rule["krw"] else s.dropna()


# ── 계산 ────────────────────────────────────────────────────────────────────
def weekly(daily: pd.DataFrame | pd.Series):
    """주간 복리 수익률. 마지막-마지막이 아니라 일별을 곱해 쌓는다 — 원본과 같다."""
    return ((daily.pct_change(fill_method=None) + 1).resample("W").prod() - 1).iloc[1:]


def evaluate(nav: pd.DataFrame, tgt: pd.Series | None, lags, base) -> pd.DataFrame:
    """펀드별 corr(최적 시차) · 1년 수익률 · 변동성 · 샤프."""
    fw = weekly(nav).loc[:base]
    ret = nav.iloc[-1] / nav.iloc[0] - 1
    vol = nav.pct_change(fill_method=None).std() * np.sqrt(TRADING_DAYS)
    out = pd.DataFrame({"return": ret, "vol": vol})
    out["sharpe"] = out["return"] / out["vol"]

    if tgt is None:
        out["corr"], out["lag"] = np.nan, np.nan
        return out

    best = None
    for lag in (lags if isinstance(lags, (list, tuple)) else [lags]):
        uw = weekly(tgt.shift(lag)).loc[:base]
        idx = fw.index.intersection(uw.index)
        c = fw.loc[idx].corrwith(uw.reindex(idx)).rename(lag)
        best = c.to_frame() if best is None else best.join(c)
    out["corr"] = best.max(axis=1)
    out["lag"] = best.idxmax(axis=1)
    return out


def apply_filters(info: pd.DataFrame, rule: dict) -> pd.DataFrame:
    f = info
    if rule.get("category_in"):
        f = f[f["category"].isin(rule["category_in"])]
    if rule.get("region_in"):
        f = f[f["region"].isin(rule["region_in"])]
    if rule.get("name_in"):
        f = f[f["name"].str.contains("|".join(rule["name_in"]), regex=True, na=False)]
    if rule.get("name_not_in"):
        f = f[~f["name"].str.contains("|".join(rule["name_not_in"]), regex=True, na=False)]
    return f


def pick(universe_codes: list[str], base: str, classes=None, exclusive: bool = True) -> dict:
    """자산군별 선정. exclusive 면 **한 펀드가 두 슬리브에 들어가지 않는다**.

    지수끼리 상관이 높으면 같은 펀드가 여러 자산군의 컷을 동시에 통과한다 — 실제로
    KR30Y·KR10Y 가 같은 글로벌 채권펀드를 1위로 뽑았다. 그러면 배분이 겹쳐 의도한
    익스포저가 나오지 않는다.

    **RULES 의 정의 순서대로 뽑고 이미 뽑힌 펀드는 뒤 자산군에서 뺀다.** 순서가 곧
    우선권이라 좁은 자산군을 앞에 둔다(KR30Y 가 KR10Y 보다 앞).
    """
    base_dt = pd.Timestamp(base)
    start = base_dt - pd.offsets.Week(WEEKS + 1)

    db = SessionLocal()
    try:
        info, nav, aum = load_funds(db, universe_codes, start.date(), base_dt.date())
        nav = nav.loc[:base_dt]

        # 유니버스 공통 컷 — 설정액, 이름
        keep = aum[aum > MIN_AUM].index
        info = info.loc[info.index.intersection(keep)]
        info = info[~info["name"].str.contains(EXCLUDE_NAME, regex=True, na=False)]
        # 창 전체에 기준가가 있어야 수익률·상관이 성립한다
        full = nav.columns[nav.notna().all()]
        info = info.loc[info.index.intersection(full)]
        nav = nav[info.index]

        grades = load_risk_grades(db, info["fund_code"].tolist())
        result, taken = {}, set()
        for name, rule in RULES.items():
            if classes and name not in classes:
                continue
            cand = apply_filters(info, rule)
            if exclusive and taken:
                cand = cand.loc[~cand.index.isin(taken)]
            if cand.empty:
                result[name] = dict(rule=rule, table=pd.DataFrame(), picked=None,
                                    reason="필터 통과 펀드 없음")
                continue

            tgt = target_series(db, rule, start.date(), base_dt.date())
            ev = evaluate(nav[cand.index], tgt, rule["lag"], base_dt)
            tbl = cand.join(ev).join(grades, on="fund_code")

            if rule["corr_cut"] is not None and tgt is not None:
                tbl = tbl[tbl["corr"] > rule["corr_cut"]]
            tbl = tbl.sort_values(rule["rank"], ascending=False)

            reason = None
            if tgt is None and rule["corr_cut"] is not None:
                reason = "참조 지수 미적재 — 상관 필터를 적용하지 못했다"
            picked = tbl.index[0] if len(tbl) else None
            if picked is not None:
                taken.add(picked)
            result[name] = dict(rule=rule, table=tbl, reason=reason, picked=picked)
        return result
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="자산군별 추종 펀드 선정 (운용펀드만)")
    p.add_argument("--universe", required=True, help="펀드코드 CSV")
    p.add_argument("--base", default=None, help="기준일 (기본: 최근 월말)")
    p.add_argument("--show", type=int, default=5, help="자산군별 상위 N개 출력")
    p.add_argument("--only", action="append", help="특정 자산군만")
    p.add_argument("--allow-dup", action="store_true",
                   help="한 펀드가 여러 자산군에 중복 선정되는 것을 허용")
    a = p.parse_args()

    base = a.base or (pd.Timestamp.today() - pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d")
    codes = load_universe(a.universe)
    res = pick(codes, base, a.only, exclusive=not a.allow_dup)

    print(f"기준일 {base} · 유니버스 {len(codes):,}개 · 최근 {WEEKS}주\n")
    for name, r in res.items():
        rule, tbl = r["rule"], r["table"]
        undl = rule["undl"] or "—"
        cut = "—" if rule["corr_cut"] is None else f"{rule['corr_cut']:.2f}"
        lag = rule["lag"] if not isinstance(rule["lag"], list) else "/".join(map(str, rule["lag"]))
        print(f"■ {name}   지수 {undl} · LAG {lag} · corr>{cut} · {rule['rank']} 최대")
        if r["reason"]:
            print(f"   ⚠ {r['reason']}")
        if tbl.empty:
            print("   통과 펀드 없음\n")
            continue
        for i, (fid, row) in enumerate(tbl.head(a.show).iterrows()):
            mark = "★" if i == 0 else f"{i + 1}."
            c = "—" if pd.isna(row["corr"]) else f"{row['corr']:.3f}"
            lg = "" if pd.isna(row["lag"]) else f"L{int(row['lag'])}"
            g = "—" if pd.isna(row.get("risk_grade")) else f"{int(row['risk_grade'])}등급"
            print(f"   {mark:<2} corr {c:>6} {lg:<3} 수익 {row['return']:>7.2%} "
                  f"샤프 {row['sharpe']:>5.2f} 위험 {g:<5} {row['fund_code']}  {row['name'][:40]}")
        print()

    print("── 선정 결과 ──")
    for name, r in res.items():
        if r["picked"] is None:
            print(f"   {name:<6} —")
            continue
        row = r["table"].loc[r["picked"]]
        g = "—" if pd.isna(row.get("risk_grade")) else f"{int(row['risk_grade'])}등급"
        print(f"   {name:<6} {g:<5} {row['fund_code']}  {row['name'][:46]}")
