"""배분 #1 — 슬리브별 추종 펀드 매핑 시각화. 읽기 전용.

**계산은 `fund_picking.pick()` 을 그대로 부른다.** 리포트가 따로 계산하지 않으므로
표와 그림이 어긋나지 않는다(`cycle_switch_report.py` 와 같은 원칙).

이 화면이 답하려는 것은 "무엇이 뽑혔나"보다 **"왜 그게 뽑혔나"** 다. 선정 규칙은
상관 하한을 넘긴 것 중 수익률 1등을 고르는데, 그러다 보면 지수를 더 잘 따라가는
후보가 밀린다. 그래서 슬리브마다 후보 전체를 상관(가로) x 수익률(세로)로 흩뿌리고
컷 선과 선정 지점을 함께 그린다 — 표만 보면 안 보이는 맞바꿈이 그림에서는 보인다.

사용법:
  python analysis/allocation/fund_map_viz.py --universe <유니버스.csv>
  python analysis/allocation/fund_map_viz.py --universe u.csv --base 2026-07-31
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fund_picking import WEEKS, load_universe, pick  # noqa: E402

OUT = BACKEND.parent / "reference" / "analysis"

# 슬리브 이름은 코드값(US, KR30Y …)이라 그대로 두면 읽는 사람이 매번 번역해야 한다.
LABEL = {
    "US": ("미국 주식", "S&P500 추종"),
    "EAFE": ("선진국 주식 (ex-US)", "MSCI EAFE 추종"),
    "EM": ("신흥국 주식", "MSCI EM 추종"),
    "KR": ("국내 주식", "KOSPI200 TR 추종"),
    "GOLD": ("금", "금 현물 추종"),
    "KR30Y": ("국고 30년", "KIS 국고채30년 추종"),
    "KR10Y": ("국고 10년", "KIS 10년 국채 추종"),
    "MMF": ("초단기채", "CD 지수 수준 추종"),
    "LS": ("롱숏", "추종 지수 없음 — 샤프로 선정"),
}


def collect(codes: list[str], base: str) -> dict:
    funnel: dict = {}
    res = pick(codes, base, funnel=funnel)

    sleeves = []
    for key, r in res.items():
        rule, tbl = r["rule"], r["table"]
        funds = []
        for fid, row in tbl.iterrows():
            funds.append({
                "code": row["fund_code"], "name": row["name"], "company": row["company"],
                "corr": None if pd.isna(row["corr"]) else round(float(row["corr"]), 4),
                "lag": None if pd.isna(row["lag"]) else int(row["lag"]),
                "ret": round(float(row["return"]), 6),
                "sharpe": round(float(row["sharpe"]), 4),
                "vol": round(float(row["vol"]), 6),
                "grade": None if pd.isna(row.get("risk_grade")) else int(row["risk_grade"]),
                # numpy.bool_ 은 json 이 못 쓴다 — 파이썬 bool 로 내린다.
                "picked": bool(fid == r["picked"]),
            })
        # **선정 펀드보다 상관이 높은데 밀린 후보**. 수익률로 줄 세운 결과라 생기는
        # 맞바꿈이고, 이 숫자가 크면 그 슬리브의 대표 펀드는 추종력이 1등이 아니다.
        pk = next((f for f in funds if f["picked"]), None)
        better = sum(1 for f in funds
                     if pk and f["corr"] is not None and pk["corr"] is not None
                     and f["corr"] > pk["corr"]) if pk else 0
        sleeves.append({
            "key": key, "label": LABEL[key][0], "note": LABEL[key][1],
            "undl": rule["undl"], "cut": rule["corr_cut"], "rank": rule["rank"],
            "lag": rule["lag"] if not isinstance(rule["lag"], list)
                   else "/".join(map(str, rule["lag"])),
            "reason": r["reason"], "funds": funds, "better_corr": better,
        })
    return {"base": base, "weeks": WEEKS, "funnel": funnel, "sleeves": sleeves,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds")}


CSS = """
:root{
  --ground:#f3f6f7; --card:#ffffff; --ink:#141a21; --ink2:#39434e; --muted:#6b7684;
  --rule:#e2e8ea; --rule2:#eef2f3;
  --teal:#1d5b6b; --pick:#b4551d; --dot:#86b4ba; --cut:#a8332b;
  --good:#2c6e54; --alert:#a8332b; --warn:#8a6410;
  --goodbg:#e6f1eb; --alertbg:#fbeae8; --warnbg:#f9f1de; --chip:#eaf0f1;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#11161a; --card:#181f24; --ink:#e6ebee; --ink2:#c0c9d0; --muted:#8794a0;
  --rule:#28323a; --rule2:#212930;
  --teal:#6fb3c2; --pick:#dd8b4e; --dot:#4a8b96; --cut:#e0736a;
  --good:#63b48d; --alert:#e0736a; --warn:#d0a44e;
  --goodbg:#16291f; --alertbg:#2c1c1b; --warnbg:#2a2416; --chip:#1e262c;
}}
:root[data-theme="dark"]{
  --ground:#11161a; --card:#181f24; --ink:#e6ebee; --ink2:#c0c9d0; --muted:#8794a0;
  --rule:#28323a; --rule2:#212930;
  --teal:#6fb3c2; --pick:#dd8b4e; --dot:#4a8b96; --cut:#e0736a;
  --good:#63b48d; --alert:#e0736a; --warn:#d0a44e;
  --goodbg:#16291f; --alertbg:#2c1c1b; --warnbg:#2a2416; --chip:#1e262c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",
    "Malgun Gothic","Noto Sans KR",system-ui,sans-serif;
  font-size:15px;line-height:1.65;-webkit-font-smoothing:antialiased}
.num{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.wrap{max-width:1060px;margin:0 auto;padding:40px 22px 90px}
.up{color:var(--good)} .down{color:var(--alert)}

header{border-bottom:2px solid var(--teal);padding-bottom:18px;margin-bottom:28px}
.kicker{font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin:0 0 8px}
h1{font-size:clamp(25px,4vw,35px);line-height:1.12;margin:0 0 10px;letter-spacing:-.035em;
  font-weight:800;color:var(--teal);text-wrap:balance}
.sub{margin:0;color:var(--ink2);max-width:70ch}
.meta{display:flex;flex-wrap:wrap;gap:6px 22px;margin-top:14px;font-size:12.5px;color:var(--muted)}
.meta b{color:var(--ink2);font-weight:700}

h2{font-size:19px;margin:46px 0 6px;font-weight:800;letter-spacing:-.02em;color:var(--teal)}
.lede{margin:0 0 16px;color:var(--ink2);max-width:74ch;font-size:14px}

.funnel{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;margin:0 0 6px}
.fstep{flex:1 1 150px;background:var(--card);border:1px solid var(--rule);border-radius:9px;
  padding:11px 13px}
.fstep span{display:block;font-size:11.5px;color:var(--muted);margin-bottom:3px}
.fstep b{font-size:21px;font-weight:800;letter-spacing:-.03em}
.fstep em{font-style:normal;font-size:11.5px;color:var(--muted);margin-left:5px}

.picks{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.pick{background:var(--card);border:1px solid var(--rule);border-radius:11px;padding:14px 15px}
.pick .top{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
  border-bottom:1px solid var(--rule2);padding-bottom:8px;margin-bottom:9px}
.pick .cls{font-weight:800;color:var(--teal);letter-spacing:-.02em}
.pick .undl{font-size:11.5px;color:var(--muted)}
.pick .fname{font-weight:700;line-height:1.35;margin:0 0 2px;font-size:14px}
.pick .fcode{font-size:11.5px;color:var(--muted);margin:0 0 9px}
.pick dl{display:grid;grid-template-columns:repeat(4,1fr);gap:2px 8px;margin:0}
.pick dt{font-size:11px;color:var(--muted)}
.pick dd{margin:0;font-weight:700;font-size:14px}
.none{color:var(--muted);font-style:italic}

.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.chart{background:var(--card);border:1px solid var(--rule);border-radius:11px;padding:13px 14px 8px}
.chart h3{margin:0 0 1px;font-size:14px;font-weight:800;letter-spacing:-.02em}
.chart p{margin:0 0 8px;font-size:11.5px;color:var(--muted)}
canvas{width:100%;height:190px;display:block}

.legend{display:flex;flex-wrap:wrap;gap:5px 18px;font-size:12.5px;color:var(--muted);margin:10px 0 0}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;
  vertical-align:middle}

.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--rule);border-radius:11px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:7px 11px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--rule2)}
th{background:var(--rule2);font-size:11.5px;color:var(--muted);font-weight:700;
  position:sticky;top:0}
th:first-child,td:first-child{text-align:left}
td.nm{text-align:left;white-space:normal;min-width:230px}
tr.sel td{background:var(--warnbg);font-weight:700}
tr.head td{background:var(--chip);font-weight:800;color:var(--teal);text-align:left}

.note{background:var(--card);border:1px solid var(--rule);border-left:3px solid var(--warn);
  border-radius:9px;padding:14px 16px;margin:12px 0}
.note h3{margin:0 0 6px;font-size:14.5px;font-weight:800}
.note p{margin:0 0 8px;font-size:14px;color:var(--ink2)}
.note p:last-child{margin-bottom:0}
.pill{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11.5px;font-weight:700}
.pill.bad{background:var(--alertbg);color:var(--alert)}
.pill.ok{background:var(--goodbg);color:var(--good)}
footer{margin-top:46px;padding-top:14px;border-top:1px solid var(--rule);
  font-size:12px;color:var(--muted)}
"""

JS = """
const D = window.__DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function draw(cv, s){
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext('2d'); g.scale(dpr, dpr); g.clearRect(0,0,W,H);

  const pad = {l:38, r:10, t:8, b:22};
  const pts = s.funds.filter(f => f.corr !== null);
  // 추종 지수가 없는 슬리브는 상관 대신 변동성을 가로축에 쓴다.
  const useCorr = pts.length > 0;
  const xs = useCorr ? s.funds.map(f=>f.corr) : s.funds.map(f=>f.vol);
  const ys = s.funds.map(f=>f.ret);
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  if (useCorr && s.cut !== null) x0 = Math.min(x0, s.cut);
  // **0% 를 축에 강제로 끼우지 않는다.** 초단기채처럼 후보가 3.05~3.12% 로 몰린
  // 슬리브는 0 을 넣는 순간 점이 한 줄로 뭉개져 비교가 안 된다. 0 선은 실제로 범위
  // 안에 들어올 때만 그린다.
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const padx = (x1-x0)*0.15 || 0.02, pady = (y1-y0)*0.15 || 0.02;
  x0 -= padx; x1 += padx; const Y0 = y0 - pady, Y1 = y1 + pady;
  const X = v => pad.l + (v-x0)/(x1-x0) * (W-pad.l-pad.r);
  const Y = v => H - pad.b - (v-Y0)/(Y1-Y0) * (H-pad.t-pad.b);

  g.strokeStyle = css('--rule'); g.lineWidth = 1;
  g.fillStyle = css('--muted'); g.font = '10px ui-monospace,Menlo,monospace';
  g.textAlign = 'right'; g.textBaseline = 'middle';
  // 눈금 소수자리는 범위에 맞춘다 — 폭이 5%p 도 안 되는데 정수로 찍으면 눈금이
  // 전부 같은 숫자가 된다. -0.4% 가 '-0%' 로 보이는 것도 여기서 막는다.
  const span = (Y1-Y0)*100;
  const dec = span >= 5 ? 0 : span >= 0.5 ? 1 : 2;
  for (let i=0;i<=3;i++){
    const v = Y0 + (Y1-Y0)*i/3, y = Y(v);
    g.beginPath(); g.moveTo(pad.l, y); g.lineTo(W-pad.r, y); g.stroke();
    let t = (v*100).toFixed(dec); if (Math.abs(+t) < 1e-9) t = (0).toFixed(dec);
    g.fillText(t+'%', pad.l-5, y);
  }
  if (Y0 < 0 && Y1 > 0){                       // 0% 선은 굵게 — 손실 구간이 보이게
    g.strokeStyle = css('--ink2'); g.beginPath();
    g.moveTo(pad.l, Y(0)); g.lineTo(W-pad.r, Y(0)); g.stroke();
  }
  if (useCorr && s.cut !== null){               // 상관 하한
    g.strokeStyle = css('--cut'); g.setLineDash([4,3]); g.beginPath();
    g.moveTo(X(s.cut), pad.t); g.lineTo(X(s.cut), H-pad.b); g.stroke(); g.setLineDash([]);
    g.fillStyle = css('--cut'); g.textAlign = 'left';
    g.fillText('컷 '+s.cut.toFixed(2), X(s.cut)+4, pad.t+6);
  }
  g.fillStyle = css('--muted'); g.textAlign = 'center'; g.textBaseline = 'top';
  g.fillText(useCorr ? '상관' : '변동성', (pad.l+W-pad.r)/2, H-pad.b+6);

  s.funds.forEach(f => {
    const cx = X(useCorr ? f.corr : f.vol), cy = Y(f.ret);
    g.beginPath(); g.arc(cx, cy, f.picked ? 6 : 4, 0, Math.PI*2);
    g.fillStyle = f.picked ? css('--pick') : css('--dot');
    g.globalAlpha = f.picked ? 1 : .72; g.fill(); g.globalAlpha = 1;
    if (f.picked){ g.strokeStyle = css('--card'); g.lineWidth = 2; g.stroke(); }
  });
}

function drawAll(){ D.sleeves.forEach(s => {
  const cv = document.getElementById('cv-'+s.key); if (cv) draw(cv, s); }); }
drawAll();
addEventListener('resize', drawAll);
matchMedia('(prefers-color-scheme:dark)').addEventListener('change', drawAll);
"""


def pct(v, digits=2):
    return f'<span class="num {"up" if v >= 0 else "down"}">{v * 100:+.{digits}f}%</span>'


def render(D: dict) -> str:
    f = D["funnel"]
    steps = "".join(
        f'<div class="fstep"><span>{k}</span><b class="num">{v:,}</b>'
        f'<em>{"" if i == 0 else f"−{list(f.values())[i - 1] - v:,}"}</em></div>'
        for i, (k, v) in enumerate(f.items()))

    picks = []
    for s in D["sleeves"]:
        p = next((x for x in s["funds"] if x["picked"]), None)
        if p is None:
            picks.append(f'<article class="pick"><div class="top"><span class="cls">{s["label"]}</span>'
                         f'<span class="undl">{s["undl"] or "—"}</span></div>'
                         f'<p class="none">통과 펀드 없음</p></article>')
            continue
        corr = "—" if p["corr"] is None else f'{p["corr"]:.3f}'
        lag = "" if p["lag"] is None else f' <em style="color:var(--muted)">L{p["lag"]}</em>'
        grade = "—" if p["grade"] is None else f'{p["grade"]}등급'
        picks.append(f'''<article class="pick">
  <div class="top"><span class="cls">{s["label"]}</span>
    <span class="undl">{s["undl"] or "지수 없음"} · {s["note"]}</span></div>
  <p class="fname">{p["name"]}</p>
  <p class="fcode num">{p["code"]} · {p["company"]}</p>
  <dl><dt>상관</dt><dt>1년 수익</dt><dt>샤프</dt><dt>위험</dt>
    <dd class="num">{corr}{lag}</dd><dd>{pct(p["ret"])}</dd>
    <dd class="num">{p["sharpe"]:.2f}</dd><dd class="num">{grade}</dd></dl>
</article>''')

    charts = []
    for s in D["sleeves"]:
        cut = "상관 컷 없음" if s["cut"] is None else f"상관 컷 {s['cut']:.2f}"
        rank = "수익률" if s["rank"] == "return" else "샤프"
        charts.append(f'''<div class="chart"><h3>{s["label"]}</h3>
  <p>후보 {len(s["funds"])}개 · {cut} · {rank} 1등 선정</p>
  <canvas id="cv-{s["key"]}"></canvas></div>''')
    charts = "".join(charts)

    rows = []
    for s in D["sleeves"]:
        cut = "상관 컷 없음" if s["cut"] is None else f"corr>{s['cut']:.2f}"
        rows.append(f'<tr class="head"><td colspan="8">{s["label"]} · '
                    f'{s["undl"] or "지수 없음"} · LAG {s["lag"]} · {cut}</td></tr>')
        if not s["funds"]:
            rows.append('<tr><td colspan="8" class="none">통과 펀드 없음</td></tr>')
        for i, p in enumerate(s["funds"][:6]):
            corr = "—" if p["corr"] is None else f'{p["corr"]:.3f}'
            lag = "" if p["lag"] is None else f'L{p["lag"]}'
            grade = "—" if p["grade"] is None else f'{p["grade"]}'
            rows.append(
                f'<tr class="{"sel" if p["picked"] else ""}">'
                f'<td class="nm">{"★ " if p["picked"] else f"{i + 1}. "}{p["name"]}</td>'
                f'<td class="num">{p["code"]}</td><td class="num">{corr}</td>'
                f'<td class="num">{lag}</td><td>{pct(p["ret"])}</td>'
                f'<td class="num">{p["sharpe"]:.2f}</td>'
                f'<td class="num">{p["vol"] * 100:.1f}%</td><td class="num">{grade}</td></tr>')

    # 짚어둘 것 — 수익률 1등이 추종력 1등은 아니다
    tradeoffs = [s for s in D["sleeves"] if s["better_corr"] > 0]
    if tradeoffs:
        items = "".join(
            f'<li><b>{s["label"]}</b> — 선정 펀드보다 상관이 높은 후보가 '
            f'<span class="pill bad">{s["better_corr"]}개</span> 있다 '
            f'(최고 {max(x["corr"] for x in s["funds"] if x["corr"] is not None):.3f} vs '
            f'선정 {next(x["corr"] for x in s["funds"] if x["picked"]):.3f})</li>'
            for s in tradeoffs)
        trade = f'''<div class="note"><h3>수익률 1등이 추종력 1등은 아니다</h3>
  <p>선정 규칙은 <b>상관 하한을 넘긴 것 중 최근 1년 수익률이 가장 높은 것</b>을 고른다.
  하한만 넘으면 그 위로는 상관을 더 보지 않으므로, 지수를 더 잘 따라가는 후보가 밀린다.</p>
  <ul>{items}</ul>
  <p>슬리브의 목적이 <b>지수 대체</b>라면 이건 손해다. 위 산점도에서 주황 점이 오른쪽 끝에
  있지 않은 슬리브가 그 경우다. 반대로 목적이 <b>그 자산군에서 잘하는 펀드 찾기</b>라면
  의도대로 동작한 것이다. 어느 쪽인지는 전략이 정할 문제라 여기서는 드러내기만 한다.</p></div>'''
    else:
        trade = ""

    payload = json.dumps(D, ensure_ascii=False)
    return f"""<title>사이클 스위치 펀드 매핑</title>
<style>{CSS}</style>
<div class="wrap">
<header>
  <p class="kicker">Allocation #1 · Fund Mapping</p>
  <h1>사이클 스위치 펀드 매핑</h1>
  <p class="sub">전략이 요구하는 자산군마다 그것을 대신할 공모펀드를 하나씩 고른 결과다.
  지수를 살 수 없으니 펀드로 바꿔 끼우는 단계이고, 얼마나 잘 대신하는지는 <b>기준가가
  참조 지수를 따라간 정도</b>로 잰다.</p>
  <div class="meta">
    <span>기준일 <b>{D["base"]}</b></span>
    <span>관측 <b>최근 {D["weeks"]}주</b></span>
    <span>자산군 <b>{len(D["sleeves"])}개</b></span>
    <span>운용펀드만 · 자산군 간 중복 선정 없음</span>
  </div>
</header>

<h2>유니버스가 어떻게 좁혀졌나</h2>
<p class="lede">모든 자산군이 이 마지막 단계의 펀드 풀을 공유한다. 여기서 자산군별 규칙
(분류·지역·이름·상관)이 추가로 걸린다.</p>
<div class="funnel">{steps}</div>

<h2>자산군별 선정 결과</h2>
<p class="lede">상관은 주간 수익률 기준이고 <span class="num">L</span> 은 적용된 시차(영업일)다.
국내 공모펀드의 기준가는 해외 시장을 며칠 늦게 반영하므로 시차를 맞춰야 추종력이 제대로 나온다.</p>
<div class="picks">{"".join(picks)}</div>

<h2>후보는 어디에 흩어져 있나</h2>
<p class="lede">가로가 참조 지수와의 상관, 세로가 최근 1년 수익률이다. 빨간 점선이 상관 하한이고
주황 점이 선정된 펀드다. <b>주황 점이 오른쪽 끝이 아니면</b> 더 잘 따라가는 후보를 두고
수익률이 높은 쪽을 고른 것이다.</p>
<div class="grid">{charts}</div>
<p class="legend"><span><i style="background:var(--pick)"></i>선정</span>
  <span><i style="background:var(--dot)"></i>통과 후보</span>
  <span><i style="background:var(--cut)"></i>상관 하한</span>
  <span>롱숏은 참조 지수가 없어 가로축이 변동성이다</span></p>

{trade}

<h2>슬리브별 후보 (상위 6)</h2>
<p class="lede">상관 하한을 통과한 펀드만 나온다. 굵게 칠해진 줄이 선정된 펀드다.</p>
<div class="scroll"><table>
<thead><tr><th>펀드</th><th>코드</th><th>상관</th><th>시차</th><th>1년 수익</th>
  <th>샤프</th><th>변동성</th><th>위험등급</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>

<footer>계산 <span class="num">analysis/allocation/fund_picking.py</span> ·
  생성 <span class="num">{D["generated_at"].replace("T", " ")}</span> ·
  수익률·상관은 최근 {D["weeks"]}주 수정기준가 기준이며 위험등급은 DART 투자설명서 값이다.</footer>
</div>
<script>window.__DATA__ = {payload};</script>
<script>{JS}</script>
"""


def main(universe: str, base: str) -> None:
    D = collect(load_universe(universe), base)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fund_map.json").write_text(json.dumps(D, ensure_ascii=False, indent=1))
    (OUT / "fund_map.html").write_text(render(D))

    print(f"기준일 {D['base']} · " + " → ".join(f"{k} {v:,}" for k, v in D["funnel"].items()))
    for s in D["sleeves"]:
        p = next((x for x in s["funds"] if x["picked"]), None)
        flag = f"  (상관 더 높은 후보 {s['better_corr']}개)" if s["better_corr"] else ""
        print(f"  {s['label']:<16} 후보 {len(s['funds']):>3}개  "
              f"{p['name'][:34] if p else '—'}{flag}")
    print(f"\n{OUT / 'fund_map.html'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--base", default=None)
    a = ap.parse_args()
    main(a.universe,
         a.base or (pd.Timestamp.today() - pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d"))
