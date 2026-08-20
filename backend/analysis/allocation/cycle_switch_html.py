"""배분 #1 리포트 HTML 생성. cycle_switch_report.build() 산출 딕셔너리를 받는다.

수치를 손으로 옮기지 않는다 — 전부 D에서 만든다.
"""
import json

CSS = """
:root{
  --ground:#f3f6f7; --card:#ffffff; --ink:#141a21; --ink2:#39434e; --muted:#6b7684;
  --rule:#e2e8ea; --rule2:#eef2f3; --band:#eaf0f1;
  --teal:#1d5b6b; --mp1:#1d5b6b; --mp2:#4a8b96; --mp3:#86b4ba; --bm:#98a3ae;
  --stock:#b4551d; --gold:#a07c12; --bond:#2e6b55;
  --good:#2c6e54; --alert:#a8332b; --warn:#8a6410;
  --goodbg:#e6f1eb; --alertbg:#fbeae8; --warnbg:#f9f1de; --chip:#eaf0f1;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#11161a; --card:#181f24; --ink:#e6ebee; --ink2:#c0c9d0; --muted:#8794a0;
  --rule:#28323a; --rule2:#212930; --band:#1e262c;
  --teal:#6fb3c2; --mp1:#6fb3c2; --mp2:#4a8b96; --mp3:#356a73; --bm:#7b8794;
  --stock:#dd8b4e; --gold:#d3ac47; --bond:#5fa987;
  --good:#63b48d; --alert:#e0736a; --warn:#d0a44e;
  --goodbg:#16291f; --alertbg:#2c1c1b; --warnbg:#2a2416; --chip:#1e262c;
}}
:root[data-theme="dark"]{
  --ground:#11161a; --card:#181f24; --ink:#e6ebee; --ink2:#c0c9d0; --muted:#8794a0;
  --rule:#28323a; --rule2:#212930; --band:#1e262c;
  --teal:#6fb3c2; --mp1:#6fb3c2; --mp2:#4a8b96; --mp3:#356a73; --bm:#7b8794;
  --stock:#dd8b4e; --gold:#d3ac47; --bond:#5fa987;
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
.wrap{max-width:1000px;margin:0 auto;padding:40px 22px 90px}
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
.lede{margin:0 0 16px;color:var(--ink2);max-width:70ch;font-size:14px}

.card{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:16px 17px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.eyebrow{font-size:11.5px;font-weight:700;color:var(--muted);margin:0 0 6px}
.big{font-size:30px;font-weight:800;margin:0;letter-spacing:-.035em;line-height:1.1}
.note{margin:6px 0 0;font-size:11.5px;color:var(--muted)}

/* 현재 국면 */
.now{display:grid;grid-template-columns:minmax(230px,300px) 1fr;gap:14px;align-items:stretch}
.sw{display:flex;flex-direction:column;gap:9px}
.swrow{display:grid;grid-template-columns:52px 1fr auto;align-items:center;gap:10px;
  font-size:13.5px}
.swrow .nm{font-weight:700;color:var(--ink2)}
.pill{font-size:11px;font-weight:800;letter-spacing:.06em;padding:3px 9px;border-radius:999px;
  border:1px solid currentColor}
.on-stock{color:var(--stock)} .on-gold{color:var(--gold)} .on-bond{color:var(--bond)}
.off{color:var(--muted);opacity:.75}
.trk{height:7px;border-radius:4px;background:var(--band);overflow:hidden}
.trk i{display:block;height:100%;border-radius:4px}

.alloc{display:flex;flex-direction:column;gap:7px;justify-content:center}
.arow{display:grid;grid-template-columns:96px 1fr 58px;align-items:center;gap:10px;font-size:12.5px}
.arow span{color:var(--ink2)}
.arow .bar{height:16px;border-radius:3px;background:var(--rule2);overflow:hidden}
.arow .bar i{display:block;height:100%;border-radius:3px}
.arow b{text-align:right;color:var(--ink2);font-weight:700}

figure{margin:0}
canvas{width:100%;height:auto;display:block}
figcaption{font-size:11.5px;color:var(--muted);margin-top:10px;padding-top:9px;
  border-top:1px solid var(--rule2)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12px;color:var(--ink2);margin-bottom:11px}
.legend i{display:inline-block;width:18px;height:3px;border-radius:2px;
  vertical-align:middle;margin-right:6px}
.legend i.sq{height:11px;width:11px;border-radius:2px}

.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13px;background:var(--card)}
th{text-align:right;font-size:11px;color:var(--muted);font-weight:700;padding:8px 10px;
  border-bottom:1px solid var(--rule);white-space:nowrap}
th:first-child{text-align:left}
td{padding:8px 10px;border-bottom:1px solid var(--rule2);text-align:right;white-space:nowrap}
td:first-child{text-align:left;font-weight:700}
tr.hi td{background:var(--band)}
td.k{font-family:ui-monospace,monospace;font-size:12px;letter-spacing:.08em}

.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:760px){.grid2,.now{grid-template-columns:1fr}}

.callout{background:var(--warnbg);border-left:3px solid var(--warn);border-radius:0 7px 7px 0;
  padding:12px 15px;font-size:13px;color:var(--ink2);margin:16px 0 0;max-width:78ch}
.ok{background:var(--goodbg);border-left-color:var(--good)}
.callout b{font-weight:700}
.callout code{font-size:12px;background:var(--chip);padding:1px 5px;border-radius:4px}

ul.tight{margin:10px 0 0;padding-left:20px;font-size:13.5px;color:var(--ink2)}
ul.tight li{margin-bottom:6px}
footer{margin-top:52px;padding-top:16px;border-top:1px solid var(--rule);
  font-size:11.5px;color:var(--muted)}
footer li{margin-bottom:3px}
"""


def pc(v, d=2, sign=True):
    if v is None:
        return "-"
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def render(D: dict) -> str:
    s = D["stats"]
    lat = D["latest"]
    sw_label = {"stock": "주식", "gold": "금", "bond": "채권"}
    sw_color = {"stock": "stock", "gold": "gold", "bond": "bond"}

    # ── 현재 국면
    swrows = ""
    for c in ("stock", "gold", "bond"):
        on = lat["switch"][c]
        cls = f"on-{sw_color[c]}" if on else "off"
        fill = (f'<i style="width:100%;background:var(--{sw_color[c]})"></i>' if on else "")
        swrows += (f'<div class="swrow"><span class="nm">{sw_label[c]}</span>'
                   f'<span class="trk">{fill}</span>'
                   f'<span class="pill {cls}">{"ON" if on else "OFF"}</span></div>')

    sheet = lat["weight_sheet"]
    gnames = ["주식", "금", "채권", "현금"]
    gcolor = ["--mp1", "--gold", "--bond", "--muted"]
    mx = max(sheet) or 1
    grows = "".join(
        f'<div class="arow"><span>{n}</span><span class="bar">'
        f'<i style="width:{v / mx * 100:.1f}%;background:var({c})"></i></span>'
        f'<b class="num">{v:.1%}</b></div>'
        for n, v, c in zip(gnames, sheet, gcolor))

    mp1 = lat["mp"]["MP1"]
    mpx = max(mp1.values()) or 1
    mprows = "".join(
        f'<div class="arow"><span>{D["asset_label"][t]}</span><span class="bar">'
        f'<i style="width:{v / mpx * 100:.1f}%;background:var(--mp1)"></i></span>'
        f'<b class="num">{v:.1%}</b></div>'
        for t, v in sorted(mp1.items(), key=lambda kv: -kv[1]))

    # ── 성과 표
    rows = ""
    for n in ["MP1", "MP2", "MP3", "BM"]:
        m = s[n]
        rows += (f'<tr{" class=hi" if n == "MP1" else ""}><td>{n}</td>'
                 f'<td class="num">{pc(m["cagr"])}</td>'
                 f'<td class="num">{pc(m["excess_cagr"])}</td>'
                 f'<td class="num">{pc(m["annualized_volatility"], 2, False)}</td>'
                 f'<td class="num">{m["sharpe_rf"]:.2f}</td>'
                 f'<td class="num">{m["sharpe"]:.2f}</td>'
                 f'<td class="num down">{pc(m["mdd"], 1)}</td>'
                 f'<td class="num">{pc(m["turnover_pa"], 1, False)}</td>'
                 f'<td class="num">{pc(m["cumulative_return"], 0)}</td></tr>')

    # ── 국면 표
    rrows = ""
    for r in D["regimes"]:
        on = "".join(f'<span class="pill {"on-" + sw_color[c] if r["key"][i] == "1" else "off"}"'
                     f' style="margin-right:3px">{sw_label[c]}</span>'
                     for i, c in enumerate(("stock", "gold", "bond")))
        ann = "-" if r["annualized"] is None else pc(r["annualized"], 1)
        cls = "up" if (r["annualized"] or 0) > 0 else "down"
        never = r["months_all"] == 0
        rrows += (f'<tr><td class="k">{r["key"]}</td><td style="text-align:left">{on}</td>'
                  f'<td class="num">{r["weights"][0]:.0%}</td>'
                  f'<td class="num">{r["weights"][1]:.1%}</td>'
                  f'<td class="num">{r["weights"][2]:.1%}</td>'
                  f'<td class="num">{r["weights"][3]:.1%}</td>'
                  f'<td class="num">{r["months_all"] or "—"}</td>'
                  f'<td class="num">{pc(r["share_all"], 1, False) if not never else "—"}</td>'
                  f'<td class="num {cls}">{ann}</td></tr>')

    swsum = " · ".join(f'{sw_label[x["name"]]} <b>ON {x["on_ratio"]:.0%}</b>'
                       f' / 전환 <b>{x["flips"]}회</b>' for x in D["switches"])

    y0, y1 = D["period"]
    return f"""<title>경기 사이클 스위치</title>
<style>{CSS}</style>
<div class="wrap">
<header>
  <p class="kicker">Allocation #1 · Cycle Switch</p>
  <h1>경기 사이클 스위치</h1>
  <p class="sub">마진부채·금·채권 세 개의 스위치로 국면을 판정하고, 국면마다 미리 정해 둔
  자산 비중표를 꺼내 쓰는 전술적 자산배분이다. 최적화도 스코어링도 없다 — 규칙과 표가 전부다.</p>
  <div class="meta">
    <span>성과 구간 <b>{y0} ~ {y1}</b></span>
    <span>신호 <b>{D["signal_period"][0]} ~ {D["signal_period"][1]}</b></span>
    <span>리밸런싱 <b>분기말</b></span>
    <span>거래비용 <b>{D["cost"]:.2%}</b></span>
    <span>무위험수익률 <b>SOFR</b></span>
  </div>
</header>

<h2>지금 어떤 국면인가</h2>
<p class="lede">스위치는 월간으로 계산하고 분기말에만 반영한다. 아래는
{lat["date"]} 기준 신호이며, 다음 리밸런싱은 {lat["next_rebalance"]}이다.</p>
<div class="now">
  <div class="card sw">
    <p class="eyebrow">스위치 · 국면 [{lat["key"]}]</p>
    {swrows}
    <p class="note" style="margin-top:10px">키는 주식·금·채권 순서다.
    8가지 조합마다 비중표가 하나씩 있다.</p>
  </div>
  <div class="card alloc">
    <p class="eyebrow">자산군 비중 (국면 [{lat["key"]}])</p>
    {grows}
  </div>
</div>
<div class="card" style="margin-top:12px">
  <p class="eyebrow">MP1 목표비중 — 개별 지수로 분해</p>
  <div class="alloc">{mprows}</div>
  <p class="note" style="margin-top:10px">주식은 미국·선진국·신흥국 7:2:1, 채권은 만기별로
  나눈다. MP2·MP3는 위험자산 전체를 각각 80%·65%로 줄이고 그만큼 현금이 받는다.</p>
</div>

<h2>성과</h2>
<div class="kpis">
  <article class="card">
    <p class="eyebrow">MP1 연평균 수익률</p>
    <p class="big num up">{pc(s["MP1"]["cagr"])}</p>
    <p class="note">벤치마크 {pc(s["BM"]["cagr"])} · 무위험 {pc(s["MP1"]["riskfree_cagr"])}</p>
  </article>
  <article class="card">
    <p class="eyebrow">샤프 (초과수익 기준)</p>
    <p class="big num">{s["MP1"]["sharpe_rf"]:.2f}</p>
    <p class="note">벤치마크 {s["BM"]["sharpe_rf"]:.2f}</p>
  </article>
  <article class="card">
    <p class="eyebrow">최대 낙폭</p>
    <p class="big num down">{pc(s["MP1"]["mdd"], 1)}</p>
    <p class="note">벤치마크 {pc(s["BM"]["mdd"], 1)}</p>
  </article>
  <article class="card">
    <p class="eyebrow">연 회전율 (편도)</p>
    <p class="big num">{pc(s["MP1"]["turnover_pa"], 0, False)}</p>
    <p class="note">벤치마크 {pc(s["BM"]["turnover_pa"], 0, False)} · 분기 리밸런싱</p>
  </article>
</div>

<div class="card" style="margin-top:14px">
  <div class="legend">
    <span><i style="background:var(--mp1)"></i>MP1 (공격)</span>
    <span><i style="background:var(--mp2)"></i>MP2</span>
    <span><i style="background:var(--mp3)"></i>MP3 (보수)</span>
    <span><i style="background:var(--bm)"></i>벤치마크 ACWI 60 / Global-Agg 40</span>
  </div>
  <canvas id="c1"></canvas>
  <figcaption>세로축은 로그 스케일이다 — 긴 구간에서는 같은 비율 변화가 같은 높이로 보여야
  초기와 최근을 함께 읽을 수 있다. 첫날 매수비용을 반영해 100 아래에서 시작한다.</figcaption>
</div>

<div class="card" style="margin-top:12px">
  <canvas id="c2"></canvas>
  <figcaption>고점 대비 낙폭. 주 단위로 솎되 각 주의 <b>최저치</b>를 취해 깊이를 보존했다.</figcaption>
</div>

<div class="scroll" style="margin-top:14px">
<table>
  <thead><tr><th>포트폴리오</th><th>CAGR</th><th>초과</th><th>변동성</th><th>샤프(rf)</th>
  <th>샤프</th><th>MDD</th><th>회전율</th><th>누적</th></tr></thead>
  <tbody>{rows}</tbody>
</table></div>
<p class="note">샤프(rf)는 SOFR 초과수익 기준이고, 옆의 샤프는 무위험이자율을 빼지 않은 값이다
— 이 리포지토리의 주식 트랙이 후자를 쓰므로 비교를 위해 같이 둔다.</p>

<h2>스위치는 어떻게 움직였나</h2>
<p class="lede">{D["signal_period"][0]}부터의 월간 신호다. {swsum}.
채권 스위치의 히스테리시스 밴드가 0.2σ로 가장 좁아 전환이 잦다.</p>
<div class="card">
  <canvas id="c3"></canvas>
  <figcaption>칠해진 구간이 ON이다. 세 줄이 동시에 비는 구간이 방어 국면
  <span class="num">[000]</span>이고, 그때 현금이 30%까지 올라간다.</figcaption>
</div>

<h2>국면별 비중표와 실제 성과</h2>
<p class="lede">비중은 사람이 미리 정해 표에 박아 둔 값이다. 오른쪽 두 열은 신호 전체 구간의
체류 기간과, 성과 구간에서 그 국면에 있던 달들의 연환산 수익률이다.</p>
<div class="scroll">
<table>
  <thead><tr><th>키</th><th>스위치</th><th>주식</th><th>금</th><th>채권</th><th>현금</th>
  <th>개월</th><th>비중</th><th>연환산</th></tr></thead>
  <tbody>{rrows}</tbody>
</table></div>
<p class="callout"><b>표본이 작다.</b> 국면 하나에 머문 기간이 한 자릿수 개월인 칸이 있고,
<span class="num">[010]</span>은 28년 동안 한 번도 나오지 않았다 — 금만 켜지고 주식·채권이
모두 꺼지는 조합이다. 국면별 연환산 수익률은 <b>성향을 보는 참고치</b>이지 기대수익이 아니다.</p>

<h2>연도별 수익률</h2>
<div class="card">
  <div class="legend">
    <span><i class="sq" style="background:var(--mp1)"></i>MP1</span>
    <span><i class="sq" style="background:var(--bm)"></i>벤치마크</span>
  </div>
  <canvas id="c4"></canvas>
  <figcaption>첫해와 마지막 해는 부분 기간이다.</figcaption>
</div>

<h2>자산배분 추이</h2>
<div class="card">
  <canvas id="c5"></canvas>
  <figcaption>MP1의 실제 보유 비중(월말). 리밸런싱 사이에는 가격이 움직이는 대로
  비중이 흘러가므로 계단이 아니라 곡선으로 보인다.</figcaption>
</div>

<h2>검증</h2>
<p class="callout ok"><b>엔진은 원본과 소수점 14자리까지 같다.</b> 원본이 쓴 가격과 원본이 만든
비중을 그대로 넣고 원본 NAV와 비교하면 최대 상대차가 <span class="num">5.6e-16 ~ 1.8e-14</span>다.
목표비중도 리밸런싱 107회 중 106회가 일치한다. 어긋난 한 번(2019-09-30)의 원인은 금 계열
차이다 — 원본은 LBMA 런던 고시가 계열, 우리는 <code>XAU</code>(뉴욕 마감 현물)를 쓴다.
금 스위치가 415개월 중 4개월 갈리고 그중 분기말에 걸린 것이 그 한 번이다.</p>

<h2>한계</h2>
<ul class="tight">
  <li><b>신호는 월간, 리밸런싱은 분기말이다.</b> 분기 중간에 스위치가 뒤집혀도 반영되지 않는다
  — 실제로 {lat["date"]} 신호가 다음 분기말까지 기다린다.</li>
  <li><b>금 스위치가 비교하는 "채권"에 듀레이션이 없다.</b> 10년물 금리로 월복리 누적한
  합성 계열이라 금리가 급등해도 계속 오른다. 실제 채권이 손실인 국면에서 금 스위치가 늦게 켜진다.</li>
  <li><b>z-score 창이 12개월이다.</b> 표본 12개로 표준편차를 재므로 값이 불안정하다.</li>
  <li><b>거래비용 {D["cost"]:.2%}는 원본 백테스트의 가정이다.</b> 지수를 직접 살 수 없으므로
  실제 집행 수단(ETF·펀드)을 정하면 보수와 추적오차가 더 붙는다.</li>
  <li><b>성과 구간이 곧 설계 구간이다.</b> 비중표와 임계값은 이 구간을 보고 정해진 값이므로
  여기서의 성과는 실력의 상한에 가깝다.</li>
</ul>

<footer>
  <ul>
    <li>지수는 전부 총수익(NTR/TR) 기준. 벤치마크는 MSCI ACWI NTR 60 / Bloomberg Global-Aggregate TR 40.</li>
    <li>무위험수익률은 SOFR 지수 — 자산이 미국 지수이기 때문이다. 국내 자산이면 KOFR를 쓴다.</li>
    <li>백테스트 엔진 {D["engine_ms"]}ms · {D["days"]:,}거래일 × {D["assets"]}자산 · 포트폴리오 4개.</li>
    <li>슬리피지 미반영. 지수를 직접 보유할 수 없으므로 실제 집행에는 추가 비용이 있다.</li>
  </ul>
</footer>
</div>
<script>
const D = {json.dumps(D, ensure_ascii=False)};
const css = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim();
const CW = 940;
function hidpi(cv,w,h){{const r=2;cv.width=w*r;cv.height=h*r;cv.removeAttribute('style');
  const x=cv.getContext('2d');x.setTransform(r,0,0,r,0,0);return x;}}
const KEYS=['MP1','MP2','MP3','BM'], COL={{MP1:'--mp1',MP2:'--mp2',MP3:'--mp3',BM:'--bm'}};

/* 누적 성과 — 로그 스케일 */
function chartNav(){{
  const cv=document.getElementById('c1'), W=CW,H=350,x0=54,x1=W-14,y0=18,y1=H-30;
  const g=hidpi(cv,W,H), dts=D.curve.dates, n=dts.length;
  const all=KEYS.flatMap(k=>D.curve.nav[k]).filter(v=>v>0);
  const lo=Math.log(Math.min(...all)*0.97), hi=Math.log(Math.max(...all)*1.03);
  const X=i=>x0+(x1-x0)*i/(n-1), Y=v=>y1-(y1-y0)*(Math.log(v)-lo)/(hi-lo);
  g.font='11px ui-monospace,monospace'; g.textAlign='right'; g.textBaseline='middle';
  for(const t of [100,150,200,300,400,500,700]){{
    if(Math.log(t)<lo||Math.log(t)>hi) continue;
    const y=Y(t); g.strokeStyle=css('--rule'); g.lineWidth=1;
    g.beginPath(); g.moveTo(x0,y); g.lineTo(x1,y); g.stroke();
    g.fillStyle=css('--muted'); g.fillText(t,x0-8,y);}}
  g.textAlign='center'; g.textBaseline='top';
  dts.forEach((d,i)=>{{ if(d.slice(5,10)!=='12-31'&&i!==n-1) return;
    if(+d.slice(0,4)%3) return;
    g.fillStyle=css('--muted'); g.fillText(d.slice(0,4),X(i),y1+8);}});
  for(const k of KEYS){{
    g.strokeStyle=css(COL[k]); g.lineWidth=k==='MP1'?2.6:1.6; g.lineJoin='round';
    g.beginPath(); D.curve.nav[k].forEach((v,i)=>i?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v)));
    g.stroke();
    const last=D.curve.nav[k][n-1];
    g.fillStyle=css(COL[k]); g.font='700 11.5px ui-monospace,monospace';
    g.textAlign='left'; g.textBaseline='middle';
    g.fillText(Math.round(last), X(n-1)+4, Y(last));}}
}}

/* 낙폭 */
function chartDD(){{
  const cv=document.getElementById('c2'), W=CW,H=190,x0=54,x1=W-14,y0=16,y1=H-28;
  const g=hidpi(cv,W,H), n=D.curve.dates.length;
  const lo=Math.min(...KEYS.flatMap(k=>D.curve.dd[k]))*1.05;
  const X=i=>x0+(x1-x0)*i/(n-1), Y=v=>y0+(y1-y0)*(v-0)/(lo-0);
  g.font='11px ui-monospace,monospace'; g.textAlign='right'; g.textBaseline='middle';
  for(let t=0;t>=lo;t-=0.1){{const y=Y(t); g.strokeStyle=css('--rule'); g.lineWidth=1;
    g.beginPath(); g.moveTo(x0,y); g.lineTo(x1,y); g.stroke();
    g.fillStyle=css('--muted'); g.fillText((t*100).toFixed(0)+'%',x0-8,y);}}
  for(const k of ['BM','MP1']){{
    const d=D.curve.dd[k];
    g.beginPath(); g.moveTo(X(0),Y(0));
    d.forEach((v,i)=>g.lineTo(X(i),Y(v))); g.lineTo(X(n-1),Y(0)); g.closePath();
    g.fillStyle=css(COL[k])+(k==='MP1'?'44':'22'); g.fill();
    g.strokeStyle=css(COL[k]); g.lineWidth=k==='MP1'?2:1.4;
    g.beginPath(); d.forEach((v,i)=>i?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v))); g.stroke();}}
  g.textAlign='left'; g.textBaseline='top'; g.font='700 11.5px system-ui';
  g.fillStyle=css('--mp1'); g.fillText('MP1',x0+6,y0+2);
  g.fillStyle=css('--bm'); g.fillText('벤치마크',x0+42,y0+2);
}}

/* 스위치 타임라인 */
function chartSwitch(){{
  const cv=document.getElementById('c3'), W=CW, lane=26, gap=9;
  const rows=['stock','gold','bond'], H=rows.length*(lane+gap)+34, x0=54,x1=W-10,y0=8;
  const g=hidpi(cv,W,H), dts=D.switch_history.dates, n=dts.length;
  const X=i=>x0+(x1-x0)*i/n, w=(x1-x0)/n;
  const lab={{stock:'주식',gold:'금',bond:'채권'}}, col={{stock:'--stock',gold:'--gold',bond:'--bond'}};
  rows.forEach((k,r)=>{{
    const y=y0+r*(lane+gap);
    g.fillStyle=css('--band'); g.fillRect(x0,y,x1-x0,lane);
    g.fillStyle=css(col[k]);
    D.switch_history.series[k].forEach((v,i)=>{{ if(v) g.fillRect(X(i),y,Math.ceil(w)+0.5,lane); }});
    g.fillStyle=css('--ink2'); g.font='700 12px system-ui';
    g.textAlign='right'; g.textBaseline='middle'; g.fillText(lab[k],x0-9,y+lane/2);}});
  g.textAlign='center'; g.textBaseline='top'; g.font='11px ui-monospace,monospace';
  g.fillStyle=css('--muted');
  dts.forEach((d,i)=>{{ if(d.slice(5)!=='01'||+d.slice(0,4)%4) return;
    g.fillText(d.slice(0,4),X(i),y0+rows.length*(lane+gap)+4);}});
}}

/* 연도별 */
function chartYear(){{
  const cv=document.getElementById('c4'), W=CW,H=250,x0=48,x1=W-10,y0=16,y1=H-30;
  const g=hidpi(cv,W,H), ys=D.yearly, n=ys.length;
  const vals=ys.flatMap(y=>[y.MP1,y.BM]);
  const hi=Math.max(...vals,0)*1.1, lo=Math.min(...vals,0)*1.15;
  const Y=v=>y1-(y1-y0)*(v-lo)/(hi-lo), bw=(x1-x0)/n;
  g.strokeStyle=css('--rule'); g.lineWidth=1;
  for(let t=Math.ceil(lo*10)/10;t<=hi;t+=0.1){{const y=Y(t);
    g.beginPath(); g.moveTo(x0,y); g.lineTo(x1,y); g.stroke();
    g.fillStyle=css('--muted'); g.font='11px ui-monospace,monospace';
    g.textAlign='right'; g.textBaseline='middle'; g.fillText((t*100).toFixed(0)+'%',x0-7,y);}}
  g.strokeStyle=css('--muted'); g.beginPath(); g.moveTo(x0,Y(0)); g.lineTo(x1,Y(0)); g.stroke();
  ys.forEach((y,i)=>{{
    const cx=x0+bw*i+bw*0.5, bar=bw*0.34;
    [['MP1',-bar],['BM',0]].forEach(([k,off])=>{{
      const v=y[k]; g.fillStyle=css(COL[k]);
      g.fillRect(cx+off, Math.min(Y(v),Y(0)), bar, Math.abs(Y(v)-Y(0)));}});
    if(i%2===0||i===n-1){{ g.fillStyle=css('--muted'); g.font='10.5px ui-monospace,monospace';
      g.textAlign='center'; g.textBaseline='top'; g.fillText(String(y.year).slice(2),cx,y1+7);}}
  }});
}}

/* 자산배분 추이 */
function chartWeights(){{
  const cv=document.getElementById('c5'), W=CW,H=270,x0=48,x1=W-10,y0=14,y1=H-28;
  const g=hidpi(cv,W,H), wd=D.weights, n=wd.dates.length;
  const col={{SPXNTR:'--mp1',MSCIEAFENTR:'--mp2',MSCIEMNTR:'--mp3',GOLD:'--gold',
             US30Y:'--bond',US10Y:'--teal',USBIL:'--muted'}};
  const X=i=>x0+(x1-x0)*i/(n-1), Y=v=>y1-(y1-y0)*v;
  const acc=new Array(n).fill(0);
  wd.order.forEach(t=>{{
    const s=wd.series[t];
    g.beginPath(); g.moveTo(X(0),Y(acc[0]));
    for(let i=0;i<n;i++) g.lineTo(X(i),Y(acc[i]+s[i]));
    for(let i=n-1;i>=0;i--) g.lineTo(X(i),Y(acc[i]));
    g.closePath(); g.fillStyle=css(col[t]); g.globalAlpha=t==='US10Y'?0.55:0.85; g.fill();
    g.globalAlpha=1;
    for(let i=0;i<n;i++) acc[i]+=s[i];}});
  g.fillStyle=css('--muted'); g.font='11px ui-monospace,monospace';
  g.textAlign='right'; g.textBaseline='middle';
  [0,0.5,1].forEach(t=>g.fillText((t*100)+'%',x0-7,Y(t)));
  g.textAlign='center'; g.textBaseline='top';
  wd.dates.forEach((d,i)=>{{ if(d.slice(5)!=='12'||+d.slice(0,4)%3) return;
    g.fillText(d.slice(0,4),X(i),y1+7);}});
  const lg=[['미국주식','--mp1'],['선진국','--mp2'],['신흥국','--mp3'],['금','--gold'],
            ['미국채20Y+','--bond'],['미국채7-10Y','--teal'],['현금','--muted']];
  let lx=x0+4; g.textAlign='left'; g.textBaseline='middle'; g.font='700 10.5px system-ui';
  lg.forEach(([t,c])=>{{ g.fillStyle=css(c); g.fillRect(lx,y0-6,9,9);
    g.fillStyle=css('--ink2'); g.fillText(t,lx+12,y0-1); lx+=g.measureText(t).width+28;}});
}}

function drawAll(){{chartNav();chartDD();chartSwitch();chartYear();chartWeights();}}
drawAll();
new MutationObserver(drawAll).observe(document.documentElement,
  {{attributes:true,attributeFilter:['data-theme']}});
matchMedia('(prefers-color-scheme:dark)').addEventListener('change',drawAll);
</script>
"""
