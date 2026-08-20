"""알고리즘 #1 월중(MTD) 성과 시각화 — 전일 종가 기준. 읽기 전용.

재현 로직·기준일 규칙은 `mtd_performance.py`를 그대로 쓴다 — 표와 그림이 어긋나지
않도록 원천을 한 곳에 둔다.

**매일 돌리는 것을 전제로 한다.** 기준일은 전 영업일, 형성일은 그 직전 월말 거래일을
자동으로 잡으므로 달이 바뀌어도 손댈 게 없다. 같은 날 몇 번을 돌려도 결과가 같다.

벤치마크가 그래도 하루 짧을 수 있다(지수 배치가 밀린 경우). 그때는 **두 계열을 같은
날까지 자른 값**을 초과성과로 쓰고, 포트폴리오만 기준일까지 연장해 그린다.

사용법:
  python analysis/algorithm1/mtd_viz.py                     # 전 영업일 기준
  python analysis/algorithm1/mtd_viz.py --as-of 2026-08-18  # 기준일 지정
  python analysis/algorithm1/mtd_viz.py --today             # 당일 종가까지
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mtd_performance import (  # noqa: E402
    bm_return, build, parse_args, resolve_window, stock_paths,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
SP = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)

FORM, AS_OF = resolve_window(parse_args())
ctx = build(FORM, AS_OF)
days = list(ctx.tdays)
rows = stock_paths(ctx, ctx.end)

# ── 일별 계열 ──────────────────────────────────────────────────────────────
# 포트폴리오 = 1 + 노출 × Σ(비중 × (가치배수 − 1)). 엔진의 합산식과 같다.
nav = [sum(r["w"] * (r["path"][d] - 1.0) for r in rows) * ctx.exposure for d in days]
nav_ns = [sum(r["w"] * (r["path_nostop"][d] - 1.0) for r in rows) * ctx.exposure for d in days]

bm_raw = {r.date: float(r.close) for r in ctx.db.execute(text(
    """SELECT p.date,p.close FROM prices p JOIN instruments i ON i.id=p.instrument_id
       WHERE i.ticker='KOSPI' AND p.period='D' AND p.date BETWEEN :a AND :b ORDER BY p.date"""),
    {"a": FORM, "b": ctx.end})}
b0 = bm_raw[FORM]
bm = [(bm_raw[d] / b0 - 1.0) if d in bm_raw else None for d in days]

mtd = nav[-1]
daily = [None] + [(1 + nav[i]) / (1 + nav[i - 1]) - 1 for i in range(1, len(nav))]

# 초과성과는 **두 계열이 다 있는 마지막 날** 기준으로만 말한다
j = max(i for i, v in enumerate(bm) if v is not None)
match = dict(date=days[j].isoformat(), mp=nav[j], bm=bm[j], excess=nav[j] - bm[j],
             aligned=(j == len(days) - 1))

stopped = [r for r in rows if r["stopped"]]
sect: dict[str, float] = {}
for r in rows:
    sect[r["sector"]] = sect.get(r["sector"], 0.0) + r["w"]

D = dict(
    form=FORM.isoformat(), end=ctx.end.isoformat(), last_bm=ctx.last_bm.isoformat(),
    n_days=len(days) - 1, n_stocks=len(rows), exposure=ctx.exposure,
    mtd=mtd, mtd_nostop=nav_ns[-1], stop_drag=mtd - nav_ns[-1],
    today=daily[-1], today_date=days[-1].isoformat(),
    best=max(rows, key=lambda r: r["r"])["name"], worst=min(rows, key=lambda r: r["r"])["name"],
    up=sum(1 for r in rows if r["r"] > 0), match=match,
    dates=[d.isoformat() for d in days], nav=nav, nav_nostop=nav_ns, bm=bm, daily=daily,
    stops=[dict(name=r["name"], date=r["stop_date"].isoformat(), r=r["r"],
                r_nostop=r["r_nostop"], w=r["w"]) for r in stopped],
    sectors=sorted(sect.items(), key=lambda kv: -kv[1]),
    rows=sorted([dict(name=r["name"], ticker=r["ticker"], sector=r["sector"], w=r["w"],
                      r=r["r"], r_nostop=r["r_nostop"], contrib=r["contrib"],
                      stopped=r["stopped"],
                      stop_date=r["stop_date"].isoformat() if r["stop_date"] else None)
                 for r in rows], key=lambda r: -r["contrib"]),
)
ctx.db.close()
(SP / "mtd_viz.json").write_text(json.dumps(D, ensure_ascii=False, indent=1))


# ── HTML ──────────────────────────────────────────────────────────────────
def pc(v, d=2, sign=True):
    return f"{v*100:+.{d}f}%" if sign else f"{v*100:.{d}f}%"


def md(iso):
    return f"{int(iso[5:7])}/{int(iso[8:10])}"


kpi = [("포트폴리오 월중 수익률", pc(mtd), f"{D['form']} → {D['end']} · {D['n_days']}거래일",
        "up" if mtd > 0 else "down"),
       (f"코스피 대비 초과 ({md(match['date'])}까지)", pc(match["excess"]),
        f"포트폴리오 {pc(match['mp'])} · 코스피 총수익 {pc(match['bm'])}",
        "up" if match["excess"] > 0 else "down"),
       (f"당일 ({md(D['today_date'])})", pc(D["today"]),
        f"편입 {D['n_stocks']}종목 · 주식 노출 {D['exposure']:.0%}",
        "up" if D["today"] > 0 else "down"),
       ("손실 제한 영향", pc(D["stop_drag"]),
        f"{len(D['stops'])}건 발동 · 미발동 시 {pc(D['mtd_nostop'])}",
        "down" if D["stop_drag"] < 0 else "up")]
kpis = "".join(f"""
  <article class="kpi">
    <p class="eyebrow">{t}</p>
    <p class="big num {c}">{v}</p>
    <p class="note">{s}</p>
  </article>""" for t, v, s, c in kpi)

trs = "".join(f"""
  <tr class="{'stopped' if r['stopped'] else ''}">
    <td class="nm">{r['name']}<i>{r['ticker']}</i></td>
    <td class="sec">{r['sector']}</td>
    <td class="num">{r['w']*100:.1f}%</td>
    <td class="num {'up' if r['r'] > 0 else 'down'}">{pc(r['r'], 1)}</td>
    <td class="num {'up' if r['contrib'] > 0 else 'down'}">{pc(r['contrib'], 2)}p</td>
    <td class="tag">{('손절 ' + md(r['stop_date'])) if r['stopped'] else ''}</td>
  </tr>""" for r in D["rows"])

stopnote = ""
if D["stops"]:
    li = "".join(f"<li><b>{s['name']}</b> · {md(s['date'])} 익일시가 청산 "
                 f"<span class='num down'>{pc(s['r'], 1)}</span> — 미청산 시 "
                 f"<span class='num'>{pc(s['r_nostop'], 1)}</span> "
                 f"(차이 {pc(s['r']-s['r_nostop'], 1)}p, 비중 {s['w']*100:.1f}%)</li>"
                 for s in D["stops"])
    stopnote = f"""
<h2><span class="n">04</span>손실 제한 발동</h2>
<p class="lede">사전 정의된 하락 폭에 도달해 익영업일 시가에 청산했고, 다음 정기 리밸런싱까지
현금으로 보유한다. 이달은 이 장치가 <b>수익률을 깎았다</b> — 청산 후 주가가 되돌아섰기 때문이다.
규칙은 사후에 유리했는지로 판단하지 않는다.</p>
<ul class="stops">{li}</ul>
<div class="cmp">
  <div><span>실제 (손실 제한 적용)</span><b class="num {'up' if mtd > 0 else 'down'}">{pc(mtd)}</b></div>
  <div><span>미적용 가정</span><b class="num">{pc(D['mtd_nostop'])}</b></div>
  <div><span>차이</span><b class="num down">{pc(D['stop_drag'])}p</b></div>
</div>"""

secbars = "".join(
    f'<div class="sb"><span>{k}</span>'
    f'<div class="bar"><i style="width:{v/D["sectors"][0][1]*100:.1f}%"></i></div>'
    f'<b class="num">{v*100:.1f}%</b></div>' for k, v in D["sectors"])

bmcap = "" if match["aligned"] else (
    f"코스피 총수익 계열은 {D['last_bm']}까지만 수신돼 먼저 끝난다. ")
bmwarn = "" if match["aligned"] else f"""
<p class="warn">벤치마크(코스피 총수익)는 {D['last_bm']}까지만 수신됐다 — 지수 배치(평일
18:30)가 기준일치를 아직 채우지 못했다. 초과성과는 <b>두 계열이 모두 있는
{md(match['date'])}까지</b>로만 계산했고, 그림에서 코스피 선이 먼저 끝나는 이유도 같다.</p>"""

CSS = """
:root{
  --ground:#f5f7fa; --card:#ffffff; --ink:#151920; --ink2:#39414e; --muted:#6a7382;
  --navy:#1e3a5f; --good:#2c6e54; --warn:#96650f; --alert:#a8332b;
  --rule:#e3e7ec; --rule2:#eef1f5; --band:#eef2f7;
  --goodbg:#e7f2ec; --warnbg:#faf1de; --alertbg:#fbeae8; --line:#1e3a5f; --line2:#9aa5b4;
  --bm:#a8641f;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ground:#13161c; --card:#1a1e26; --ink:#e7eaef; --ink2:#c2c9d4; --muted:#8b95a4;
  --navy:#8fb4dd; --good:#63b48d; --warn:#d1a24a; --alert:#e0736a;
  --rule:#2a303a; --rule2:#232831; --band:#20252e;
  --goodbg:#1a2c25; --warnbg:#2c2618; --alertbg:#2e1e1d; --line:#8fb4dd; --line2:#6b7686;
  --bm:#d99a55;
}}
:root[data-theme="dark"]{
  --ground:#13161c; --card:#1a1e26; --ink:#e7eaef; --ink2:#c2c9d4; --muted:#8b95a4;
  --navy:#8fb4dd; --good:#63b48d; --warn:#d1a24a; --alert:#e0736a;
  --rule:#2a303a; --rule2:#232831; --band:#20252e;
  --goodbg:#1a2c25; --warnbg:#2c2618; --alertbg:#2e1e1d; --line:#8fb4dd; --line2:#6b7686;
  --bm:#d99a55;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",
    "Malgun Gothic","Noto Sans KR",system-ui,sans-serif;
  font-size:15px;line-height:1.65;-webkit-font-smoothing:antialiased}
.num{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.wrap{max-width:940px;margin:0 auto;padding:40px 22px 80px}
.up{color:var(--good)} .down{color:var(--alert)}

header{border-bottom:2px solid var(--navy);padding-bottom:18px;margin-bottom:26px}
.kicker{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin:0 0 8px}
h1{font-size:clamp(24px,4vw,33px);line-height:1.15;margin:0 0 10px;letter-spacing:-.03em;
  font-weight:800;color:var(--navy);text-wrap:balance}
.sub{margin:0;color:var(--ink2);max-width:64ch}
.meta{display:flex;flex-wrap:wrap;gap:6px 22px;margin-top:14px;font-size:12.5px;color:var(--muted)}
.meta b{color:var(--ink2);font-weight:700}

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
.legend i.dash{border-radius:0;background:repeating-linear-gradient(
  to right, var(--line2) 0 4px, transparent 4px 7px)}

.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13px;background:var(--card)}
th{text-align:right;font-size:11px;color:var(--muted);font-weight:700;padding:8px 10px;
  border-bottom:1px solid var(--rule);white-space:nowrap}
th:first-child,th:nth-child(2){text-align:left}
td{padding:8px 10px;border-bottom:1px solid var(--rule2);text-align:right;white-space:nowrap}
td.nm,td.sec,td.tag{text-align:left}
td.nm{font-weight:700}
td.nm i{font-style:normal;font-family:ui-monospace,monospace;font-size:10.5px;
  color:var(--muted);margin-left:7px}
td.sec{color:var(--muted);font-size:12px}
td.tag{font-size:11px;color:var(--alert);font-weight:700}
tr.stopped{background:var(--alertbg)}
tfoot td{font-weight:800;border-top:1px solid var(--rule);border-bottom:none}

.stops{margin:0 0 16px;padding-left:20px;font-size:13.5px;color:var(--ink2)}
.stops li{margin-bottom:5px}
.cmp{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.cmp div{background:var(--card);border:1px solid var(--rule);border-radius:9px;padding:12px 14px}
.cmp span{display:block;font-size:11.5px;color:var(--muted);margin-bottom:4px}
.cmp b{font-size:22px;font-weight:800;letter-spacing:-.03em}

.sb{display:grid;grid-template-columns:110px 1fr 52px;align-items:center;gap:10px;
  font-size:12.5px;margin-bottom:5px}
.sb span{color:var(--ink2)}
.sb .bar{background:var(--rule2);border-radius:3px;height:15px;overflow:hidden}
.sb .bar i{display:block;height:100%;background:var(--navy);border-radius:3px}
.sb b{text-align:right;color:var(--ink2);font-weight:700}

.warn{background:var(--warnbg);border-left:3px solid var(--warn);border-radius:0 6px 6px 0;
  padding:11px 14px;font-size:12.5px;color:var(--ink2);margin:16px 0 0;max-width:72ch}
.warn b{font-weight:700}
footer{margin-top:46px;padding-top:16px;border-top:1px solid var(--rule);
  font-size:11.5px;color:var(--muted)}
footer li{margin-bottom:3px}
"""

JS = r"""
const D = __DATA__;
const css = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim();
const CW = 880;                       // 고정 논리폭 — 레이아웃 폭에 맞춰 그리면 화면·인쇄가 어긋난다
function hidpi(cv,w,h){const r=2;cv.width=w*r;cv.height=h*r;cv.removeAttribute('style');
  const x=cv.getContext('2d');x.setTransform(r,0,0,r,0,0);return x;}
const fmt = v => (v>=0?'+':'') + (v*100).toFixed(1) + '%';
const md = s => (+s.slice(5,7)) + '/' + (+s.slice(8,10));

function lineChart(){
  const cv=document.getElementById('c1'), W=CW, H=330, x0=52,x1=W-14,y0=22,y1=H-30;
  const g=hidpi(cv,W,H);
  const all=[...D.nav,...D.nav_nostop,...D.bm].filter(v=>v!=null);
  let lo=Math.min(...all,0), hi=Math.max(...all);
  const pad=(hi-lo)*0.12||0.01; lo-=pad; hi+=pad;
  const X=i=>x0+(x1-x0)*i/(D.dates.length-1), Y=v=>y1-(y1-y0)*(v-lo)/(hi-lo);

  g.font='11px ui-monospace,monospace'; g.textAlign='right'; g.textBaseline='middle';
  const step=(hi-lo)/5;
  for(let k=0;k<=5;k++){const v=lo+step*k, y=Y(v);
    g.strokeStyle=Math.abs(v)<1e-9?css('--muted'):css('--rule'); g.lineWidth=1;
    g.beginPath(); g.moveTo(x0,y); g.lineTo(x1,y); g.stroke();
    g.fillStyle=css('--muted'); g.fillText((v*100).toFixed(1)+'%',x0-8,y);}
  g.strokeStyle=css('--muted'); g.lineWidth=1.2;
  g.beginPath(); g.moveTo(x0,Y(0)); g.lineTo(x1,Y(0)); g.stroke();

  g.textAlign='center'; g.textBaseline='top'; g.fillStyle=css('--muted');
  D.dates.forEach((d,i)=>{ if(i%2===0||i===D.dates.length-1) g.fillText(md(d),X(i),y1+8); });

  const draw=(arr,color,width,dash)=>{
    g.save(); g.setLineDash(dash||[]); g.strokeStyle=color; g.lineWidth=width;
    g.lineJoin='round'; g.beginPath(); let started=false;
    arr.forEach((v,i)=>{ if(v==null) return;
      started ? g.lineTo(X(i),Y(v)) : (g.moveTo(X(i),Y(v)), started=true); });
    g.stroke(); g.restore();};

  draw(D.nav_nostop, css('--line2'), 1.4, [4,3]);
  draw(D.bm,         css('--bm'),    2);
  draw(D.nav,        css('--line'),  2.8);

  // 손절 실행일 표시
  (D.stops||[]).forEach(s=>{ const i=D.dates.indexOf(s.date); if(i<0) return;
    g.save(); g.setLineDash([3,3]); g.strokeStyle=css('--alert'); g.lineWidth=1.2;
    g.beginPath(); g.moveTo(X(i),y0); g.lineTo(X(i),y1); g.stroke(); g.restore();
    g.fillStyle=css('--alert'); g.font='700 10.5px system-ui'; g.textAlign='left';
    g.textBaseline='top'; g.fillText(' '+s.name+' 청산',X(i)+2,y0);});

  // 끝점 라벨
  const tip=(arr,color)=>{ let i=-1; arr.forEach((v,k)=>{if(v!=null) i=k;}); if(i<0) return;
    g.fillStyle=color; g.beginPath(); g.arc(X(i),Y(arr[i]),3.5,0,7); g.fill();
    g.font='700 12px ui-monospace,monospace'; g.textAlign='right'; g.textBaseline='bottom';
    g.fillText(fmt(arr[i]),X(i)-6,Y(arr[i])-6);};
  tip(D.bm,css('--bm')); tip(D.nav,css('--line'));
}

function contribChart(){
  const r=D.rows, cv=document.getElementById('c2');
  const W=CW, rowH=24, H=r.length*rowH+34, x0=104, xm=W-58;
  const g=hidpi(cv,W,H);
  const m=Math.max(...r.map(v=>Math.abs(v.contrib)))*1.05||0.01;
  const zero=x0+(xm-x0)*0.62;              // 음수 쪽이 짧으므로 0선을 오른쪽에 둔다
  const sc=v=>v>=0?(xm-zero)*v/m:(zero-x0)*v/m;
  g.strokeStyle=css('--rule'); g.lineWidth=1;
  g.beginPath(); g.moveTo(zero,14); g.lineTo(zero,H-20); g.stroke();
  r.forEach((v,i)=>{
    const y=20+i*rowH, w=sc(v.contrib), pos=v.contrib>=0;
    g.fillStyle=pos?css('--navy'):css('--alert');
    g.globalAlpha=v.stopped?1:.88;
    g.fillRect(pos?zero:zero+w, y, Math.abs(w), rowH-9);
    g.globalAlpha=1;
    g.fillStyle=css('--ink2'); g.font='12px system-ui';
    g.textAlign='right'; g.textBaseline='middle'; g.fillText(v.name,x0-10,y+(rowH-9)/2);
    g.fillStyle=pos?css('--good'):css('--alert');
    g.font='700 11.5px ui-monospace,monospace'; g.textAlign='left';
    g.fillText((v.contrib*100).toFixed(2)+'%p', (pos?zero+w:zero)+6, y+(rowH-9)/2);
  });
}
lineChart(); contribChart();
new MutationObserver(()=>{lineChart();contribChart();})
  .observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
matchMedia('(prefers-color-scheme:dark)').addEventListener('change',()=>{lineChart();contribChart();});
"""

title = f"{ctx.end.year}년 {ctx.end.month}월 월중 성과"
html = f"""<title>알고리즘 #1 · {title}</title>
<style>{CSS}</style>
<div class="wrap">
<header>
  <p class="kicker">Algorithm #1 · Month-to-date</p>
  <h1>{title}</h1>
  <p class="sub">{FORM.month}월 말 형성한 포트폴리오를 다음 정기 리밸런싱까지 그대로 들고 가는 구간이다.
  아래 수치는 모두 배당을 반영한 총수익 기준이며, 거래비용은 형성 시점에 이미 반영돼 있다.</p>
  <div class="meta">
    <span>형성일 <b>{D['form']}</b></span>
    <span>기준일 <b>{D['end']}</b></span>
    <span>경과 <b>{D['n_days']}거래일</b></span>
    <span>편입 <b>{D['n_stocks']}종목</b></span>
    <span>주식 노출 <b>{D['exposure']:.0%}</b></span>
  </div>
</header>

<div class="kpis">{kpis}</div>
{bmwarn}

<h2><span class="n">01</span>누적 수익률 추이</h2>
<p class="lede">형성일을 0%로 놓은 월중 누적 수익률이다. 점선은 손실 제한을 적용하지 않았다면
그려졌을 경로로, 실선과 벌어진 폭이 곧 이 장치가 이달에 치른 비용이다.</p>
<figure>
  <div class="legend">
    <span><i style="background:var(--line)"></i>포트폴리오</span>
    <span><i style="background:var(--bm)"></i>코스피 총수익</span>
    <span><i class="dash"></i>손실 제한 미적용 가정</span>
  </div>
  <canvas id="c1"></canvas>
  <figcaption>{bmcap}세로 점선은 손실 제한에 따른 청산일이다.</figcaption>
</figure>

<h2><span class="n">02</span>종목별 기여도</h2>
<p class="lede">비중 × 수익률로, 전부 더하면 포트폴리오 월중 수익률이 된다.
막대 길이가 실제로 포트폴리오를 움직인 크기다 — 수익률이 높아도 비중이 작으면 짧다.</p>
<figure>
  <canvas id="c2"></canvas>
  <figcaption>{D['n_stocks']}종목 중 <b>{D['up']}종목</b>이 상승.
  최고 {D['best']} · 최저 {D['worst']}.</figcaption>
</figure>

<h2><span class="n">03</span>보유 종목</h2>
<div class="scroll"><table>
  <thead><tr><th>종목</th><th>업종</th><th>비중</th><th>수익률</th><th>기여도</th><th></th></tr></thead>
  <tbody>{trs}</tbody>
  <tfoot><tr>
    <td class="nm">합계</td><td></td>
    <td class="num">{sum(r['w'] for r in D['rows'])*100:.1f}%</td><td></td>
    <td class="num {'up' if mtd > 0 else 'down'}">{pc(mtd)}p</td><td></td>
  </tr></tfoot>
</table></div>
{stopnote}

<h2><span class="n">05</span>업종 구성</h2>
<p class="lede">한 업종에 최대 2종목, 업종 합계 50%, 개별 종목 25% 한도가 걸려 있다.</p>
{secbars}

<footer>
  <ul>
    <li>수익률은 배당을 반영한 총수익 기준. 벤치마크는 코스피 총수익지수.</li>
    <li>월중 구간이라 거래는 손실 제한 청산 외에 없다 — 정기 리밸런싱 비용은 형성 시점에 반영됨.</li>
    <li>시장 국면 판정은 형성일 값을 월중 유지한 것으로, 엔진의 월중 재판정과는 다를 수 있다.</li>
    <li>슬리피지 미반영. 11거래일 구간의 성과는 표본이 작아 전략 평가의 근거로 쓰지 않는다.</li>
  </ul>
</footer>
</div>
<script>{JS.replace("__DATA__", json.dumps(D, ensure_ascii=False))}</script>
"""

(SP / "mtd_viz.html").write_text(html)
print(f"저장: {SP/'mtd_viz.json'}\n      {SP/'mtd_viz.html'}")
print(f"MTD {mtd:+.2%} · 초과 {match['excess']*100:+.2f}%p ({match['date']}까지) "
      f"· 당일 {D['today']*100:+.2f}%")
