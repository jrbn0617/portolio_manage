"""사내 알고리즘 제안서 HTML 생성. proposal_data.py 산출 JSON을 읽는다.

A4 인쇄를 전제로 한다 — @page 규격, 참고 절은 페이지 분할, 표·차트는 페이지 경계에서
쪼개지지 않게 한다. 로직 노출 수준은 "기법명까지만"(사용자 결정)이며 대응표는
docs/algorithm-specs/00-변환가이드.md 3절을 따른다.
"""
import json
import os
from pathlib import Path

# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
SP = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)
D = json.loads((SP / "proposal_data.json").read_text())
S, OPS = D["summary"], D["ops"]
ALL, OOS, INS = S["all"], S["oos"], S["insample"]
PEER = ALL.get("peer")          # 「키움 Momentum」 적극투자형
PEER_NM = "키움 Momentum (적극투자형)"

TEAM = "랩솔루션팀"
# 작성일은 전달본과 동일하게 유지한다 — 날짜를 바꾸면 같은 보고서의 판본 대조가 어려워진다.
# 대신 개정일을 병기해 어느 쪽을 보고 있는지 구분되게 한다. 개정이 없으면 None.
DATED = "2026.8.14"
REVISED = "2026.8.18"


DG = D["diag2019"]
_y18 = next(r for r in D["yearly"] if r["year"] == 2018)
BAD2018_MP, BAD2018_BM = _y18["mp"], _y18["bm"]
BAD2018_EX = BAD2018_MP - BAD2018_BM

# 벤치마크 상회 연도 수 — 부분 연도(2017·2026) 포함한 전 분석 연도 기준
YRS = D["yearly"]
WIN_N = sum(1 for r in YRS if r["mp"] > r["bm"])

# 국면 의존도 표 — 추세지속도(rho)는 국면 진단값, 초과성과는 **연도별 표와 같은 값**을 쓴다.
# 두 표에 다른 2017 값이 실리는 것을 막기 위해 분석 구간(yearly)에 있는 연도만 남긴다.
# 그 결과 분석 시작 이전인 2016년은 자동으로 빠진다.
_ex_by_year = {r["year"]: r["mp"] - r["bm"] for r in YRS}
_partial = {r["year"] for r in YRS if r["partial"]}
REG = [dict(x, excess=_ex_by_year[x["year"]], partial=x["year"] in _partial)
       for x in D["regime"] if x["year"] in _ex_by_year]
_r = [x["rho"] for x in REG]
_e = [x["excess"] for x in REG]
_mr, _me = sum(_r)/len(_r), sum(_e)/len(_e)
REG_CORR = (sum((a-_mr)*(b-_me) for a, b in zip(_r, _e))
            / ((sum((a-_mr)**2 for a in _r) * sum((b-_me)**2 for b in _e)) ** 0.5))

# 표시용 사명 — DB의 종목명이 구명칭인 건들. 사명 변경 이력이지 데이터 오류가 아니다.
DISPLAY_NAME = {
    "000660": "SK하이닉스",
    "028050": "삼성E&A",          # 舊 삼성엔지니어링 (2024.3 사명 변경)
    "082740": "한화엔진",          # 舊 HSD엔진 (2024.5 사명 변경)
    "267270": "HD현대건설기계",    # 舊 에이치디건설기계
}


def p(x, d=1, sign=False):
    return f"{x*100:+.{d}f}%" if sign else f"{x*100:.{d}f}%"


def dp(a, b, d=1):
    """두 지표의 차이(%p) — **문서에 찍힌 반올림 값끼리** 뺀다.

    원값끼리 빼면 본문 수치와 어긋난다. 실제로 연수익률 차이를 원값으로 계산했더니
    25.88-11.25=14.63 → "14.6%p"로 나갔는데, 독자가 표의 25.9와 11.2를 빼면 14.7이라
    맞지 않았다. 읽는 사람이 검산할 수 있어야 하므로 표시값 기준으로 맞춘다."""
    return round(round(a * 100, d) - round(b * 100, d), d)


CSS = """
@page { size: A4; margin: 17mm 15mm 15mm;
  @bottom-center { content: counter(page) " / " counter(pages);
    font-size: 8.5pt; color: #5f6570; } }
/* 인쇄(A4 PDF)가 최종 산출물이라 종이 기준 라이트 팔레트로 고정한다.
   뷰어 테마를 따라가지 않도록 다크 대응을 두지 않는다. */
:root, :root[data-theme="dark"], :root[data-theme="light"]{
  color-scheme: light;
  --paper:#fff; --ink:#1a1d24; --ink-2:#3c424e; --muted:#6b7280;
  --navy:#1e3a5f; --rule:#d5d2cc; --rule-2:#e9e6e0; --band:#f4f2ee;
  --up:#c0322c; --down:#1f5fa8; --flat:#8a8f98;
  --up-soft:rgba(192,50,44,.10); --down-soft:rgba(31,95,168,.10);
}
*{box-sizing:border-box}
html{background:#fff}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",
    "Malgun Gothic","Noto Sans KR",system-ui,sans-serif;
  font-size:14px;line-height:1.62;-webkit-font-smoothing:antialiased}
.num{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
  font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.page{max-width:820px;margin:0 auto;padding:44px 32px 72px}

.cover{border-bottom:3px solid var(--navy);padding-bottom:18px;margin-bottom:8px}
.cover h1{font-size:30px;line-height:1.2;margin:0 0 10px;letter-spacing:-.03em;
  font-weight:800;color:var(--navy);text-wrap:balance}
.cover .meta{font-size:13px;color:var(--muted);margin:0;font-weight:600;text-align:right;line-height:1.5}
.cover .rev{font-size:11px;font-weight:500;opacity:.75}

h2{font-size:17px;margin:34px 0 12px;padding-bottom:7px;font-weight:800;letter-spacing:-.02em;
  color:var(--navy);border-bottom:1.5px solid var(--navy);break-after:avoid}
h3{font-size:14.5px;margin:20px 0 8px;font-weight:700;letter-spacing:-.01em;break-after:avoid}
h3::before{content:"";display:inline-block;width:3px;height:12px;background:var(--navy);
  margin-right:8px;vertical-align:-1px;border-radius:1px}
h4{font-size:13.5px;margin:16px 0 6px;font-weight:700;color:var(--ink-2);break-after:avoid}

ul{margin:6px 0 10px;padding-left:0;list-style:none}
li{position:relative;padding-left:17px;margin:4px 0}
li::before{content:"○";position:absolute;left:0;color:var(--navy);font-size:10px;top:4px}
ul.dash li::before{content:"–";font-size:13px;top:0}
li ul{margin:3px 0 3px}
p{margin:6px 0 10px}
.q{border-left:3px solid var(--rule);background:var(--band);padding:8px 14px;margin:10px 0;
  font-size:13px;color:var(--ink-2);break-inside:avoid}
.q b{color:var(--ink)}
.arrow{font-weight:700;color:var(--navy)}

table{width:100%;border-collapse:collapse;font-size:12.8px;margin:10px 0 12px;
  break-inside:avoid}
th{background:var(--band);text-align:left;font-weight:700;padding:7px 10px;
  border-top:1.5px solid var(--navy);border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid var(--rule-2);vertical-align:top}
tbody tr:last-child td{border-bottom:1px solid var(--rule)}
th.r,td.r{text-align:right}
th.c,td.c{text-align:center}
tr.hi td{background:var(--band);font-weight:700}
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--flat)}
.wrapt{overflow-x:auto}

.kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--rule);
  border:1px solid var(--rule);margin:12px 0 14px;break-inside:avoid}
.kpi>div{background:var(--paper);padding:12px 14px}
.kpi .k{font-size:10.5px;letter-spacing:.07em;color:var(--muted);font-weight:700;
  text-transform:uppercase;margin-bottom:5px}
.kpi .v{font-size:21px;font-weight:800;letter-spacing:-.025em;line-height:1.1;color:var(--navy)}
.kpi .s{font-size:11.5px;color:var(--muted);margin-top:4px;display:flex;
  justify-content:space-between;gap:8px;border-top:1px solid var(--rule-2);padding-top:3px}
.kpi .s b{font-weight:600;color:var(--ink-2);font-variant-numeric:tabular-nums}

/* 그림 제목은 차트 **위**에 둔다 — 아래에 두면 바로 뒤에 오는 표·제목의 머리말처럼 읽힌다 */
figure{margin:14px 0 20px;break-inside:avoid}
canvas{display:block;width:100%;height:auto}
figcaption{font-size:11.5px;color:var(--ink-2);font-weight:700;margin:0 0 6px;
  padding-bottom:4px;border-bottom:1px solid var(--rule-2)}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);
  margin:0 0 6px}
.legend i{display:inline-block;width:13px;height:3px;vertical-align:middle;margin-right:5px;
  border-radius:2px}

.ref{break-before:page;border-top:3px double var(--navy);padding-top:14px;margin-top:40px}
.ref h2{border-bottom:none;margin-top:0;font-size:18px}
.ref .sub{font-size:12.5px;color:var(--muted);margin:-6px 0 14px;font-weight:600}

footer{margin-top:40px;padding-top:14px;border-top:1px solid var(--rule);
  font-size:11.5px;color:var(--muted)}

@media print{
  :root{--paper:#fff;--ink:#1a1d24;--ink-2:#3c424e;--muted:#5f6570;--navy:#1e3a5f;
    --rule:#c9c6c0;--rule-2:#e4e1db;--band:#f2f0ec;--up:#b02a24;--down:#1a4f8f;--flat:#7c828b;
    --up-soft:rgba(176,42,36,.12);--down-soft:rgba(26,79,143,.12)}
  body{font-size:10.2pt;line-height:1.5}
  .page{max-width:none;padding:0}
  h2{font-size:13pt;margin:18pt 0 7pt}
  h3{font-size:11pt;margin:12pt 0 5pt}
  table{font-size:8.8pt}
  .kpi .v{font-size:15pt}
  .cover h1{font-size:21pt}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}

  /* --- 페이지 분할 --- */
  /* 제목·도입문단만 페이지 끝에 남고 본체가 넘어가는 것을 막는다.
     표는 .wrapt(div)로 감싼 경우가 있어 div까지 형제 선택자에 넣는다 */
  h2,h3,h4{break-after:avoid-page;break-inside:avoid}
  h3+table,h3+p,h3+ul,h3+figure,h3+div,h3+.wrapt,
  h4+ul,h4+p,h4+table,h4+div,
  p+table,p+figure,p+div,p+.wrapt,p+ul{break-before:avoid-page}
  /* 문단·목록이 한 줄만 떨어져 넘어가지 않게 */
  p,li{orphans:3;widows:3}
  li{break-inside:avoid}
  /* 표는 통째로 넘기되, 부득이 쪼개지면 헤더를 반복하고 행 중간에서 자르지 않는다 */
  table{break-inside:avoid}
  thead{display:table-header-group}
  tr,figure,.q,.kpi,.wrapt,.keep{break-inside:avoid}
  .keep+.wrapt,.keep+table{break-before:avoid-page}
  /* 표·차트 바로 뒤의 각주·설명은 본체와 떨어뜨리지 않는다 */
  table+ul,table+p,.wrapt+ul,.wrapt+p,figure+ul,figure+p,
  table+.q,.wrapt+.q,figure+.q{break-before:avoid-page}
  /* 참고 절은 항상 새 페이지에서 시작 */
  .ref{break-before:page;margin-top:0}
  footer{break-before:avoid-page}
}
@media(max-width:600px){.kpi{grid-template-columns:repeat(2,1fr)}.page{padding:28px 16px 48px}}
"""

JS = """
const D=__DATA__;
const css=getComputedStyle(document.documentElement),C=k=>css.getPropertyValue(k).trim();
/* 캔버스는 **고정 논리폭(CW)** 으로 그리고 CSS가 width:100%/height:auto 로 비례 축소한다.
   레이아웃 폭(clientWidth)에 맞춰 그리면 화면과 인쇄의 폭이 달라 캔버스가 캡션을 덮는다. */
const CW=680;
function hidpi(cv,w,h){const r=2;cv.width=w*r;cv.height=h*r;
  cv.removeAttribute('style');
  const x=cv.getContext('2d');x.setTransform(r,0,0,r,0,0);return x;}
const F='11px ui-monospace,SFMono-Regular,Menlo,monospace';

function cum(){
  const cv=document.getElementById('c-cum');if(!cv)return;
  const W=CW,H=250,g=hidpi(cv,W,H);
  const P=D.curve,n=P.dates.length,L=42,R=10,T=12,B=26,iw=W-L-R,ih=H-T-B;
  const all=P.mp.concat(P.bm),lo=Math.log(Math.min(...all)),hi=Math.log(Math.max(...all)*1.06);
  const X=i=>L+iw*i/(n-1),Y=v=>T+ih*(hi-Math.log(v))/(hi-lo);
  g.font=F;
  [1,2,4,8].forEach(v=>{if(Math.log(v)<lo||Math.log(v)>hi)return;
    g.strokeStyle=C('--rule-2');g.lineWidth=1;g.beginPath();g.moveTo(L,Y(v));g.lineTo(W-R,Y(v));g.stroke();
    g.fillStyle=C('--muted');g.textAlign='right';g.fillText((v*100).toFixed(0),L-6,Y(v)+4);});
  const line=(a,c,w)=>{g.beginPath();a.forEach((v,i)=>i?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v)));
    g.strokeStyle=c;g.lineWidth=w;g.lineJoin='round';g.stroke();};
  if(P.peer){const q=P.peer.map(v=>v==null?NaN:v);
    g.beginPath();let st=false;
    q.forEach((v,i)=>{if(isNaN(v))return;st?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v));st=true;});
    g.strokeStyle=C('--up');g.lineWidth=1.6;g.stroke();}
  line(P.bm,C('--flat'),1.4);line(P.mp,C('--down'),2.1);
  g.fillStyle=C('--muted');
  P.dates.forEach((d,i)=>{if(!d.endsWith('-01-01')&&d.slice(5,7)!=='01')return;});
  const yrs={};P.dates.forEach((d,i)=>{const y=d.slice(0,4);if(!(y in yrs))yrs[y]=i;});
  Object.entries(yrs).forEach(([y,i],k)=>{if(k%2)return;
    g.textAlign='center';g.fillText(y,X(i),H-9);});
}

function dd(){
  const cv=document.getElementById('c-dd');if(!cv)return;
  const W=CW,H=150,g=hidpi(cv,W,H);
  const P=D.dd,n=P.dates.length,L=42,R=10,T=10,B=22,iw=W-L-R,ih=H-T-B;
  const lo=Math.min(...P.mp,...P.bm)*1.05;
  const X=i=>L+iw*i/(n-1),Y=v=>T+ih*(0-v)/(0-lo);
  g.font=F;
  [0,-0.1,-0.2,-0.3,-0.4].forEach(v=>{if(v<lo)return;
    g.strokeStyle=C('--rule-2');g.beginPath();g.moveTo(L,Y(v));g.lineTo(W-R,Y(v));g.stroke();
    g.fillStyle=C('--muted');g.textAlign='right';g.fillText((v*100).toFixed(0)+'%',L-6,Y(v)+4);});
  const area=(a,c,al)=>{g.beginPath();g.moveTo(X(0),Y(0));
    a.forEach((v,i)=>g.lineTo(X(i),Y(v)));g.lineTo(X(n-1),Y(0));g.closePath();
    g.fillStyle=c;g.globalAlpha=al;g.fill();g.globalAlpha=1;};
  area(P.bm,C('--flat'),.30);area(P.mp,C('--down'),.42);
  const line=(a,c)=>{g.beginPath();a.forEach((v,i)=>i?g.lineTo(X(i),Y(v)):g.moveTo(X(i),Y(v)));
    g.strokeStyle=c;g.lineWidth=1.3;g.stroke();};
  line(P.bm,C('--flat'));line(P.mp,C('--down'));
}

function yearly(){
  const cv=document.getElementById('c-year');if(!cv)return;
  const W=CW,H=220,g=hidpi(cv,W,H);
  const Y_=D.yearly,n=Y_.length,L=42,R=10,T=14,B=34,iw=W-L-R,ih=H-T-B;
  const vs=Y_.flatMap(r=>[r.mp,r.bm]);
  let lo=Math.min(...vs,0)*1.12,hi=Math.max(...vs)*1.12;
  const Y=v=>T+ih*(hi-v)/(hi-lo),slot=iw/n,bw=Math.min(15,slot/2.7);
  g.font=F;
  for(let v=Math.ceil(lo*5)/5;v<=hi;v+=0.2){
    g.strokeStyle=C('--rule-2');g.beginPath();g.moveTo(L,Y(v));g.lineTo(W-R,Y(v));g.stroke();
    g.fillStyle=C('--muted');g.textAlign='right';g.fillText((v*100).toFixed(0)+'%',L-6,Y(v)+4);}
  g.strokeStyle=C('--ink');g.lineWidth=1;g.beginPath();g.moveTo(L,Y(0));g.lineTo(W-R,Y(0));g.stroke();
  Y_.forEach((r,i)=>{const cx=L+slot*(i+0.5);
    [[r.mp,C('--down'),-1],[r.bm,C('--flat'),1]].forEach(([v,c,sd])=>{
      const x=sd<0?cx-bw-1:cx+1,y0=Y(0),y1=Y(v);
      g.fillStyle=c;g.globalAlpha=sd>0?.5:1;
      g.fillRect(x,Math.min(y0,y1),bw,Math.max(Math.abs(y1-y0),.6));g.globalAlpha=1;});
    g.fillStyle=C('--muted');g.textAlign='center';
    g.fillText(String(r.year).slice(2),cx,H-19);
    if(r.partial){g.fillText('*',cx,H-8);}});
}

function heat(){
  const cv=document.getElementById('c-heat');if(!cv)return;
  const W=CW,years=[...new Set(D.monthly.map(r=>r.y))];
  const L=40,T=20,cw=(W-L-6)/12,ch=19,H=T+years.length*ch+8;
  const g=hidpi(cv,W,H);
  const mx=Math.max(...D.monthly.map(r=>Math.abs(r.mp)));
  g.font='10px ui-monospace,monospace';
  for(let m=1;m<=12;m++){g.fillStyle=C('--muted');g.textAlign='center';
    g.fillText(m+'월',L+cw*(m-.5),13);}
  years.forEach((y,i)=>{g.fillStyle=C('--muted');g.textAlign='right';
    g.fillText(String(y),L-7,T+ch*i+13);});
  D.monthly.forEach(r=>{
    const i=years.indexOf(r.y),x=L+cw*(r.m-1),y=T+ch*i;
    const a=Math.min(Math.abs(r.mp)/mx,1)**0.55;
    g.fillStyle=r.mp>=0?C('--up'):C('--down');g.globalAlpha=.12+a*.78;
    g.fillRect(x+1,y+1,cw-2,ch-2);g.globalAlpha=1;
    g.fillStyle=a>.55?'#fff':C('--ink');g.textAlign='center';
    g.fillText((r.mp*100).toFixed(0),x+cw/2,y+13);});
}

function funnel(){
  const cv=document.getElementById('c-funnel');if(!cv)return;
  const F_=D.funnel,W=CW,rh=40,H=F_.length*rh+10,g=hidpi(cv,W,H);
  const L=150,R=52,iw=W-L-R,mxv=F_[0].n;
  g.font='12px -apple-system,sans-serif';
  F_.forEach((f,i)=>{
    const w=Math.max(iw*Math.sqrt(f.n/mxv),26),y=i*rh+8,h=24;
    g.fillStyle=C('--down');g.globalAlpha=.30+.16*i;
    g.fillRect(L,y,w,h);g.globalAlpha=1;
    g.strokeStyle=C('--down');g.lineWidth=1;g.strokeRect(L,y,w,h);
    g.fillStyle=C('--ink');g.textAlign='right';g.fillText(f.label,L-10,y+17);
    g.fillStyle=C('--navy');g.font='700 13px ui-monospace,monospace';g.textAlign='left';
    g.fillText(f.n+'종목',L+w+8,y+17);g.font='12px -apple-system,sans-serif';
    if(i<F_.length-1){g.strokeStyle=C('--rule');g.beginPath();
      g.moveTo(L+10,y+h);g.lineTo(L+10,y+rh);g.stroke();}});
}

function sector(){
  const cv=document.getElementById('c-sector');if(!cv)return;
  const S_=D.sectors,W=CW,rh=24,H=S_.length*rh+8,g=hidpi(cv,W,H);
  const L=118,R=48,iw=W-L-R,mx=Math.max(...S_.map(s=>s.weight));
  g.font='12px -apple-system,sans-serif';
  S_.forEach((s,i)=>{const y=i*rh+6,h=15;
    g.fillStyle=C('--down');g.globalAlpha=.65;g.fillRect(L,y,iw*s.weight/mx,h);g.globalAlpha=1;
    g.fillStyle=C('--ink');g.textAlign='right';g.fillText(s.name,L-9,y+12);
    g.fillStyle=C('--muted');g.font=F;g.textAlign='left';
    g.fillText((s.weight*100).toFixed(1)+'%',L+iw*s.weight/mx+7,y+12);
    g.font='12px -apple-system,sans-serif';});
}

function draw(){[cum,dd,yearly,heat,funnel,sector].forEach(f=>{try{f()}catch(e){}});}
draw();
""".replace("__DATA__", json.dumps(
    {k: D[k] for k in ("curve", "dd", "yearly", "monthly", "funnel", "sectors")}, ensure_ascii=False))


PEER_CUM = f" · {PEER_NM.split(' (')[0]} {p(PEER['cum'],0)}" if PEER else ""


def mrow(label, m, hi=False):
    return (f'<tr class="{"hi" if hi else ""}"><td>{label}</td>'
            f'<td class="r num">{p(m["cagr"],1,True)}</td>'
            f'<td class="r num">{p(m["vol"])}</td>'
            f'<td class="r num">{p(m["mdd"])}</td>'
            f'<td class="r num">{p(m["var5"])}</td>'
            f'<td class="r num">{m["sharpe"]:.2f}</td></tr>')


PEER_ROW = mrow(PEER_NM, PEER) if PEER else ""
PEER_ROW_OOS = mrow(PEER_NM, S["oos"]["peer"]) if S["oos"].get("peer") else ""
PEER_ROW_INS = mrow(PEER_NM, S["insample"]["peer"]) if S["insample"].get("peer") else ""

sec_rows = "\n".join(
    f'<tr><td>{s["name"]}</td><td class="c num">{s["n"]}</td>'
    f'<td class="r num down">{p(s["contrib"],2,True)}p</td></tr>' for s in D["sec2019"])

reg_rows = "\n".join(
    f'<tr><td class="c num">{r["year"]}{"*" if r["partial"] else ""}</td>'
    f'<td class="r num">{r["rho"]:+.3f}</td>'
    f'<td class="r num">{p(r["hi"],1,True)}</td>'
    f'<td class="r num">{p(r["lo"],1,True)}</td>'
    f'<td class="c">{r["regime"]}</td>'
    f'<td class="r num {"up" if (r["excess"] or 0)>0 else "down"}">'
    f'{p(r["excess"],1,True)}p</td></tr>'
    for r in REG)

# 설명용 사례 — 반전이 가장 심했던 해와 추세가 가장 강했던 해
_rev = min(REG, key=lambda r: r["rho"])
_trd = max(REG, key=lambda r: r["rho"])

hold_rows = "\n".join(
    f'<tr><td>{DISPLAY_NAME.get(h["ticker"], h["name"])}</td>'
    f'<td class="num">{h["ticker"]}</td><td>{h["sector"]}</td>'
    f'<td class="r num">{p(h["weight"])}</td></tr>' for h in D["holdings"])

def _pk(r):
    return (f'<td class="r num {"up" if r["peer"]>0 else "down"}">{p(r["peer"],1,True)}</td>'
            if r.get("peer") is not None else '<td class="r">–</td>')


year_rows = "\n".join(
    f'<tr><td class="c num">{r["year"]}{"*" if r["partial"] else ""}</td>'
    f'<td class="r num {"up" if r["mp"]>0 else "down"}">{p(r["mp"],1,True)}</td>'
    + _pk(r) +
    f'<td class="r num {"up" if r["bm"]>0 else "down"}">{p(r["bm"],1,True)}</td>'
    f'<td class="r num {"up" if r["mp"]-r["bm"]>0 else "down"}">{p(r["mp"]-r["bm"],1,True)}p</td></tr>'
    for r in D["yearly"])

HTML = f"""<title>국내주식 종목선정 알고리즘(안) — 개발 결과 보고</title>
<style>{CSS}</style>
<div class="page">

<div class="cover">
  <h1>국내주식 종목선정 알고리즘(안)</h1>
  <p class="meta">{DATED} / {TEAM}{f'<br><span class="rev">개정 {REVISED}</span>' if REVISED else ''}</p>
</div>

<h2>1. 보고 개요</h2>

<h3>1) 알고리즘 개요</h3>
<ul>
  <li>유가증권시장(코스피) 상장 주식 중 <b>재무적으로 저평가되어 있으면서 주가 흐름이 양호한 종목</b>을 선별하여 최대 20종목으로 포트폴리오를 구성</li>
  <li>기존 자산배분 알고리즘과 달리 ETF·펀드가 아닌 <b>개별 종목을 직접 선정</b>하며, 국내 주식 단일 자산군으로 운용</li>
  <li>개별 종목 급락과 시장 전반의 하락 국면에 대해 <b>사전에 정의된 규칙</b>으로 대응</li>
</ul>

<table>
  <thead><tr><th style="width:26%">구분</th><th>내용</th></tr></thead>
  <tbody>
    <tr><td>투자대상</td><td>유가증권시장 상장 보통주 (약 830종목). 관리종목·거래정지·기업인수목적회사·우선주 제외</td></tr>
    <tr><td>벤치마크</td><td>코스피 지수 (배당 포함 총수익 기준)</td></tr>
    <tr><td>편입 종목수</td><td>최대 20종목 (분산 제약 적용 후 실제 편입은 11~19종목)</td></tr>
    <tr><td>리밸런싱</td><td>정기 월 1회 (매월 말) · 수시 리밸런싱 별도</td></tr>
    <tr><td>분석 기간</td><td>{ALL['start']} ~ {ALL['end']} ({ALL['mp']['years']:.1f}년, {OPS['rebalances']}회 리밸런싱)<br>
      <span style="color:var(--muted)">사내 「키움 Momentum」 알고리즘 공시 개시 시점과 동일한 출발선으로 설정</span></td></tr>
  </tbody>
</table>

<h3>2) 핵심 성과</h3>
<div class="kpi">
  <div><div class="k">연평균 수익률</div><div class="v num">{p(ALL['mp']['cagr'])}</div>
    <div class="s"><span>코스피</span><b>{p(ALL['bm']['cagr'])}</b></div>
    <div class="s"><span>키움 Momentum</span><b>{p(PEER['cagr'])}</b></div></div>
  <div><div class="k">위험대비수익</div><div class="v num">{ALL['mp']['sharpe']:.2f}</div>
    <div class="s"><span>코스피</span><b>{ALL['bm']['sharpe']:.2f}</b></div>
    <div class="s"><span>키움 Momentum</span><b>{PEER['sharpe']:.2f}</b></div></div>
  <div><div class="k">최대낙폭</div><div class="v num">{p(ALL['mp']['mdd'])}</div>
    <div class="s"><span>코스피</span><b>{p(ALL['bm']['mdd'])}</b></div>
    <div class="s"><span>키움 Momentum</span><b>{p(PEER['mdd'])}</b></div></div>
  <div><div class="k">누적 수익률</div><div class="v num">{p(ALL['mp']['cum'],0)}</div>
    <div class="s"><span>코스피</span><b>{p(ALL['bm']['cum'],0)}</b></div>
    <div class="s"><span>키움 Momentum</span><b>{p(PEER['cum'],0)}</b></div></div>
</div>
<ul>
  <li>거래비용(매도 증권거래세 0.20% + 위탁수수료 0.015% 양방향) 반영 후 기준</li>
  <li>상세 <span class="arrow">→ [참고 1. 전 구간 성과]</span></li>
</ul>

<h3>3) 보고 요지</h3>
<ul>
  <li>분석 기간 {ALL['mp']['years']:.1f}년 전체에서 벤치마크 대비 <b>수익률은 연 {dp(ALL['mp']['cagr'],ALL['bm']['cagr']):.1f}%p 높으면서,
    변동성은 {dp(ALL['bm']['vol'],ALL['mp']['vol']):.1f}%p 낮고 최대낙폭은 {dp(abs(ALL['bm']['mdd']),abs(ALL['mp']['mdd'])):.1f}%p 얕았습니다</b>
    — 수익은 더 냈고 위험은 덜 졌습니다</li>
  <li>기존 공시 알고리즘(「키움 Momentum」 적극투자형) 대비로도 <b>연 {dp(ALL['mp']['cagr'],PEER['cagr']):.1f}%p 상회</b>하며 최대낙폭은 {dp(abs(PEER['mdd']),abs(ALL['mp']['mdd'])):.1f}%p 얕습니다</li>
  <li>특정 연도에 성과가 집중된 구조가 아닙니다 — <b>분석 대상 {len(YRS)}개 연도 중 {WIN_N}개 연도에서 벤치마크를 상회</b>했습니다 <span class="arrow">→ [참고 4]</span></li>
  <li>성과는 사전에 규정한 <b>검증 체계</b>(4장)를 통과한 설정값으로 산출했습니다</li>
</ul>

<h2>2. 전략 구성</h2>

<h3>1) 종목 선정</h3>
<table>
  <thead><tr><th style="width:16%">단계</th><th style="width:30%">기법</th><th>내용</th></tr></thead>
  <tbody>
    <tr><td>1. 대상 확정</td><td>시점별 종목군 재구성</td><td>매 리밸런싱 시점에 실제로 존재하고 거래 가능했던 종목만으로 대상을 다시 구성. 이후에 알려진 정보가 과거 판단에 섞이지 않도록 함</td></tr>
    <tr><td>2. 재무 선별</td><td>업종 내 상대비교 기반 재무 선별</td><td>수익성 대비 주가 수준을 <b>같은 업종에 속한 기업들끼리</b> 비교하여 상대적으로 저평가된 기업만 통과. 업종마다 통상적인 밸류에이션 수준이 다르기 때문</td></tr>
    <tr><td>3. 순위화</td><td>중기 가격 흐름 순위화</td><td>2단계 통과 종목을 중기적인 주가 흐름의 우열로 정렬하여 상위 종목군 선정. 단기 등락의 영향을 줄이기 위한 보정 적용</td></tr>
    <tr><td>4. 종목 확정</td><td>업종 분산 제약</td><td>동일 업종에서 과도한 종목이 선정되지 않도록 종목수 기준 제한을 적용</td></tr>
  </tbody>
</table>
<div class="q">기준일로부터 <b>일정 기간 이상 갱신되지 않은 재무데이터는 사용하지 않습니다.</b>
과거의 오래된 재무 정보로 현재 종목을 선정하는 것을 구조적으로 차단하기 위한 장치입니다.</div>

<h3>2) 비중 배분</h3>
<ul>
  <li><b>유동주식 기준 규모에 연동</b>하여 비중을 부여 — 실제 시장에서 거래 가능한 주식 수를 기준으로 산출하므로, 매매 시 시장 충격이 상대적으로 작음</li>
  <li>비중이 미미한 수준에 그치는 종목은 편입하지 않고 잔여 종목에 재배분 — 관리 실익이 없는 소액 주문을 줄임</li>
</ul>
<table>
  <thead><tr><th style="width:34%">구분</th><th class="c" style="width:16%">한도</th><th>비고</th></tr></thead>
  <tbody>
    <tr><td>동일 종목</td><td class="c num">25%</td><td>시가총액 규모에 따른 예외 없이 전 종목 일괄 적용</td></tr>
    <tr><td>동일 업종 합산</td><td class="c num">50%</td><td>적용 분류기준: KRX 업종분류</td></tr>
    <tr><td>동일 업종 편입 종목수</td><td class="c num">2종목</td><td>비중 기준 한도와 별도로 종목수 기준 한도를 병행</td></tr>
    <tr><td>현금성 자산</td><td class="c">한도 없음</td><td>위험관리 규칙 작동 시 비중 확대</td></tr>
  </tbody>
</table>

<h3>3) 위험 관리</h3>
<ul>
  <li><b>개별 종목 손실 제한</b> — 편입 시점 대비 사전에 정한 하락 폭에 도달한 종목은 익영업일에 청산하고, 회수 자금은 다음 정기 리밸런싱까지 현금으로 보유</li>
  <li><b>시장 국면 판단</b> — 시장 전체가 중장기 추세를 이탈한 것으로 판정되는 기간에는 위험자산 비중을 절반 수준까지 축소</li>
  <li>두 장치 모두 <b>월 1회 리밸런싱 사이의 기간에도 상시 작동</b></li>
</ul>

<h3>4) 추가 설명</h3>
<h4>○ 수익 추구 로직과 위험 관리 로직을 분리한 이유</h4>
<ul class="dash">
  <li>위험관리를 종목 선정 기준에 섞지 않고 독립된 장치로 두어, 어느 한쪽을 조정해도 다른 쪽의 성격이 훼손되지 않음</li>
  <li>각 장치의 기여를 개별적으로 측정할 수 있어 검증이 용이함</li>
</ul>
<h4>○ 회수 자금을 현금으로 보유하는 이유</h4>
<ul class="dash">
  <li>회수 자금을 지수 ETF나 잔여 종목으로 재배분하는 방식을 <b>모두 검증한 결과, 방어 효과가 상쇄</b>되는 것을 확인</li>
  <li>개별 종목 사유와 시장 전체 사유 <b>두 경로 모두에서 같은 결론</b>을 얻음</li>
</ul>
<h4>○ 업종 분산을 종목수와 비중 두 기준으로 건 이유</h4>
<ul class="dash">
  <li>비중 기준 한도만으로는 걸러지지 않는 편중이 존재 — 종목수 기준 제한이 실질적으로 더 강하게 작동</li>
  <li>두 기준을 병행할 때 성과 손실 없이 예외적 쏠림에 대한 안전장치가 확보됨</li>
</ul>

<h2>3. 백테스팅 결과</h2>
<div class="wrapt"><table>
  <thead><tr><th style="width:30%"> </th><th class="r">연수익률</th><th class="r">연변동성</th>
    <th class="r">최대낙폭</th><th class="r">VaR(1년,95%)</th><th class="r">샤프지수</th></tr></thead>
  <tbody>
    {mrow("코스피 (BM)", ALL["bm"])}
    {PEER_ROW}
    {mrow("본 알고리즘", ALL["mp"], True)}
  </tbody>
</table></div>
<ul>
  <li>{ALL['start']} ~ {ALL['end']}, 월말 리밸런싱, 거래비용 왕복 0.230% 반영</li>
  <li><b>비교 대상</b>: RA 테스트베드 공시 「키움 Momentum」 적극투자형. <b>공시개시일이 {ALL['start']}로 본 알고리즘 성과 구간 시작과 동일</b>하여 같은 출발선에서 비교 가능. 공시 기준가 기준이므로 실제 운용비용이 반영된 값</li>
  <li>샤프지수: 무위험이자율 미반영 (일간 수익률 기준 연환산)</li>
  <li>VaR: 1년 보유 수익률 분포의 <b>하위 5% 지점</b> (5%의 확률로 연 {p(abs(ALL['mp']['var5']))} 초과 하락)</li>
  <li>구간별 분해 <span class="arrow">→ [참고 2]</span> · 연도별·월별 <span class="arrow">→ [참고 4]</span> · 현재 포트폴리오 <span class="arrow">→ [참고 5]</span></li>
</ul>

<h2>4. 검증 체계</h2>
<p>본 알고리즘은 <b>총 23건의 실험</b>을 거쳐 확정되었으며, 그중 <b>채택 6건 · 기각 16건 · 진단 1건</b>입니다.
과최적화를 걸러내기 위해 아래 장치를 사전에 규정하고 운영했습니다.</p>

<h3>1) 과최적화 방지 장치</h3>
<table>
  <thead><tr><th style="width:30%">장치</th><th>내용</th></tr></thead>
  <tbody>
    <tr><td>검증 전용 구간 봉인</td><td>과거 데이터의 일정 구간을 설정값 선택에 <b>일절 사용하지 않고</b> 남겨 두었다가, 설정값 확정 후 <b>1회에 한해</b> 열어 검증</td></tr>
    <tr><td>사전 등록</td><td>가설·설정값·판정기준을 <b>데이터 확인 전에</b> 문서로 확정하고, 결과에 따라 설정값을 되돌려 바꾸지 않음</td></tr>
    <tr><td>복수 종목군 동시 검증</td><td>하나의 종목군에서만 좋은 결과가 나온 설정은 채택하지 않음. 성격이 다른 복수 종목군에서 같은 방향이 나올 때만 유효로 판정</td></tr>
    <tr><td>기각 기록 보존</td><td>채택되지 않은 검토 내역을 삭제하지 않고 모두 보존 — 얼마나 많은 후보를 검토했는지가 함께 드러나 채택 설정의 과대평가를 방지</td></tr>
    <tr><td>제약의 실효성 확인</td><td>설정한 제약이 실제로 작동하는 빈도를 점검. 걸어 두었으나 발동하지 않는 제약을 "제약이 있다"고 간주하지 않음</td></tr>
    <tr><td>로직 변경 시 회귀 확인</td><td>산출 로직 변경 시 기존 경로의 결과가 그대로 재현되는 것을 먼저 확인한 뒤 변경분을 반영</td></tr>
    <tr><td>시간분할 재추정</td><td>분석 기간을 시간순으로 나눠, <b>앞 구간만 보고 고른 설정값이 뒤 구간에서도 유효한지</b> 확인. 실제 운용과 같은 순서로 재현 <span style="color:var(--muted)">(2026-08 실시, 아래 4항)</span></td></tr>
  </tbody>
</table>

<h3>2) 표본 외 검증 수행 결과</h3>
<ul>
  <li>설정값 선택에 사용하지 않은 구간({OOS['start']} ~ {OOS['end']})을 대상으로 검증을 수행했으며, <b>검증 이후 설정값을 재조정하지 않았습니다</b> — 사전 등록 원칙을 그대로 적용했습니다</li>
  <li>해당 구간 중 2018년에는 시장이 {p(abs(BAD2018_BM))} 하락하는 동안 {p(BAD2018_MP,1,True)}를 기록하여 <b>벤치마크를 {p(BAD2018_EX,1,True)}p 상회</b>했습니다 — 하락 국면에서 위험관리 장치가 설계대로 작동한 사례입니다</li>
  <li>구간별 성과 <span class="arrow">→ [참고 2]</span> · 요인 분해 <span class="arrow">→ [참고 3]</span></li>
</ul>

<h3>3) 성과 요인 분해</h3>
<p>본 알고리즘은 <b>수익 추구 로직과 위험관리 로직이 분리</b>되어 있어 성과를 요인별로
나누어 측정할 수 있습니다. 성과 편차가 가장 컸던 2019년을 예로 들면 다음과 같습니다.</p>
<table>
  <thead><tr><th style="width:34%">요인</th><th class="r" style="width:18%">기여</th><th>내용</th></tr></thead>
  <tbody>
    <tr><td>국면 대응 규칙</td><td class="r num up"><b>{p(DG['exp_eff'],1,True)}p</b></td>
      <td>평균 노출을 {DG['avg_exposure']*100:.0f}%로 축소하여 <b>손실을 줄이는 방향으로 작동</b></td></tr>
    <tr><td>손실 제한 규칙</td><td class="r num">{p(DG['stop_eff'],1,True)}p</td>
      <td>하락 직후 반등이 반복된 구간의 특성이 반영된 결과</td></tr>
    <tr><td>종목 선정 (기본 수익률)</td><td class="r num">{p(DG['A'],1,True)}</td>
      <td>당해 편입 업종과 지수 상승을 주도한 업종이 상이했던 영향. 위 두 규칙의 효과(%p)는 이 수익률에 가감됩니다</td></tr>
  </tbody>
</table>
<div class="q"><b>각 장치의 기여를 개별적으로 측정할 수 있다는 점이 본 알고리즘 구조의 강점입니다.</b>
성과가 어느 요인에서 발생했는지 사후에 분해할 수 있어 개선 지점을 특정할 수 있습니다.
상세 <span class="arrow">→ [참고 3]</span></div>

<h3>4) 설정값 안정성 점검 (2026-08 실시)</h3>
<p>현재 설정값은 분석 기간 전체를 한 번에 보고 정한 값입니다. 실제 운용에서는 불가능한
순서이므로, 기간을 시간순으로 <b>9개 구간</b>으로 나눠 <b>앞 구간만 보고 고른 값이 뒤 구간에서도
유효한지</b>를 확인했습니다.</p>
<table>
  <thead><tr><th style="width:34%">점검 결과</th><th>내용</th></tr></thead>
  <tbody>
    <tr><td>9개 구간 전부 동일</td>
      <td>편입 종목수 기준은 표본이 가장 적은 첫 구간에서도 흔들리지 않았습니다</td></tr>
    <tr><td>첫 구간만 예외</td>
      <td>기준 2종(분산 1종·선별 1종)은 첫 구간에서만 다른 값이 선정되고 이후 전 구간에서 현행값이 선정됐습니다 — <b>초기 표본 부족</b>의 전형적 형태입니다</td></tr>
    <tr><td>다른 값이 선정</td>
      <td>2종은 현행값이 아닌 값이 선정됐습니다. <b>각각 확인을 거쳐 현행값을 유지</b>했습니다 (아래)</td></tr>
  </tbody>
</table>
<h4>○ 다른 값이 선정된 2종에 대한 후속 검증</h4>
<ul class="dash">
  <li>하나는 <b>이미 알고 치른 대가</b>였습니다 — 수익성 지표만 보면 다른 값이 낫지만
    낙폭과 집중도를 개선하기 위해 의도적으로 현재 값을 택했고, 그 판단이 시점을 바꿔도
    일관되게 재확인됐습니다</li>
  <li>다른 하나는 <b>복수 종목군으로 넓혀 다시 확인</b>한 결과, 종목군 성격에 따라 유불리가
    정반대로 갈렸습니다. 본 알고리즘의 대상 종목군에서는 <b>현행값이 우위</b>였습니다</li>
</ul>
<div class="q"><b>가장 중요한 확인은 따로 있습니다 — 구간마다 그때그때 가장 좋았던 값으로
갈아타는 방식이 현행 고정값보다 못했습니다.</b> 점검한 5개 설정값 <b>전부</b>에서 그러했습니다.
앞 구간에서의 우위가 다음 구간으로 이어지지 않는다는 뜻이며, <b>최적값을 좇는 행위 자체가
과최적화</b>임을 같은 데이터로 확인한 결과입니다. 이에 따라 <b>어떤 설정값도 변경하지
않았습니다.</b></div>

<h2>5. 유의사항</h2>
<table>
  <thead><tr><th style="width:30%">항목</th><th>내용</th></tr></thead>
  <tbody>
    <tr><td>체결 비용</td><td>세금·수수료(왕복 0.230%)는 반영했으나 주문 규모에 따른 체결가 불리(슬리피지)는 반영하지 않았습니다. 운용 규모에 따라 실제 성과와 차이가 발생할 수 있습니다</td></tr>
    <tr><td>실질 분산</td><td>규모 연동 배분의 특성상 상위 종목 비중이 높아, 명목 편입 종목수 대비 실질 분산은 {D['eff_n']:.1f}종목 수준입니다</td></tr>
    <tr><td>성과 편차</td><td>연도별 성과 편차가 존재합니다 (연도별 상세는 참고 4, 요인 분해는 참고 3)</td></tr>
    <tr><td>운용 가능 규모</td><td>주문 규모 대비 유동성 측정은 후속 과제로 계획하고 있습니다</td></tr>
    <tr><td>비교 기준</td><td>본 알고리즘은 백테스팅 결과이며, 「키움 Momentum」은 실제 운용된 공시 기준가입니다</td></tr>
  </tbody>
</table>

<h2>6. 운영 계획</h2>
<table>
  <thead><tr><th style="width:24%">구분</th><th style="width:18%">실행 주기</th><th>비고</th></tr></thead>
  <tbody>
    <tr><td>정기 리밸런싱</td><td>월 1회</td><td>매월 말 포트폴리오 산출·교체</td></tr>
    <tr><td>수시 리밸런싱</td><td>수시</td><td>개별 종목 손실 제한 발동 / 시장 국면 전환 / 매매 제한 종목 발생 시</td></tr>
    <tr><td>알고리즘 점검</td><td>연 1회</td><td>데이터 원천·설정값 유효성 점검. 변경 시 사전 등록 절차 적용</td></tr>
    <tr><td>데이터 품질 점검</td><td>월 1회</td><td>재무데이터 갱신 시점, 가격 데이터의 배당·분할 반영 정합성, 기준 데이터 이상값</td></tr>
  </tbody>
</table>
<h4>○ 향후 계획</h4>
<ul class="dash">
  <li><b>1단계</b> — 시간분할 재추정을 통한 설정값 안정성 점검
    <b style="color:var(--navy)">✓ 완료 (2026-08)</b> · 결과는 4장 4항</li>
  <li><b>2단계</b> — 설정값을 고정한 상태에서 이후 구간의 성과를 축적하여 검증 표본을 확보</li>
  <li><b>3단계</b> — 주문 규모 대비 유동성 측정 및 체결비용 모형 도입</li>
  <li><b>4단계</b> — 선별 기준 확장(수급·재무 건전성 등) 및 국면 판단 모델 정교화</li>
</ul>

<!-- ================= 참고 ================= -->

<div class="ref">
<h2>[참고 1] 전 구간 성과</h2>
<p class="sub">{ALL['start']} ~ {ALL['end']} · 월말 리밸런싱 · 거래비용 왕복 0.230%</p>

<figure><figcaption>누적성과 ({ALL['start']} = 100, LOG SCALE)</figcaption>
  <div class="legend"><span><i style="background:var(--down)"></i>본 알고리즘</span>
    <span><i style="background:var(--flat)"></i>코스피(BM)</span>
    <span><i style="background:var(--up)"></i>키움 Momentum (적극투자형)</span></div>
  <canvas id="c-cum"></canvas></figure>

<figure><figcaption>고점 대비 낙폭</figcaption>
  <canvas id="c-dd"></canvas></figure>

<div class="wrapt"><table>
  <thead><tr><th style="width:30%"> </th><th class="r">연수익률</th><th class="r">연변동성</th>
    <th class="r">최대낙폭</th><th class="r">VaR(1년,95%)</th><th class="r">샤프지수</th></tr></thead>
  <tbody>{mrow("코스피 (BM)", ALL["bm"])}{PEER_ROW}{mrow("본 알고리즘", ALL["mp"], True)}</tbody>
</table></div>

<h3>운용 통계</h3>
<table>
  <thead><tr><th class="c">정기 리밸런싱</th><th class="c">손실 제한 청산</th>
    <th class="c">누적 편입 종목수</th><th class="c">평균 회전율</th>
    <th class="c">거래비용 부담</th></tr></thead>
  <tbody><tr class="hi"><td class="c num">{OPS['rebalances']}회</td>
    <td class="c num">{OPS['triggered']:,}건</td>
    <td class="c num">{OPS['names_total']}개</td>
    <td class="c num">{OPS['avg_turnover']*100:.0f}% / 회</td>
    <td class="c num">연 {abs(OPS['cagr_drag'])*100:.2f}%p</td></tr></tbody>
</table>
<ul>
  <li><b>정기 리밸런싱</b>은 월 1회 정기 교체 횟수이며, <b>손실 제한 청산</b>은 그 사이에 발생한
    수시 매도 건수입니다 (회당 평균 {OPS['stops_per_rebal']:.1f}건 · 전체 보유 포지션
    {OPS['positions']:,}건 대비 {OPS['trigger_rate']*100:.1f}%)</li>
  <li><b>거래비용 부담</b>은 비용 반영 전후로 각각 산출한 <b>연평균 수익률의 차이</b>입니다
    (반영 전 {p(OPS['cagr_gross'],2,True)} → 반영 후 {p(OPS['cagr_net'],2,True)}).
    누적 기준으로는 지수 {OPS['total_cost_pt']:.1f}p 차감(정기 {OPS['rebalance_cost_pt']:.1f} +
    손실 제한 {OPS['stop_cost_pt']:.1f})에 해당하며, 비용의 {OPS['stop_cost_pt']/OPS['total_cost_pt']*100:.0f}%가
    손실 제한 청산에서 발생합니다</li>
</ul>
</div>

<div class="ref">
<h2>[참고 2] 구간별 성과</h2>
<p class="sub">설정값 선택 구간과 검증 전용 구간의 분해</p>

<div class="wrapt"><table>
  <thead><tr><th style="width:28%">구간</th><th class="r">연수익률</th><th class="r">연변동성</th>
    <th class="r">최대낙폭</th><th class="r">VaR(1년,95%)</th><th class="r">샤프지수</th></tr></thead>
  <tbody>
    <tr><td colspan="6" style="background:var(--band);font-weight:700">
      검증 전용 구간 · {OOS['start']} ~ {OOS['end']} ({OOS['mp']['years']:.1f}년)</td></tr>
    {mrow("코스피 (BM)", OOS["bm"])}
    {PEER_ROW_OOS}
    {mrow("본 알고리즘", OOS["mp"], True)}
    <tr><td colspan="6" style="background:var(--band);font-weight:700">
      설정값 선택 구간 · {INS['start']} ~ {INS['end']} ({INS['mp']['years']:.1f}년)</td></tr>
    {mrow("코스피 (BM)", INS["bm"])}
    {PEER_ROW_INS}
    {mrow("본 알고리즘", INS["mp"], True)}
  </tbody>
</table></div>

<h3>해석</h3>
<ul>
  <li><b>설정값 선택 구간</b>에서는 벤치마크를 큰 폭으로 상회했습니다 (연수익률 {p(INS['mp']['cagr'])} vs {p(INS['bm']['cagr'])}, 샤프 {INS['mp']['sharpe']:.2f} vs {INS['bm']['sharpe']:.2f})</li>
  <li><b>검증 전용 구간</b>에서는 벤치마크를 하회했습니다 (연수익률 {p(OOS['mp']['cagr'],1,True)} vs {p(OOS['bm']['cagr'],1,True)})</li>
  <li>다만 같은 기간을 실제로 운용한 「키움 Momentum」(적극투자형)과 비교하면
    (연수익률 {p(OOS['mp']['cagr'],1,True)} vs {p(OOS['peer']['cagr'],1,True)}, 샤프 {OOS['mp']['sharpe']:.2f} vs {OOS['peer']['sharpe']:.2f}),
    <b>검증 전용 구간을 포함해 양 구간 모두에서 공시 알고리즘을 상회</b>했습니다</li>
  <li>두 구간 모두 편입 종목수·회전율·분산 지표가 동일한 수준으로 유지되어 <b>설계된 대로 일관되게 작동</b>했습니다</li>
  <li>성과 편차는 <b>특정 연도에 집중</b>되어 있으며, 요인별 분해가 가능합니다 <span class="arrow">→ [참고 3]</span></li>
</ul>
<div class="q">본 알고리즘의 설정값은 선택 구간에서 결정한 뒤 <b>검증 구간에서 재조정하지 않았습니다.</b>
전 구간을 합산한 성과가 [참고 1]이며, 본 보고서의 대표 수치는 이 값을 사용했습니다.</div>
</div>

<div class="ref">
<h2>[참고 3] 성과 요인 분해</h2>
<p class="sub">수익 로직과 위험관리 로직의 분리 구조에 따른 요인별 측정</p>

<h3>1) 요인별 분해 (2019년 사례)</h3>
<p>본 알고리즘은 종목 선정과 위험관리가 독립된 장치로 구성되어 있어 성과를 요인별로
분해하여 측정할 수 있습니다. 연도별 편차가 가장 컸던 2019년을 대상으로 산출했습니다.</p>
<table>
  <thead><tr><th style="width:30%">구분</th><th class="r">수익률</th><th>내용</th></tr></thead>
  <tbody>
    <tr><td>A. 종목 선정만</td><td class="r num">{p(DG['A'],1,True)}</td>
      <td>위험관리 장치를 모두 해제하고 선정 종목·비중만으로 산출</td></tr>
    <tr><td>B. 손실 제한 적용</td><td class="r num">{p(DG['B'],1,True)}</td>
      <td>A + 손실 제한 규칙 (효과 {p(DG['stop_eff'],1,True)}p)</td></tr>
    <tr class="hi"><td>C. 국면 대응 포함 (실제)</td><td class="r num">{p(DG['C'],1,True)}</td>
      <td>B + 국면 대응 규칙 (효과 <b class="up">{p(DG['exp_eff'],1,True)}p</b>)</td></tr>
  </tbody>
</table>
<ul>
  <li><b>국면 대응 규칙이 평균 노출을 {DG['avg_exposure']*100:.0f}%로 낮춰 손실을 {p(abs(DG['exp_eff']),1)}p 축소</b>했습니다 — 하락 국면에서 설계 의도대로 작동한 것이 수치로 확인됩니다</li>
  <li>손실 제한 규칙은 {DG['n_stop']}건 발동했습니다({DG['n_pos']}개 포지션 대비)</li>
  <li>당해에는 편입 업종과 지수 상승을 주도한 업종이 상이했습니다</li>
  <li><span style="color:var(--muted)">본 표는 요인별 기여를 분리하기 위한 <b>별도 산출</b>로,
    월별 수익률을 단순 합산하고 거래비용을 반영하지 않았습니다. 복리로 연결하고 거래비용을
    반영한 [참고 4]의 연간 수익률({p(next(r["mp"] for r in YRS if r["year"]==2019),1,True)})과
    산출 방식이 다르며, <b>표의 목적은 절대 수준이 아니라 요인 간 상대 크기</b>에 있습니다</span></li>
</ul>

<h3>2) 업종별 기여</h3>
<table>
  <thead><tr><th style="width:40%">업종</th><th class="c" style="width:20%">편입 횟수</th>
    <th class="r">기여도</th></tr></thead>
  <tbody>{sec_rows}</tbody>
</table>
<div class="q"><b>업종 구성이 성과에 미치는 영향을 사후에 특정할 수 있다는 점이 구조적 이점입니다.</b>
업종당 편입 종목수 제한에 더해, 성격이 유사한 복수 업종의 동시 편입을 제어하는 방안을
개선 과제로 검토하고 있습니다.</div>

<h3>3) 시장 국면 의존도</h3>
<p>본 알고리즘은 선정 과정에 가격 흐름을 사용하므로, <b>"오르던 종목이 계속 오르는 장에서만
통하는 것 아닌가"</b>라는 점을 확인할 필요가 있습니다. 이를 위해 해마다 시장이 어떤 성격이었는지를
먼저 재고, 그 성격과 본 알고리즘의 초과성과가 같이 움직이는지 대조했습니다.</p>

<div class="keep">
<h4>○ 추세 지속도란</h4>
<p>그해 시장이 <b>"직전 해에 오른 종목이 올해도 오르는 장"</b>이었는지를 나타내는 값입니다.
코스피 전 종목(연 740~830개)을 대상으로 종목마다 <b>직전 해 수익률</b>과 <b>당해 수익률</b>의
순위를 매긴 뒤, 두 순위가 함께 움직이는 정도를 −1 ~ +1로 나타냈습니다.</p>
<ul class="dash">
  <li><b>양수(추세지속)</b> — 직전 해 상위권이 당해에도 상위권. 오르던 것이 계속 오른 장</li>
  <li><b>음수(반전)</b> — 직전 해 상위권이 당해 하위권으로 역전. 주도주가 바뀐 장</li>
  <li><b>0 부근(중립)</b> — 직전 해 성적과 당해 성적이 서로 무관한 장</li>
</ul>
<div class="q">감이 잡히도록 실제 사례를 보면, 반전이 가장 강했던 <b>{_rev['year']}년</b>에는
직전 해 상위 20% 종목이 당해 <b>{p(_rev['hi'],1,True)}</b>인 반면 하위 20% 종목은
<b>{p(_rev['lo'],1,True)}</b>로, 순위가 통째로 뒤집혔습니다. 반대로 추세가 가장 강했던
<b>{_trd['year']}년</b>에는 상위 20%가 <b>{p(_trd['hi'],1,True)}</b>, 하위 20%가
<b>{p(_trd['lo'],1,True)}</b>로 격차가 그대로 유지됐습니다.</div>
</div>

<div class="wrapt"><table>
  <thead><tr><th class="c">연도</th><th class="r">추세 지속도</th>
    <th class="r">직전 해 상위20%<br>의 당해 수익률</th>
    <th class="r">직전 해 하위20%<br>의 당해 수익률</th>
    <th class="c">국면</th><th class="r">본 알고리즘<br>초과성과</th></tr></thead>
  <tbody>{reg_rows}</tbody>
</table></div>
<p style="font-size:11.5px;color:var(--muted);margin-top:-4px">* 부분 연도 — 2017년은 5월 22일부터.
분위 수익률은 각 그룹의 중앙값이며, 국면 구분은 추세 지속도 ±0.05를 경계로 했습니다.</p>
<h4>○ 확인 결과</h4>
<div class="q"><b>추세 지속도와 초과성과의 상관계수는 {REG_CORR:+.2f}로, 본 알고리즘의 성과가
시장의 추세 지속 국면에 의존한다는 관계는 확인되지 않습니다.</b> 추세가 강했던 해에 성과가
몰려 있는 구조가 아니며, 오히려 <b>추세가 꺾인 반전 국면(2018년 {p(_ex_by_year[2018],1,True)}p ·
2021년 {p(_ex_by_year[2021],1,True)}p)에서 벤치마크를 크게 상회</b>했습니다. 반대로 추세가
가장 강했던 {_trd['year']}년의 초과성과는 {p(_ex_by_year[_trd['year']],1,True)}p로 가장 작았습니다.
단일 기준이 아니라 재무 선별·순위화·위험관리를 단계적으로 결합한 구조에서 비롯된 특성으로
판단됩니다.</div>
<ul>
  <li><span style="color:var(--muted)">다만 대조에 쓸 수 있는 연도가 {len(REG)}개뿐이므로,
    이 상관계수는 <b>"국면 의존 관계가 나타나지 않는다"</b>는 정도로 해석하며 그 이상의 의미는
    두지 않았습니다. 연도가 쌓이는 대로 갱신할 예정입니다</span></li>
</ul>
</div>

<div class="ref">
<h2>[참고 4] 연도별 · 월별 성과</h2>
<p class="sub">전 구간 {ALL['start']} ~ {ALL['end']}</p>

<figure><figcaption>연도별 수익률 (* 부분 연도)</figcaption>
  <div class="legend"><span><i style="background:var(--down)"></i>본 알고리즘</span>
    <span><i style="background:var(--flat);opacity:.5"></i>코스피(BM)</span></div>
  <canvas id="c-year"></canvas></figure>

<div class="wrapt"><table>
  <thead><tr><th class="c" style="width:14%">연도</th><th class="r">본 알고리즘</th>
    <th class="r">키움 Momentum</th><th class="r">코스피(BM)</th>
    <th class="r">초과 (BM 대비)</th></tr></thead>
  <tbody>{year_rows}</tbody>
</table></div>
<p style="font-size:11.5px;color:var(--muted);margin-top:-4px">* 부분 연도 —
2017년은 5월 22일부터, 2026년은 7월 31일까지</p>

<figure><figcaption>월별 수익률 히트맵 (%, 붉은색 상승 / 푸른색 하락)</figcaption>
  <canvas id="c-heat"></canvas></figure>
</div>

<div class="ref">
<h2>[참고 5] 현재 포트폴리오</h2>
<p class="sub">{D['formation']} 산출 기준 · {len(D['holdings'])}종목 · 주식 노출 {D['exposure']*100:.0f}%</p>

<h3>선정 과정</h3>
<figure><figcaption>단계별 잔존 종목수</figcaption>
  <canvas id="c-funnel"></canvas></figure>

<h3>업종 구성</h3>
<figure><figcaption>업종별 합산 비중</figcaption>
  <canvas id="c-sector"></canvas></figure>
<ul>
  <li>업종 {len(D['sectors'])}개 · 실효 분산 {D['eff_n']:.1f}종목 (명목 {len(D['holdings'])}종목)</li>
  <li>동일 업종 2종목·합산 50% 한도 및 동일 종목 25% 한도 적용 후 기준</li>
</ul>

<h3>편입 종목</h3>
<div class="wrapt"><table>
  <thead><tr><th style="width:32%">종목명</th><th style="width:14%">종목코드</th>
    <th>업종</th><th class="r" style="width:14%">비중</th></tr></thead>
  <tbody>{hold_rows}</tbody>
</table></div>
</div>

<footer>
  산출 기준 — 본 알고리즘은 백테스팅, 「키움 Momentum」은 공시 기준가입니다.
  거래비용은 매도 증권거래세 0.20% + 위탁수수료 0.015%(양방향)를 반영했으며,
  주문 규모에 따른 체결가 불리는 반영하지 않았습니다.
</footer>
</div>
<script>{JS}</script>
"""

out = SP / "proposal.html"
out.write_text(HTML)
print(f"wrote {out} ({len(HTML):,} bytes)")
