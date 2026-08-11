"""월별 최상위 섹터의 투자자별 수급 통계.

매월 섹터별 동일가중 수익률을 구해 그 달 1등 섹터를 뽑고, 그 섹터에 대한
기관/외국인/개인 순매수를 두 시점으로 본다.
  선행(직전달)  : 다음달 1등이 될 섹터를 미리 사는 주체가 있는가
  동시대(해당달): 오르는 동안 누가 사고 누가 팔았는가

수급은 "섹터 구성종목 순매수 합계 / 같은 기간 섹터 거래대금 합계"로 정규화한다
(단독 신호검증에서 유일하게 견고했던 정규화 방식).

비교 기준으로 '그 달 전체 섹터 평균 수급'도 같이 계산해, 1등 섹터의 수급이
평범한 섹터와 실제로 다른지(초과분)를 본다.

세 분류필드(팩셋섹터=sector / 팩셋산업=industry / 케이산업=krx_sector) 모두 산출.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

START = "2019-01-01"
FIELDS = {"sector": "팩셋섹터", "industry": "팩셋산업", "krx_sector": "케이산업"}
INVESTORS = ["기관합계", "외국인", "개인"]
MIN_MEMBERS = 5
OUT_DIR = REPO_DIR / "reference"

db = SessionLocal()
UNIV = """
with u as (select distinct instrument_id iid from index_memberships
           where index_name in ('KOSDAQ150','KOSPI200'))
"""


def pivot(sql, params):
    df = pd.read_sql(text(sql), db.bind, params=params)
    p = df.pivot(index="date", columns="instrument_id", values="value").sort_index()
    p.index = pd.to_datetime(p.index)
    return p


print("데이터 로딩 중...")
adj = pivot(UNIV + """select d.date,d.instrument_id,d.adj_close::float value
 from dividend_adjusted_prices d join u on u.iid=d.instrument_id
 where d.period='D' and d.date>=:s""", {"s": START})
amt = pivot(UNIV + """select s.date,s.instrument_id,s.total_value::float value from short_selling s
 join u on u.iid=s.instrument_id where s.date>=:s""", {"s": START}).reindex_like(adj)
flow = {inv: pivot(UNIV + """select it.date,it.instrument_id,it.net_value::float value
 from investor_trading it join u on u.iid=it.instrument_id
 where it.date>=:s and it.investor_type=:i""", {"s": START, "i": inv}).reindex_like(adj)
        for inv in INVESTORS}

inst = pd.read_sql(text(UNIV + """select i.id, i.name, i.sector, i.industry, i.krx_sector
 from instruments i join u on u.iid=i.id"""), db.bind).set_index("id")

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

# 월말 인덱스 (각 달의 마지막 거래일)
month_end = adj.groupby([adj.index.year, adj.index.month]).apply(lambda g: g.index[-1])
months = list(month_end.values)
print(f"월 {len(months)}개 ({pd.Timestamp(months[0]).date()} ~ {pd.Timestamp(months[-1]).date()})")


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


records = []
for field, flabel in FIELDS.items():
    for mi in range(1, len(months)):
        d_prev, d_cur = pd.Timestamp(months[mi - 1]), pd.Timestamp(months[mi])
        cols = [c for c in adj.columns if c in universe_at(d_cur)]
        if len(cols) < 50:
            continue

        # 그 달 종목 수익률
        ret = (adj.loc[d_cur, cols] / adj.loc[d_prev, cols] - 1)
        groups: dict[str, list] = {}
        for c in cols:
            v = inst.at[c, field] if c in inst.index else None
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                groups.setdefault(v, []).append(c)
        groups = {k: v for k, v in groups.items() if len(v) >= MIN_MEMBERS}
        if len(groups) < 5:
            continue

        sec_ret = {k: ret[v].mean() for k, v in groups.items() if ret[v].notna().sum() >= MIN_MEMBERS}
        if not sec_ret:
            continue
        top = max(sec_ret, key=sec_ret.get)

        # 기간별 수급비율: 섹터 구성종목 순매수합 / 거래대금합
        def flow_ratio(members, a, b, inv):
            win = (adj.index > a) & (adj.index <= b)
            num = flow[inv].loc[win, members].sum().sum()
            den = amt.loc[win, members].sum().sum()
            return num / den if den and not np.isnan(den) else np.nan

        d_prev2 = pd.Timestamp(months[mi - 2]) if mi >= 2 else None
        row = dict(field=flabel, month=str(d_cur.date())[:7], top_sector=top,
                   n_members=len(groups[top]), sector_ret_pct=sec_ret[top] * 100,
                   univ_ret_pct=ret.mean() * 100, n_sectors=len(sec_ret))
        for inv in INVESTORS:
            cur = flow_ratio(groups[top], d_prev, d_cur, inv)
            others = [flow_ratio(v, d_prev, d_cur, inv) for k, v in groups.items() if k != top]
            row[f"{inv}_동시대"] = cur * 100
            row[f"{inv}_동시대_초과"] = (cur - np.nanmean(others)) * 100
            if d_prev2 is not None:
                lead = flow_ratio(groups[top], d_prev2, d_prev, inv)
                lead_o = [flow_ratio(v, d_prev2, d_prev, inv) for k, v in groups.items() if k != top]
                row[f"{inv}_선행"] = lead * 100
                row[f"{inv}_선행_초과"] = (lead - np.nanmean(lead_o)) * 100
        records.append(row)
    print(f"  {flabel} 완료")

df = pd.DataFrame(records)
pd.set_option("display.width", 240)
pd.set_option("display.float_format", lambda x: f"{x:.2f}")

print("\n" + "=" * 104)
print("최상위 섹터의 수급비율 (순매수/거래대금, %) — '초과'는 그 달 나머지 섹터 평균 대비")
for flabel in FIELDS.values():
    sub = df[df.field == flabel]
    print(f"\n[{flabel}]  월 {len(sub)}개, 평균 섹터수 {sub.n_sectors.mean():.1f}, "
          f"1등섹터 평균수익률 {sub.sector_ret_pct.mean():+.2f}% (유니버스 {sub.univ_ret_pct.mean():+.2f}%)")
    print(f"    {'주체':8s} {'선행 초과':>10s} {'t':>6s} | {'동시대 초과':>11s} {'t':>6s}")
    for inv in INVESTORS:
        out = []
        for tag in ["선행", "동시대"]:
            col = f"{inv}_{tag}_초과"
            v = sub[col].dropna().values
            t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 else np.nan
            out.append((v.mean(), t))
        print(f"    {inv:8s} {out[0][0]:+9.3f}%p {out[0][1]:+6.2f} | "
              f"{out[1][0]:+10.3f}%p {out[1][1]:+6.2f}")

print("\n\n=== 팩셋섹터 기준 최근 12개월 상세")
recent = df[df.field == "팩셋섹터"].tail(12)
show = ["month", "top_sector", "sector_ret_pct", "기관합계_동시대", "외국인_동시대", "개인_동시대"]
print(recent[show].to_string(index=False))

print("\n=== 1등 섹터로 가장 자주 뽑힌 섹터 (팩셋섹터)")
print(df[df.field == "팩셋섹터"].top_sector.value_counts().head(8).to_string())

df.to_csv(OUT_DIR / "월별최상위섹터_수급통계.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/월별최상위섹터_수급통계.csv ({len(df)}행)")
db.close()
