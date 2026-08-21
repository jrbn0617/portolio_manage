"""알고리즘 #1 월별 성과 이력 시각화 — 마감된 달만. 읽기 전용.

계산은 `monthly_perf.py`가 하고(캐시 포함) 여기는 그리기만 한다. 새로 마감된 달이
있으면 이 스크립트를 돌리는 것만으로 계산까지 이어진다.

**월별 수익률을 곱해 누적을 만든다.** 전략이 월 1회 전량 리밸런싱이라 달과 달 사이에
포지션이 이어지지 않으므로, 달을 독립 사건으로 보고 복리로 쌓는 것이 실제 운용과 같다.

홀드아웃 — 2020-01 부터만 나온다. 경계는 `monthly_perf.HOLDOUT_START` 에 있다.

사용법:
  python analysis/algorithm1/monthly_viz.py
  python analysis/algorithm1/monthly_viz.py --rebuild   # 월별 계산부터 다시
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from monthly_perf import load  # noqa: E402

SP = Path(os.environ.get("ALGO_OUT",
                         Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)

p = argparse.ArgumentParser(description="알고리즘 #1 월별 성과 이력")
p.add_argument("--rebuild", action="store_true", help="월별 계산을 전부 다시")
months = load(p.parse_args().rebuild)
if not months:
    raise SystemExit("마감된 달이 없습니다.")

# ── 집계 ──────────────────────────────────────────────────────────────────
# 누적은 월 수익률의 곱이다. 벤치마크가 빠진 달은 그 달만 건너뛰지 않고 **비교 자체를
# 하지 않는다** — 한쪽만 이어 붙이면 누적 격차가 그 달만큼 조용히 틀어진다.
nav, bmnav, v, b = [], [], 1.0, 1.0
for m in months:
    v *= 1 + m["mp"]
    nav.append(v - 1)
    if m["bm"] is not None:
        b *= 1 + m["bm"]
    bmnav.append(b - 1)

paired = [m for m in months if m["bm"] is not None]
wins = [m for m in months if m["mp"] > 0]
beat = [m for m in paired if m["excess"] > 0]
n_years = len(months) / 12
cagr = (1 + nav[-1]) ** (1 / n_years) - 1 if n_years else 0.0
bm_cagr = (1 + bmnav[-1]) ** (1 / n_years) - 1 if n_years else 0.0

# 월별 낙폭 — 일별이 아니라 월말 기준이라 실제 MDD 보다 얕다. 그림에 그대로 적는다.
peak, dd = 1.0, []
for x in nav:
    peak = max(peak, 1 + x)
    dd.append((1 + x) / peak - 1)

years: dict[str, dict] = {}
for m in months:
    y = years.setdefault(m["ym"][:4], dict(mp=1.0, bm=1.0, n=0, bm_n=0))
    y["mp"] *= 1 + m["mp"]
    y["n"] += 1
    if m["bm"] is not None:
        y["bm"] *= 1 + m["bm"]
        y["bm_n"] += 1

D = dict(
    first=months[0]["ym"], last=months[-1]["ym"], n=len(months),
    cum=nav[-1], bm_cum=bmnav[-1], cagr=cagr, bm_cagr=bm_cagr,
    win_rate=len(wins) / len(months), beat_rate=len(beat) / len(paired) if paired else None,
    best=max(months, key=lambda m: m["mp"]),
    worst=min(months, key=lambda m: m["mp"]),
    mdd=min(dd), n_stopped=sum(m["n_stopped"] for m in months),
    stop_months=sum(1 for m in months if m["n_stopped"]),
    ym=[m["ym"] for m in months], nav=nav, bmnav=bmnav, dd=dd,
    mp=[m["mp"] for m in months], bm=[m["bm"] for m in months],
    years=[dict(y=k, mp=x["mp"] - 1, bm=(x["bm"] - 1) if x["bm_n"] == x["n"] else None,
                n=x["n"]) for k, x in sorted(years.items())],
    rows=months,
)
(SP / "monthly_viz.json").write_text(json.dumps(D, ensure_ascii=False, indent=1))


def pc(x, d=2, sign=True):
    if x is None:
        return "—"
    return f"{x*100:+.{d}f}%" if sign else f"{x*100:.{d}f}%"


def ymk(s):
    return f"{s[:4]}년 {int(s[5:7])}월"


kpi = [
    ("누적 수익률", pc(D["cum"], 1), f"{ymk(D['first'])} → {ymk(D['last'])} · {D['n']}개월",
     "up" if D["cum"] > 0 else "down"),
    ("연평균 (CAGR)", pc(D["cagr"], 2), f"코스피 총수익 {pc(D['bm_cagr'], 2)}",
     "up" if D["cagr"] > 0 else "down"),
    ("월 승률", pc(D["win_rate"], 0, False),
     f"코스피 초과 {pc(D['beat_rate'], 0, False)}" if D["beat_rate"] is not None else "—", ""),
    ("월말 기준 최대 낙폭", pc(D["mdd"], 1), "일별이 아니라 월말 기준이라 실제보다 얕다", "down"),
]

kpi_html = "".join(f'''
  <article class="kpi">
    <p class="eyebrow">{t}</p>
    <p class="big num {c}">{v}</p>
    <p class="note">{n}</p>
  </article>''' for t, v, n, c in kpi)

yr_html = "".join(f'''
  <tr>
    <td class="nm">{y['y']}</td>
    <td class="num {'up' if y['mp'] > 0 else 'down'}">{pc(y['mp'], 1)}</td>
    <td class="num">{pc(y['bm'], 1)}</td>
    <td class="num {'up' if y['bm'] is not None and y['mp'] > y['bm'] else 'down'}">
      {pc(y['mp'] - y['bm'], 1) if y['bm'] is not None else '—'}</td>
    <td class="num muted">{y['n']}개월</td>
  </tr>''' for y in D["years"])

rows_html = "".join(f'''
  <tr class="{'stopped' if m['n_stopped'] else ''}">
    <td class="nm">{m['ym']}</td>
    <td class="num muted">{m['n_days']}</td>
    <td class="num muted">{m['n_stocks']}</td>
    <td class="num {'up' if m['mp'] > 0 else 'down'}">{pc(m['mp'])}</td>
    <td class="num">{pc(m['bm'])}</td>
    <td class="num {'up' if m['excess'] is not None and m['excess'] > 0 else 'down'}">{pc(m['excess'])}</td>
    <td class="num muted">{pc(m['mp_nostop'])}</td>
    <td class="tag">{('손절 ' + str(m['n_stopped']) + '건') if m['n_stopped'] else ''}</td>
  </tr>''' for m in reversed(D["rows"]))

CSS = """
:root{
  --ground:#f5f7fa; --card:#ffffff; --ink:#151920; --ink2:#39414e; --muted:#6a7382;
  --navy:#1e3a5f; --good:#2c6e54; --alert:#a8332b;
  --rule:#e3e7ec; --rule2:#eef1f5; --alertbg:#fbeae8;
  --line:#1e3a5f; --bm:#a8641f; --ddfill:rgba(168,51,43,.16);
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#13161c; --card:#1a1e26; --ink:#e7eaef; --ink2:#c2c9d4; --muted:#8b95a4;
  --navy:#8fb4dd; --good:#63b48d; --alert:#e0736a;
  --rule:#2a303a; --rule2:#232831; --alertbg:#2e1e1d;
  --line:#8fb4dd; --bm:#d99a55; --ddfill:rgba(224,115,106,.18);
}}
:root[data-theme="dark"]{
  --ground:#13161c; --card:#1a1e26; --ink:#e7eaef; --ink2:#c2c9d4; --muted:#8b95a4;
  --navy:#8fb4dd; --good:#63b48d; --alert:#e0736a;
  --rule:#2a303a; --rule2:#232831; --alertbg:#2e1e1d;
  --line:#8fb4dd; --bm:#d99a55; --ddfill:rgba(224,115,106,.18);
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",
    "Malgun Gothic","Noto Sans KR",system-ui,sans-serif;
  font-size:15px;line-height:1.65;-webkit-font-smoothing:antialiased}
.num{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.wrap{max-width:940px;margin:0 auto;padding:40px 22px 80px}
.up{color:var(--good)} .down{color:var(--alert)} .muted{color:var(--muted)}
header{border-bottom:2px solid var(--navy);padding-bottom:18px;margin-bottom:26px}
.kicker{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin:0 0 8px}
h1{font-size:clamp(24px,4vw,33px);line-height:1.15;margin:0 0 10px;letter-spacing:-.03em;
  font-weight:800;color:var(--navy);text-wrap:balance}
.sub{margin:0;color:var(--ink2);max-width:64ch}
h2{font-size:18px;margin:42px 0 6px;font-weight:800;letter-spacing:-.02em;color:var(--navy)}
h2 .n{font-size:12px;color:var(--muted);font-weight:700;margin-right:9px;
  font-family:ui-monospace,monospace}
.lede{margin:0 0 16px;color:var(--ink2);max-width:66ch;font-size:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
.kpi{background:var(--card);border:1px solid var(--rule);border-radius:9px;padding:14px 15px}
.eyebrow{font-size:11.5px;font-weight:700;color:var(--muted);margin:0 0 6px}
.big{font-size:30px;font-weight:800;margin:0;letter-spacing:-.035em;line-height:1.1}
.note{margin:6px 0 0;font-size:11.5px;color:var(--muted)}
figure{margin:0;background:var(--card);border:1px solid var(--rule);border-radius:9px;
  padding:16px 16px 12px}
canvas{width:100%;height:auto;display:block}
figcaption{font-size:11.5px;color:var(--muted);margin-top:10px;padding-top:9px;
  border-top:1px solid var(--rule2)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12px;color:var(--ink2);margin-bottom:10px}
.legend i{display:inline-block;width:18px;height:3px;border-radius:2px;
  vertical-align:middle;margin-right:6px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13px;background:var(--card)}
th{text-align:right;font-size:11px;color:var(--muted);font-weight:700;padding:8px 10px;
  border-bottom:1px solid var(--rule);white-space:nowrap}
th:first-child{text-align:left}
td{padding:7px 10px;border-bottom:1px solid var(--rule2);text-align:right;white-space:nowrap}
td.nm,td.tag{text-align:left}
td.nm{font-weight:700;font-family:ui-monospace,monospace}
td.tag{font-size:11px;color:var(--alert);font-weight:700}
tr.stopped{background:var(--alertbg)}
.warn{background:var(--alertbg);border-left:3px solid var(--alert);border-radius:0 6px 6px 0;
  padding:11px 14px;font-size:12.5px;color:var(--ink2);margin:16px 0 0;max-width:72ch}
footer{margin-top:46px;padding-top:16px;border-top:1px solid var(--rule);
  font-size:11.5px;color:var(--muted)}
footer li{margin-bottom:3px}
"""

JS = r"""
const D = __DATA__;
const css = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim();
const CW = 880;
function hidpi(cv,w,h){const r=2;cv.width=w*r;cv.height=h*r;cv.removeAttribute('style');
  const x=cv.getContext('2d');x.setTransform(r,0,0,r,0,0);return x;}
const fmt = v => (v>=0?'+':'') + (v*100).toFixed(0) + '%';

// **로그 축이다.** 6년 반에 9배가 되면 선형 축에서는 초반 4년이 바닥에 눌려 아무것도
// 안 보인다. 같은 비율의 상승이 같은 높이가 되도록 log(1+r) 로 그리고, 눈금은 사람이
// 읽는 누적수익률(+50%, +100%, +200% …)에 찍는다.
const TICKS = [0, .5, 1, 2, 4, 8, 16, 32];
function cumChart(){
  const cv=document.getElementById('c1'), W=CW, H=340, x0=56,x1=W-16,y0=20,y1=H-46;
  const g=hidpi(cv,W,H);
  const all=[...D.nav,...D.bmnav,0];
  const L=v=>Math.log(1+v);
  let lo=L(Math.min(...all)), hi=L(Math.max(...all));
  const pad=(hi-lo)*0.08||0.01; lo-=pad; hi+=pad;
  const X=i=>x0+(x1-x0)*i/(D.ym.length-1), Y=v=>y1-(y1-y0)*(L(v)-lo)/(hi-lo);

  g.font='11px ui-monospace,monospace'; g.textAlign='right'; g.textBaseline='middle';
  TICKS.filter(t=>L(t)>=lo&&L(t)<=hi).forEach(t=>{ const y=Y(t);
    g.strokeStyle=t===0?css('--muted'):css('--rule'); g.lineWidth=t===0?1.2:1;
    g.beginPath(); g.moveTo(x0,y); g.lineTo(x1,y); g.stroke();
    g.fillStyle=css('--muted'); g.fillText((t>0?'+':'')+(t*100).toFixed(0)+'%',x0-8,y);});

  // 연 경계에만 눈금 — 79개월치 라벨을 다 찍으면 읽을 수 없다
  g.textAlign='center'; g.textBaseline='top'; g.fillStyle=css('--muted');
  D.ym.forEach((s,i)=>{ if(s.slice(5)==='01'||i===0) g.fillText(s.slice(0,4),X(i),y1+8); });

  const draw=(arr,color,width)=>{
    g.save(); g.strokeStyle=color; g.lineWidth=width; g.lineJoin='round';
    g.beginPath(); let st=false;
    arr.forEach((v,i)=>{ if(v==null) return; st?g.lineTo(X(i),Y(v)):(g.moveTo(X(i),Y(v)),st=true); });
    g.stroke(); g.restore();};
  draw(D.bmnav, css('--bm'), 2);
  draw(D.nav,   css('--line'), 2.8);

  const tip=(arr,color)=>{ const i=arr.length-1;
    g.fillStyle=color; g.beginPath(); g.arc(X(i),Y(arr[i]),3.5,0,7); g.fill();
    g.font='700 12px ui-monospace,monospace'; g.textAlign='right'; g.textBaseline='bottom';
    g.fillText(fmt(arr[i]),X(i)-9,Y(arr[i])-9);};
  tip(D.bmnav,css('--bm')); tip(D.nav,css('--line'));
}

function monthBars(){
  const cv=document.getElementById('c2'), W=CW, H=230, x0=48,x1=W-14,y0=16,y1=H-38;
  const g=hidpi(cv,W,H);
  const m=Math.max(...D.mp.map(Math.abs))*1.06||0.01;
  const Y=v=>((y0+y1)/2)-((y1-y0)/2)*v/m;
  const bw=Math.max(2,(x1-x0)/D.mp.length-1.6);
  g.strokeStyle=css('--rule'); g.lineWidth=1;
  g.beginPath(); g.moveTo(x0,Y(0)); g.lineTo(x1,Y(0)); g.stroke();
  g.font='11px ui-monospace,monospace'; g.textAlign='right'; g.textBaseline='middle';
  [m/2,0,-m/2].forEach(v=>{ g.fillStyle=css('--muted');
    g.fillText((v*100).toFixed(0)+'%',x0-8,Y(v)); });
  D.mp.forEach((v,i)=>{
    const x=x0+(x1-x0)*i/D.mp.length, y=Y(Math.max(v,0));
    g.fillStyle = v>=0?css('--navy'):css('--alert');
    g.fillRect(x, y, bw, Math.abs(Y(v)-Y(0)));});
  g.textAlign='center'; g.textBaseline='top'; g.fillStyle=css('--muted');
  D.ym.forEach((s,i)=>{ if(s.slice(5)==='01')
    g.fillText(s.slice(0,4), x0+(x1-x0)*i/D.mp.length+bw/2, y1+10); });
}

function ddChart(){
  const cv=document.getElementById('c3'), W=CW, H=170, x0=56,x1=W-16,y0=16,y1=H-34;
  const g=hidpi(cv,W,H);
  const lo=Math.min(...D.dd)*1.08||-0.01;
  const X=i=>x0+(x1-x0)*i/(D.dd.length-1), Y=v=>y0+(y1-y0)*(v-0)/(lo-0);
  g.font='11px ui-monospace,monospace'; g.textAlign='right'; g.textBaseline='middle';
  for(let k=0;k<=3;k++){const v=lo/3*k, y=Y(v);
    g.strokeStyle=css('--rule'); g.lineWidth=1;
    g.beginPath(); g.moveTo(x0,y); g.lineTo(x1,y); g.stroke();
    g.fillStyle=css('--muted'); g.fillText((v*100).toFixed(0)+'%',x0-8,y);}
  g.beginPath(); g.moveTo(X(0),Y(0));
  D.dd.forEach((v,i)=>g.lineTo(X(i),Y(v)));
  g.lineTo(X(D.dd.length-1),Y(0)); g.closePath();
  g.fillStyle=css('--ddfill'); g.fill();
  g.strokeStyle=css('--alert'); g.lineWidth=1.6;
  g.beginPath(); D.dd.forEach((v,i)=>i?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v))); g.stroke();
  g.textAlign='center'; g.textBaseline='top'; g.fillStyle=css('--muted');
  D.ym.forEach((s,i)=>{ if(s.slice(5)==='01') g.fillText(s.slice(0,4),X(i),y1+8); });
}
cumChart(); monthBars(); ddChart();
"""

HTML = f"""<title>알고리즘 #1 · 월별 성과 이력</title>
<style>{CSS}</style>
<div class="wrap">
<header>
  <p class="kicker">Algorithm #1 · Monthly track record</p>
  <h1>월별 성과 이력</h1>
  <p class="sub">달마다 직전 월말에 형성한 포트폴리오를 그 달 마지막 거래일까지 들고 간 결과다.
  <b>마감된 달만</b> 넣는다 — 진행 중인 달은 월중 리포트가 담당한다.
  모든 수치는 배당을 반영한 총수익 기준이다.</p>
</header>

<div class="kpis">{kpi_html}</div>

<h2><span class="n">01</span>누적 수익률</h2>
<p class="lede">월 수익률을 곱해 쌓은 것이다. 월 1회 전량 리밸런싱이라 달과 달 사이에
포지션이 이어지지 않으므로, 달을 독립 사건으로 보고 복리로 쌓는 것이 실제 운용과 같다.</p>
<figure>
  <div class="legend">
    <span><i style="background:var(--line)"></i>알고리즘 #1</span>
    <span><i style="background:var(--bm)"></i>코스피 총수익</span>
  </div>
  <canvas id="c1"></canvas>
  <figcaption>{ymk(D['first'])}부터 {D['n']}개월. 세로축은 <b>로그 축</b>이라 같은 비율의 상승이 같은 높이다 — 초반 구간이 눌리지 않는다. 가로 눈금은 연 경계다.</figcaption>
</figure>

<h2><span class="n">02</span>월별 수익률</h2>
<p class="lede">한 달 한 달이 어떻게 끝났는지다. 누적 곡선이 감추는 변동 폭이 여기서 보인다.</p>
<figure>
  <canvas id="c2"></canvas>
  <figcaption>{len([m for m in months if m['mp'] > 0])}개월 상승 · {len(months) - len([m for m in months if m['mp'] > 0])}개월 하락.
  최고 {D['best']['ym']} {pc(D['best']['mp'], 1)} · 최저 {D['worst']['ym']} {pc(D['worst']['mp'], 1)}.</figcaption>
</figure>

<h2><span class="n">03</span>낙폭</h2>
<p class="lede">직전 고점 대비 얼마나 내려와 있었나. <b>월말 기준</b>이라 달 안에서 더 깊이
내려간 구간은 잡히지 않는다 — 일별 낙폭보다 얕게 나온다는 뜻이다.</p>
<figure>
  <canvas id="c3"></canvas>
  <figcaption>월말 기준 최대 낙폭 {pc(D['mdd'], 1)}.</figcaption>
</figure>

<h2><span class="n">04</span>연도별</h2>
<div class="scroll"><table>
  <thead><tr><th>연도</th><th>알고리즘 #1</th><th>코스피 총수익</th><th>초과</th><th>구간</th></tr></thead>
  <tbody>{yr_html}</tbody>
</table></div>

<h2><span class="n">05</span>월별 상세</h2>
<p class="lede">최근 달이 위다. 붉은 줄은 그달에 손실 제한이 발동한 달이다.
&lsquo;미적용&rsquo;은 손실 제한을 걸지 않았다면 나왔을 수익률이다.</p>
<div class="scroll"><table>
  <thead><tr><th>연월</th><th>거래일</th><th>종목</th><th>수익률</th><th>코스피</th>
  <th>초과</th><th>미적용</th><th></th></tr></thead>
  <tbody>{rows_html}</tbody>
</table></div>

<p class="warn"><b>홀드아웃.</b> {ymk(D['first'])} 이전 구간은 표본 외 검증용으로 봉인돼 있어
이 표에 넣지 않는다. 그 구간의 성과는 개발 문서
(<code>docs/algorithms/algorithm1-experiments.md</code>)에만 있다.</p>

<footer>
  <ul>
    <li>손실 제한 발동 {D['stop_months']}개월 · 누적 {D['n_stopped']}건.</li>
    <li>거래비용은 각 달 형성 시점에 반영돼 있고, 슬리피지는 반영하지 않았다.</li>
    <li>재현: <code>venv/bin/python analysis/algorithm1/monthly_viz.py</code>
      (계산 캐시 <code>monthly_perf.json</code>).</li>
  </ul>
</footer>
</div>
<script>{JS.replace('__DATA__', json.dumps(D, ensure_ascii=False))}</script>
"""

(SP / "monthly_viz.html").write_text(HTML, encoding="utf-8")
print(f"저장: {SP / 'monthly_viz.json'}\n      {SP / 'monthly_viz.html'}")
print(f"{D['n']}개월 · 누적 {pc(D['cum'], 1)} (코스피 {pc(D['bm_cum'], 1)}) · "
      f"CAGR {pc(D['cagr'])} · 월 승률 {pc(D['win_rate'], 0, False)}")
