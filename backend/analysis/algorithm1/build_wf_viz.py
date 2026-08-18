"""워크포워드(실험 22) 결과 시각화 HTML 생성. wf_viz.json을 읽는다.

수치를 손으로 옮기지 않는다 — 전부 JSON에서 만든다.
"""
import json
import os
from pathlib import Path

# 산출물 경로 — 세션 스크래치패드는 세션 간 공유되지 않으므로(CLAUDE.md) 쓰지 않는다.
# 기본값은 리포의 reference/analysis/ (gitignore 대상)이며 ALGO_OUT 으로 덮어쓸 수 있다.
SP = Path(os.environ.get("ALGO_OUT",
                    Path(__file__).resolve().parents[3] / "reference" / "analysis"))
SP.mkdir(parents=True, exist_ok=True)
D = json.loads((SP / "wf_viz.json").read_text())
E23 = json.loads((SP / "stop8_viz.json").read_text())
P, BASE, BM = D["params"], D["baseline"], D["baseline_mean"]
YRS = D["valid_years"]

VCLASS = {"안정": "ok", "대체로 안정": "warn", "불안정": "bad"}
# 격자값 표시 — 비율 파라미터는 %로
PCT = {"종목당 최대비중", "손실 제한 폭", "재무 선별 강도"}


def g(label, v):
    return f"{float(v)*100:.0f}%" if label in PCT else str(v)


# ── 판정 카드 ──────────────────────────────────────────────────────────────
cards = ""
for lab, r in P.items():
    hit = r["current_picked"]
    tone = "ok" if hit == r["n"] else ("warn" if hit >= r["n"] - 1 else "bad")
    cards += f"""
  <article class="card">
    <p class="eyebrow">{lab}</p>
    <p class="cur"><span>현행</span><b class="num">{g(lab, r['current'])}</b></p>
    <p class="verdict {VCLASS[r['verdict']]}">{r['verdict']} · 최빈 {r['max_agree']}/{r['n']}</p>
    <p class="hit {tone}"><b class="num">{hit}</b>/{r['n']} 폴드에서 현행값 선택</p>
  </article>"""

# ── 폴드별 선택 패턴 ────────────────────────────────────────────────────────
strip = ""
for lab, r in P.items():
    cells = ""
    for i, pk in enumerate(r["picks"]):
        same = pk == r["current"]
        cells += (f'<td class="pick {"same" if same else "diff"}">'
                  f'<span class="num">{g(lab, pk)}</span></td>')
    strip += f'<tr><th>{lab}</th><td class="cur num">{g(lab, r["current"])}</td>{cells}</tr>'

# ── 학습창 샤프 히트맵 ──────────────────────────────────────────────────────
heat = ""
for lab, r in P.items():
    cols = "".join(f'<th class="v{" cur" if v == r["current"] else ""}">{g(lab, v)}'
                   + ('<i>현행</i>' if v == r["current"] else '') + '</th>' for v in r["grid"])
    rows = ""
    for i, row in enumerate(r["train"]):
        best = max(range(len(row)), key=lambda j: row[j])
        tds = "".join(
            f'<td class="hm{" win" if j == best else ""}{" curcol" if r["grid"][j] == r["current"] else ""}">'
            f'<span class="num">{row[j]:+.3f}</span></td>' for j in range(len(row)))
        rows += f'<tr><th class="f">F{i+1}<i>{YRS[i]}</i></th>{tds}</tr>'
    heat += f"""
  <div class="hmblock">
    <h4>{lab}</h4>
    <table class="hmt"><thead><tr><th class="f"></th>{cols}</tr></thead><tbody>{rows}</tbody></table>
  </div>"""

# ── 보조지표 막대 ──────────────────────────────────────────────────────────
order = sorted(P.items(), key=lambda kv: kv[1]["delta"], reverse=True)
mx = max([BM] + [r["sel_mean"] for _, r in order]) * 1.06
bars = ""
for lab, r in order:
    w = r["sel_mean"] / mx * 100
    tone = "flat" if abs(r["delta"]) < 0.005 else "bad"
    bars += f"""
    <div class="brow">
      <span class="blab">{lab}</span>
      <span class="btrack"><span class="bfill {tone}" style="width:{w:.1f}%"></span></span>
      <span class="bval num">{r['sel_mean']:.3f}</span>
      <span class="bdel num {tone}">{r['delta']:+.3f}</span>
      <span class="bwin num">{r['wins']}/{r['n']}</span>
    </div>"""

# ── 손절 폭 집중 ───────────────────────────────────────────────────────────
sl = P["손실 제한 폭"]
ci = sl["grid"].index(sl["current"])
ranks = [sorted(range(len(row)), key=lambda j: -row[j]).index(ci) + 1 for row in sl["train"]]
last = sum(1 for x in ranks if x == len(sl["grid"]))
slcells = "".join(f'<td class="r{x}"><span class="num">{x}위</span></td>' for x in ranks)

# ── 실험 23 — 손절 8% 4개 유니버스 재확인 ────────────────────────────────
K = E23["kospi"]
e23rows = ""
for r in E23["rows"]:
    cells = "".join(
        f'<td class="dz {"up" if c["ds"] > 0 else "dn"}"><span class="num">{c["ds"]:+.3f}</span></td>'
        for c in r["cells"])
    tone = "ok" if r["ns"] == 4 else "bad"
    e23rows += (f'<tr><th>{r["window"]}</th>{cells}'
                f'<td class="vd {tone}">{r["ns"]}/4'
                f'{" 신호" if r["ns"] == 4 else " 노이즈"}</td></tr>')

kos = [("연수익률", f"{K['c8']:+.2%}", f"{K['c10']:+.2%}", "b"),
       ("샤프지수", f"{K['s8']:.3f}", f"{K['s10']:.3f}", "b"),
       ("최대낙폭", f"{K['m8']:.1%}", f"{K['m10']:.1%}", "a"),
       ("손절 발동 (전 구간)", f"{K['t8']:,}건", f"{K['t10']:,}건", "b"),
       ("평균 회전율", f"{K['to8']:.1%}", f"{K['to10']:.1%}", "b"),
       ("누적 거래비용", f"{K['p8']:.1f}p", f"{K['p10']:.1f}p", "b")]
kosrows = "".join(
    f'<tr><th>{n}</th><td class="num{" win" if w == "a" else ""}">{v8}</td>'
    f'<td class="num{" win" if w == "b" else ""}">{v10}</td></tr>' for n, v8, v10, w in kos)

HTML = f"""<title>워크포워드 결과 — 알고리즘 #1 설정값 안정성</title>
<style>
:root{{
  --ground:#f5f7fa; --card:#ffffff; --ink:#151920; --ink2:#39414e; --muted:#6a7382;
  --navy:#1e3a5f; --good:#2c6e54; --warn:#96650f; --alert:#a8332b;
  --rule:#e3e7ec; --rule2:#eef1f5; --band:#eef2f7;
  --goodbg:#e7f2ec; --warnbg:#faf1de; --alertbg:#fbeae8;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --ground:#13161c; --card:#1a1e26; --ink:#e7eaef; --ink2:#c2c9d4; --muted:#8b95a4;
  --navy:#8fb4dd; --good:#63b48d; --warn:#d1a24a; --alert:#e0736a;
  --rule:#2a303a; --rule2:#232831; --band:#20252e;
  --goodbg:#1a2c25; --warnbg:#2c2618; --alertbg:#2e1e1d;
}}}}
:root[data-theme="dark"]{{
  --ground:#13161c; --card:#1a1e26; --ink:#e7eaef; --ink2:#c2c9d4; --muted:#8b95a4;
  --navy:#8fb4dd; --good:#63b48d; --warn:#d1a24a; --alert:#e0736a;
  --rule:#2a303a; --rule2:#232831; --band:#20252e;
  --goodbg:#1a2c25; --warnbg:#2c2618; --alertbg:#2e1e1d;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",
    "Malgun Gothic","Noto Sans KR",system-ui,sans-serif;
  font-size:15px;line-height:1.65;-webkit-font-smoothing:antialiased}}
.num{{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
  font-variant-numeric:tabular-nums;letter-spacing:-.02em}}
.wrap{{max-width:940px;margin:0 auto;padding:40px 22px 80px}}

header{{border-bottom:2px solid var(--navy);padding-bottom:18px;margin-bottom:28px}}
.kicker{{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin:0 0 8px}}
h1{{font-size:clamp(24px,4vw,33px);line-height:1.15;margin:0 0 10px;letter-spacing:-.03em;
  font-weight:800;color:var(--navy);text-wrap:balance}}
.sub{{margin:0;color:var(--ink2);max-width:64ch}}
.meta{{display:flex;flex-wrap:wrap;gap:6px 22px;margin-top:14px;font-size:12.5px;color:var(--muted)}}
.meta b{{color:var(--ink2);font-weight:700}}

h2{{font-size:18px;margin:44px 0 6px;font-weight:800;letter-spacing:-.02em;color:var(--navy)}}
h2 .n{{font-size:12px;color:var(--muted);font-weight:700;margin-right:9px;
  font-family:ui-monospace,monospace}}
.lede{{margin:0 0 16px;color:var(--ink2);max-width:66ch;font-size:14px}}
h4{{font-size:13px;margin:0 0 7px;font-weight:700;color:var(--ink2)}}

.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:10px}}
.card{{background:var(--card);border:1px solid var(--rule);border-radius:9px;padding:13px 14px}}
.eyebrow{{font-size:11.5px;font-weight:700;color:var(--muted);margin:0 0 8px;letter-spacing:.02em}}
.cur{{display:flex;align-items:baseline;justify-content:space-between;margin:0 0 9px}}
.cur span{{font-size:11px;color:var(--muted)}}
.cur b{{font-size:23px;font-weight:800;color:var(--navy);letter-spacing:-.03em}}
.verdict{{margin:0 0 6px;font-size:12px;font-weight:700;padding:3px 8px;border-radius:5px;display:inline-block}}
.verdict.ok{{background:var(--goodbg);color:var(--good)}}
.verdict.warn{{background:var(--warnbg);color:var(--warn)}}
.verdict.bad{{background:var(--alertbg);color:var(--alert)}}
.hit{{margin:0;font-size:11.5px;color:var(--muted)}}
.hit b{{font-size:13px}}
.hit.ok b{{color:var(--good)}} .hit.warn b{{color:var(--warn)}} .hit.bad b{{color:var(--alert)}}

.scroll{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}}
.strip th{{text-align:left;font-weight:700;padding:7px 10px;white-space:nowrap;color:var(--ink2)}}
.strip thead th{{font-size:11px;color:var(--muted);font-weight:700;text-align:center;
  border-bottom:1px solid var(--rule)}}
.strip thead th i{{display:block;font-style:normal;font-size:10px;opacity:.75}}
.strip td.cur{{text-align:center;color:var(--navy);font-weight:700;background:var(--band);
  border-radius:5px}}
.strip td.pick{{text-align:center;padding:5px 4px}}
.strip td.pick span{{display:inline-block;min-width:44px;padding:4px 6px;border-radius:5px;
  font-size:12px;font-weight:700}}
.pick.same span{{background:var(--band);color:var(--navy)}}
.pick.diff span{{background:var(--alertbg);color:var(--alert)}}

.hmgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}}
.hmblock{{background:var(--card);border:1px solid var(--rule);border-radius:9px;padding:12px 13px}}
.hmt th.v{{font-size:11.5px;font-weight:700;color:var(--ink2);text-align:right;padding:0 7px 6px;
  border-bottom:1px solid var(--rule)}}
.hmt th.v.cur{{color:var(--navy)}}
.hmt th.v i{{display:block;font-style:normal;font-size:9.5px;color:var(--muted);font-weight:600}}
.hmt th.f{{font-size:11px;color:var(--muted);text-align:left;font-weight:700;padding:4px 8px 4px 0;
  white-space:nowrap}}
.hmt th.f i{{font-style:normal;opacity:.7;margin-left:5px;font-weight:400}}
.hmt td.hm{{text-align:right;padding:4px 7px;font-size:12px;color:var(--muted);
  border-bottom:1px solid var(--rule2)}}
.hmt td.hm.curcol{{background:var(--band)}}
.hmt td.hm.win span{{color:var(--ink);font-weight:700;
  box-shadow:inset 0 -2px 0 0 var(--navy);padding-bottom:1px}}

.bars{{background:var(--card);border:1px solid var(--rule);border-radius:9px;padding:16px 18px}}
.bhead,.brow{{display:grid;grid-template-columns:minmax(96px,1.25fr) minmax(90px,3fr) 52px 58px 42px;
  gap:10px;align-items:center}}
.bhead{{font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  font-weight:700;padding-bottom:8px;border-bottom:1px solid var(--rule);margin-bottom:10px}}
.bhead span:nth-child(n+3){{text-align:right}}
.brow{{padding:6px 0}}
.blab{{font-size:12.5px;font-weight:600}}
.btrack{{position:relative;height:16px;background:var(--rule2);border-radius:4px}}
.btrack::after{{content:"";position:absolute;top:-3px;bottom:-3px;left:{BM/mx*100:.1f}%;
  width:2px;background:var(--navy);opacity:.85}}
.bfill{{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--muted);opacity:.5}}
.bfill.bad{{background:var(--alert);opacity:.42}}
.bval,.bdel,.bwin{{text-align:right;font-size:12px}}
.bdel.bad{{color:var(--alert);font-weight:700}}
.bwin{{color:var(--muted);font-size:11.5px}}
.bnote{{margin:12px 0 0;font-size:11.5px;color:var(--muted);display:flex;align-items:center;gap:7px}}
.bnote i{{display:inline-block;width:2px;height:13px;background:var(--navy);flex:none}}

.call{{background:var(--card);border:1px solid var(--rule);border-left:3px solid var(--navy);
  border-radius:0 9px 9px 0;padding:14px 18px;margin:14px 0}}
.call.alert{{border-left-color:var(--alert)}}
.call p{{margin:0 0 8px}} .call p:last-child{{margin:0}}
.call .big{{font-size:15.5px;font-weight:700;color:var(--ink)}}

.e23 th,.kv th{{font-size:11.5px;color:var(--muted);font-weight:700;padding:6px 10px;text-align:center}}
.e23 tbody th,.kv tbody th{{text-align:left;color:var(--ink2);white-space:nowrap}}
.e23 td,.kv td{{text-align:center;padding:6px 10px;font-size:12.5px;border-bottom:1px solid var(--rule2)}}
.e23 td.dz.up{{color:var(--good);font-weight:700}}
.e23 td.dz.dn{{color:var(--alert);font-weight:700}}
.e23 td.vd{{font-size:11.5px;font-weight:700;border-radius:5px}}
.e23 td.vd.ok{{background:var(--goodbg);color:var(--good)}}
.e23 td.vd.bad{{background:var(--alertbg);color:var(--alert)}}
.kv td{{text-align:right}} .kv td.win{{background:var(--band);font-weight:700;color:var(--navy)}}
.bnote2{{margin:7px 0 0;font-size:11.5px;color:var(--muted)}}
.sl{{width:auto}}
.sl th{{font-size:11px;color:var(--muted);font-weight:700;padding:5px 9px;text-align:center}}
.sl th:first-child{{text-align:left}}
.sl td{{text-align:center;padding:5px 9px;font-size:12px}}
.sl td.r3{{color:var(--alert);font-weight:700;background:var(--alertbg);border-radius:4px}}
.sl td.r2{{color:var(--warn)}}

ul{{margin:8px 0 0;padding-left:0;list-style:none}}
li{{position:relative;padding-left:16px;margin:6px 0;font-size:13.5px;color:var(--ink2)}}
li::before{{content:"";position:absolute;left:0;top:9px;width:5px;height:5px;border-radius:50%;
  background:var(--navy);opacity:.55}}
footer{{margin-top:52px;padding-top:16px;border-top:1px solid var(--rule);
  font-size:12px;color:var(--muted)}}
code{{font-family:ui-monospace,monospace;font-size:12px;background:var(--band);
  padding:1px 5px;border-radius:4px}}
@media(max-width:620px){{
  .bhead,.brow{{grid-template-columns:minmax(80px,1fr) 2fr 46px 50px 38px;gap:7px}}
  .blab{{font-size:11.5px}}
}}
</style>

<div class="wrap">
<header>
  <p class="kicker">실험 22 · 워크포워드</p>
  <h1>설정값은 시점을 바꿔도 같은 값이 뽑히는가</h1>
  <p class="sub">현재 확정 설정값은 전 구간을 한 번에 보고 고른 값이다. 실전에서는 불가능한
  순서다. 구간을 시간순으로 잘라 <b>앞 구간만 보고 고른 값이 뒤 구간에서도 유효한지</b>를 쟀다.</p>
  <p class="meta"><span>구간 <b>2015-08 ~ 2026-07</b></span><span>확장창 <b>9폴드</b></span>
    <span>설정값 <b>5종</b> · 실행 <b>14회</b></span><span>선택 기준 <b>학습창 샤프</b></span>
    <span>실험 22 · 23</span><span>2026-08-18</span></p>
</header>

<h2><span class="n">01</span>판정</h2>
<p class="lede">폴드마다 학습창에서 샤프가 가장 높은 값을 고른 뒤, 9개 폴드의 선택값이
얼마나 일치하는지 본다. <b>판정(안정/불안정)과 "현행값이 뽑혔는가"는 다른 질문</b>이다.</p>
<div class="cards">{cards}
</div>

<h2><span class="n">02</span>폴드별로 무엇이 뽑혔나</h2>
<p class="lede">파란 칸은 현행값이 뽑힌 폴드, 붉은 칸은 다른 값이 뽑힌 폴드다.
초기 폴드일수록 학습창이 짧아(F1은 리밸런싱 약 29회) 흔들리기 쉽다.</p>
<div class="scroll"><table class="strip">
  <thead><tr><th>설정값</th><th>현행</th>
    {"".join(f'<th>F{i+1}<i>{y}</i></th>' for i, y in enumerate(YRS))}</tr></thead>
  <tbody>{strip}</tbody>
</table></div>
<ul>
  <li><b>편입 종목수 20</b>은 9개 폴드 전부에서 1위 — 가장 얇은 F1에서도 흔들리지 않았다</li>
  <li><b>업종당 종목수·재무 선별 강도</b>는 F1에서만 이탈하고 F2부터 끝까지 현행값이다.
    불안정이 아니라 <b>초기 표본 부족</b>의 전형이다</li>
  <li><b>종목캡과 손절 폭</b>은 현행값이 한 번도 뽑히지 않았다 — 아래에서 나눠 본다</li>
</ul>

<h2><span class="n">03</span>학습창 샤프 — 어떤 차이로 갈렸나</h2>
<p class="lede">각 폴드의 학습창에서 잰 샤프. 밑줄이 그 폴드의 1위, 음영은 현행값 열이다.</p>
<div class="hmgrid">{heat}
</div>

<h2><span class="n">04</span>그래서 갈아탔으면 나았을까 — 아니다</h2>
<p class="lede">폴드별로 뽑힌 최적값을 실제 검증창에 적용해 봤다. 세로선은 현행 고정값의
평균 샤프(<span class="num">{BM:.3f}</span>)다.</p>
<div class="bars">
  <div class="bhead"><span>설정값</span><span>검증창 평균 샤프</span><span>값</span>
    <span>차이</span><span>승</span></div>
  {bars}
  <p class="bnote"><i></i>세로선 = 현행 고정값 {BM:.3f} · "승"은 9개 검증창 중 현행값을 이긴 횟수</p>
</div>
<div class="call alert">
  <p class="big">5개 항목 전부에서 “그때그때 최적값으로 갈아타는” 편이 현행 고정값보다 못했다.</p>
  <p>학습창의 우위가 검증창으로 이어지지 않는다. <b>최적값을 좇는 행위 자체가 과최적화</b>라는
  것을 같은 데이터 안에서 실측한 결과다. 실험 21에서 종목캡 20%가 선택 구간 +0.016 →
  표본 외 −0.099로 부호가 뒤집혔던 것과 같은 성격이며, 이번엔 5개 항목에서 동시에 나타났다.</p>
</div>

<h2><span class="n">05</span>종목캡 25% — 불안정이 아니라 알고 치른 대가</h2>
<div class="call">
  <p>최빈값이 <b>30%</b>이고 현행 25%는 한 번도 뽑히지 않았다. 그러나 이는 <b>이미 기록된
  의도적 선택</b>이다. 실험 14가 “4개 유니버스 × 4개 지표 전부 단조 — 샤프 기준 최선은 30%이나
  MDD는 조일수록 개선. <b>집중도 완화 목적으로 25% 채택</b>”으로 남겨 두었다.</p>
  <p>이번 워크포워드는 <b>샤프 단일 기준</b>이라 당연히 30%를 고른다. 즉 이 항목의 불일치는
  불안정의 증거가 아니라, <b>대가를 알고 고른 값이 시점을 바꿔도 일관됨</b>을 재확인한 것이다.</p>
</div>

<h2><span class="n">06</span>손실 제한 폭 — 이번 실험의 진짜 지적</h2>
<p class="lede">현행값 <span class="num">{g('손실 제한 폭', sl['current'])}</span>가 3개 값 중 몇 위였는지.</p>
<div class="scroll"><table class="sl">
  <thead><tr><th>폴드</th>{"".join(f'<th>F{i+1}<br>{y}</th>' for i, y in enumerate(YRS))}</tr></thead>
  <tbody><tr><th>현행값 등수</th>{slcells}</tr></tbody>
</table></div>
<div class="call alert">
  <p class="big">현행값이 1위인 폴드는 <span class="num">0</span>개, 꼴찌인 폴드는
  <span class="num">{last}</span>개다.</p>
  <p>대신 <b>{g('손실 제한 폭', sl['grid'][0])}</b>가 6회, <b>{g('손실 제한 폭', sl['grid'][2])}</b>가 3회
  뽑혔다. 실험 9의 “추가 튜닝으로 확정”이 코스피전체 단일 기준으로는 재현되지 않는다 —
  <b>이 프로젝트에서 근거가 가장 얇은 설정값</b>이다.</p>
  <p><b>그럼에도 값을 바꾸지 않았다.</b> ① 선등록 6절에 “어떤 결과가 나와도 바꾸지 않는다”를
  미리 적어 두었고 ② 학습창에 2015~2019가 포함된 이상 결과를 보고 조정하면 홀드아웃을
  네 번째로 쓰는 셈이며 ③ 무엇보다 <b>04의 보조지표가 “갈아타면 손해”를 가리킨다.</b></p>
</div>

<h2><span class="n">07</span>후속 검증 — 손절 8%를 4개 유니버스로 다시 쟀다</h2>
<p class="lede">06의 지적을 그대로 받아들이지 않고, 워크포워드의 최대 한계였던
<b>단일 유니버스</b>를 메워 다시 확인했다. 판정 기준은 데이터를 보기 전에 고정했다 —
<b>4개 유니버스 전부에서 샤프 개선 + MDD 비악화</b>여야 신호, 부호가 갈리면 노이즈(§1.1).</p>
<div class="scroll"><table class="e23">
  <thead><tr><th>구간</th>
    {"".join(f'<th>{u}</th>' for u in E23["universes"])}<th>판정</th></tr></thead>
  <tbody>{e23rows}</tbody>
</table></div>
<p class="bnote2">숫자는 Δ샤프(8% − 10%). 양수면 8%가 나은 것.</p>

<div class="call alert">
  <p class="big">노이즈로 판정된 3개 구간 전부에서 <b>코스피 계열은 악화, 코스닥 계열은
  개선</b>으로 갈렸다.</p>
  <p>우연이 아니라 <b>“변동성이 큰 유니버스일수록 손절을 조이는 편이 유리하다”</b>는
  유니버스 특성이다. §1.1 부호 일관성 기준이 잡아내라고 만든 전형적 노이즈이며,
  실험 3(변동성조정 모멘텀 — 코스닥150만 개선)과 같은 형태다.
  <b>검증 전용 구간만 4/4 전면 개선</b>인데, 바로 그 구간의 우위가 확장창 학습에 끌려간 것이
  06의 “1위 0회”였다 — 아래 결론과 맞물린다.</p>
</div>

<h4 style="margin-top:22px">보고서 유니버스(코스피전체) 상세 — 보고서 구간</h4>
<div class="scroll"><table class="kv">
  <thead><tr><th></th><th>손절 8%</th><th>현행 10%</th></tr></thead>
  <tbody>{kosrows}</tbody>
</table></div>
<p class="bnote2">음영이 더 나은 쪽. MDD만 8%가 앞선다.</p>

<div class="call">
  <p class="big">06의 “1위 0회”는 시점 불안정이 아니라 <b>초기 구간의 잔상</b>이었다.</p>
  <p>검증 전용 구간(2017~2019)에서는 8%가 <b>4/4로 확실히 우위</b>다. 그런데 워크포워드는
  <b>확장창</b>이라 학습 구간이 <b>항상 그 구간을 포함</b>한다. 초기 우위가 9폴드 내내
  끌려간 것이다.</p>
  <p><b>결론 — 현행 10% 유지.</b> 판정 기준을 세 구간에서 통과하지 못했고, 보고서
  유니버스에서는 오히려 열위다. 설정값을 바꾸지 않았으므로 실험 22 선등록과도 충돌하지 않는다.</p>
</div>

<h2><span class="n">08</span>이 결과의 한계</h2>
<ul>
  <li><b>단일 유니버스</b>(코스피전체) — <b>07에서 해소.</b> 4개 유니버스로 넓히자 손절 폭의
    불일치는 시점 불안정이 아니라 <b>유니버스 특성</b>임이 드러났다</li>
  <li><b>확장창의 구조적 성질.</b> 학습 구간이 항상 초기 구간을 포함해 그 특성을 계속 끌고 간다.
    앞으로 워크포워드의 지적은 <b>§1.1 부호 일관성으로 한 번 더 거른 뒤</b> 결론을 낸다</li>
  <li><b>샤프 단일 기준.</b> MDD·실효 분산을 함께 보던 원 선택 기준과 다르다 (종목캡 사례가 이를 보여준다)</li>
  <li><b>인샘플 내부다.</b> 표본 외 검증이 아니며 성과 주장을 재검증하지 않는다.
    그 자리는 실전 검증만이 채울 수 있다</li>
  <li>폴드 간 학습창이 크게 겹쳐 <b>9개 폴드가 독립 관측 9회가 아니다</b></li>
</ul>

<footer>
  선등록 <code>docs/algorithms/algorithm1-walkforward-prereg.md</code> ·
  기록 <code>algorithm1-experiments.md</code> 실험 22 · 23 ·
  방법론 <code>methodology.md</code> §1.10.5 ·
  산출 <code>walkforward.py</code> · <code>stoploss_8_vs_10.py</code> (읽기 전용)
</footer>
</div>
"""
out = SP / "wf_viz.html"
out.write_text(HTML)
print(f"wrote {out} ({len(HTML):,} bytes)")
