"""섹터 수급모멘텀(3개월 누적) -> 다음달 섹터 수익률.

직전 분석에서 '최상위 섹터의 직전 1개월 수급'은 선행력이 전혀 없었다(t -1.2~+1.6).
누적기간이 짧아 노이즈에 묻혔을 수 있으므로 3개월(60거래일)로 늘려 다시 본다.

신호: 섹터 구성종목 순매수 합 / 같은 기간 섹터 거래대금 합 (3개월 누적)
대상: 다음달(20거래일) 섹터 동일가중 수익률
형성일: 월말 -> forward 1개월이라 구간이 겹치지 않는다(독립 관측치, t-stat 보정 불필요)

섹터 수가 15~23개라 5분위는 거칠어서 IC와 함께 상위3/하위3 섹터 스프레드를 본다.
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
FLOW_MONTHS = 3          # 수급 누적기간
MIN_MEMBERS = 5
TOPK = 3
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
inst = pd.read_sql(text(UNIV + """select i.id, i.sector, i.industry, i.krx_sector
 from instruments i join u on u.iid=i.id"""), db.bind).set_index("id")

mem = pd.read_sql(text("""select as_of_date,instrument_id from index_memberships
 where index_name in ('KOSDAQ150','KOSPI200')"""), db.bind)
mem["as_of_date"] = pd.to_datetime(mem["as_of_date"])
snaps = sorted(mem["as_of_date"].unique())
snap_members = {s: set(mem.loc[mem["as_of_date"] == s, "instrument_id"]) for s in snaps}

month_end = adj.groupby([adj.index.year, adj.index.month]).apply(lambda g: g.index[-1])
months = [pd.Timestamp(x) for x in month_end.values]
print(f"월 {len(months)}개")


def universe_at(d):
    el = [s for s in snaps if s <= d]
    return snap_members[max(el)] if el else set()


def spearman(a, b):
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    return float(np.corrcoef(ra, rb)[0, 1]) if np.std(ra) > 0 and np.std(rb) > 0 else np.nan


ic_rec: dict[tuple, list] = {}
sp_rec: dict[tuple, list] = {}
detail = []

for field, flabel in FIELDS.items():
    for mi in range(FLOW_MONTHS, len(months) - 1):
        d_sig_start = months[mi - FLOW_MONTHS]   # 수급 누적 시작
        d_form = months[mi]                       # 형성일(월말)
        d_next = months[mi + 1]                   # 다음달 말

        cols = [c for c in adj.columns if c in universe_at(d_form)]
        if len(cols) < 50:
            continue
        groups: dict[str, list] = {}
        for c in cols:
            v = inst.at[c, field] if c in inst.index else None
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                groups.setdefault(v, []).append(c)
        groups = {k: v for k, v in groups.items() if len(v) >= MIN_MEMBERS}
        if len(groups) < 8:
            continue

        fwd_win = (adj.index > d_form) & (adj.index <= d_next)
        sig_win = (adj.index > d_sig_start) & (adj.index <= d_form)

        fwd = {}
        for k, v in groups.items():
            r = (adj.loc[d_next, v] / adj.loc[d_form, v] - 1)
            if r.notna().sum() >= MIN_MEMBERS:
                fwd[k] = r.mean()
        if len(fwd) < 8:
            continue

        for inv in INVESTORS:
            sig = {}
            for k, v in groups.items():
                if k not in fwd:
                    continue
                num = flow[inv].loc[sig_win, v].sum().sum()
                den = amt.loc[sig_win, v].sum().sum()
                if den and not np.isnan(den):
                    sig[k] = num / den
            common = sorted(set(sig) & set(fwd))
            if len(common) < 8:
                continue
            s = [sig[k] for k in common]
            f = [fwd[k] for k in common]
            ic = spearman(s, f)
            if np.isnan(ic):
                continue
            ic_rec.setdefault((flabel, inv), []).append(ic)
            order = np.argsort(-np.asarray(s))
            top = np.mean([f[i] for i in order[:TOPK]])
            bot = np.mean([f[i] for i in order[-TOPK:]])
            sp_rec.setdefault((flabel, inv), []).append((top, bot, np.mean(f)))
            if flabel == "팩셋섹터":
                detail.append(dict(month=str(d_form.date())[:7], investor=inv,
                                   top_sector=common[order[0]], top3_ret=top * 100,
                                   bot3_ret=bot * 100, univ_ret=np.mean(f) * 100))
    print(f"  {flabel} 완료")

print("\n" + "=" * 100)
print(f"섹터 수급모멘텀({FLOW_MONTHS}개월 누적) -> 다음달 섹터 수익률")
print("  IC>0 = 수급 많이 받은 섹터가 다음달 강세 / IC<0 = 역지표")
rows = []
for (flabel, inv), ics in sorted(ic_rec.items()):
    v = np.array(ics)
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    sp = sp_rec[(flabel, inv)]
    top = np.array([x[0] for x in sp]) * 100
    bot = np.array([x[1] for x in sp]) * 100
    uni = np.array([x[2] for x in sp]) * 100
    spread = top - bot
    ts = spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))
    te = (top - uni).mean() / ((top - uni).std(ddof=1) / np.sqrt(len(top)))
    rows.append(dict(분류=flabel, 주체=inv, n=len(v), mean_IC=v.mean(), IC_t=t,
                     상위3_초과=(top - uni).mean(), 상위3_t=te,
                     하위3_초과=(bot - uni).mean(),
                     스프레드=spread.mean(), 스프레드_t=ts))
res = pd.DataFrame(rows)
pd.set_option("display.width", 220)
pd.set_option("display.float_format", lambda x: f"{x:.3f}")
print(res.to_string(index=False))

print("\n주체별 요약 (세 분류 평균 IC t)")
for inv in INVESTORS:
    sub = res[res.주체 == inv]
    print(f"  {inv:8s} IC t 평균 {sub.IC_t.mean():+.2f}  "
          f"(범위 {sub.IC_t.min():+.2f} ~ {sub.IC_t.max():+.2f}) | "
          f"상위3 초과 평균 {sub.상위3_초과.mean():+.3f}%p")

res.to_csv(OUT_DIR / "섹터수급모멘텀_익월수익률.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(detail).to_csv(OUT_DIR / "섹터수급모멘텀_월별상세.csv", index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT_DIR}/섹터수급모멘텀_익월수익률.csv, _월별상세.csv")
db.close()
