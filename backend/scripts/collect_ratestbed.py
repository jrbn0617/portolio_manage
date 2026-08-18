"""RA 테스트베드(ratestbed.kr) 공시 일별 기준가 수집.

    https://www.ratestbed.kr:7443/portal/pblntf/viewAlgrthInfoComop.do?menuNo=200235
    (알고리즘별 운용정보 · 심사후)

이 페이지의 데이터 엔드포인트는 아래 하나이고, **sdate/edate로 전 기간을 1회에 받는다**.

    GET /portal/pblntf/viewAlgrthInfoComop.json
        ?odrSn=<차수>&algrthSn=<알고리즘>&basicDt=&sdate=YYYY-MM-DD&edate=YYYY-MM-DD&noHit=Y

    응답
      chartsRatereturnAlgrth : [{acnutSn, invtTyCd, basicDate, standardPrice,
                                 standardDeviation, sharpRatio, ...}]  ← 일별 기준가
      chartsRatereturnIndex  : [{basicDate, kospiRate, kospi200Rate, kosdaqRate}]
      viewAlgrth / viewAcnutData : 알고리즘·계좌 메타

**주의 — 잘못된 경로들** (전부 404):
  viewDalyStdpcComop.do / viewAcnutDetailComop.do 는 서버에 없다.
  목록 페이지의 `data-params.method`는 경로처럼 보이지만 실제로는 동작하지 않는다.
  chartsRatereturn.json 은 basicDate를 무시하고 **최근 3개월만** 준다 — 쓰지 말 것.

투자유형 invtTyCd: 01 안정형 / 02 중립형 / 03 적극형

사용법
  # 1) 차수(odrSn)의 알고리즘 목록 보기 — algrthSn을 찾는다
  python scripts/collect_ratestbed.py --list --odr-sn 2
  python scripts/collect_ratestbed.py --list --odr-sn 2 --grep 키움

  # 2) 일별 기준가 수집 (CSV 저장)
  python scripts/collect_ratestbed.py --odr-sn 2 --algrth-sn 87 \
      --sdate 2017-05-22 --edate 2026-08-14

DB에는 쓰지 않는다. CSV로만 떨어뜨린다(적재가 필요하면 별도 승인 후).
"""
import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.ratestbed.kr:7443"
PAGE = f"{BASE}/portal/pblntf/viewAlgrthInfoComop.do?menuNo=200235"
API = f"{BASE}/portal/pblntf/viewAlgrthInfoComop.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
OUT_DIR = Path(__file__).resolve().parents[2] / "reference"
INVT = {"01": "안정형", "02": "중립형", "03": "적극형"}

# 사이트가 자체서명/구형 체인을 쓰는 경우가 있어 검증을 끈다(공개 공시 데이터).
import ssl  # noqa: E402

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def get(params: dict, timeout: int = 120) -> dict:
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": PAGE})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def list_algorithms(odr_sn: int, grep: str | None):
    data = get({"odrSn": odr_sn})
    rows = data.get("listOperSttus", [])
    print(f"차수 {odr_sn} · 알고리즘 {len(rows)}건 (기준일 {data.get('basicDate')})\n")
    print(f"{'algrthSn':>9} {'업체':<16} {'알고리즘':<34} {'공시개시':<12} {'공시종료':<12}")
    print("-" * 88)
    for r in sorted(rows, key=lambda x: x.get("entrpsNm") or ""):
        line = f"{r['algrthSn']:>9} {(r.get('entrpsNm') or '')[:15]:<16} {(r.get('algrthNm') or '')[:33]:<34} " \
               f"{r.get('comopBgnde') or '-':<12} {r.get('comopEndde') or '공시중':<12}"
        if grep and grep not in line:
            continue
        print(line)


def collect(odr_sn: int, algrth_sn: int, sdate: str, edate: str, out: Path | None):
    data = get({"odrSn": odr_sn, "algrthSn": algrth_sn, "basicDt": "",
                "sdate": sdate, "edate": edate, "noHit": "Y"})
    meta = data.get("viewAlgrth") or {}
    nav = sorted(data.get("chartsRatereturnAlgrth", []), key=lambda r: (r["acnutSn"], r["basicDate"]))
    idx = sorted(data.get("chartsRatereturnIndex", []), key=lambda r: r["basicDate"])
    if not nav:
        print("기준가 데이터가 비어 있습니다. algrthSn/odrSn/기간을 확인하세요.")
        return

    name = (meta.get("algrthNm") or f"algrth{algrth_sn}").replace("/", "_").strip()
    comp = (meta.get("entrpsNm") or "").strip()
    print(f"[{comp}] {name}  공시 {meta.get('comopBgnde')} ~ {meta.get('comopEndde') or '진행중'}")

    idx_by_date = {r["basicDate"]: r for r in idx}
    out = out or (OUT_DIR / f"테스트베드_{name}_{sdate}_{edate}.csv")
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date", "acnutSn", "invtTyCd", "invtTyNm", "standardPrice",
                    "standardDeviation", "sharpRatio", "kospi", "kospi200", "kosdaq"])
        for r in nav:
            i = idx_by_date.get(r["basicDate"], {})
            w.writerow([r["basicDate"].replace("/", "-"), r["acnutSn"], r["invtTyCd"],
                        INVT.get(r["invtTyCd"], r["invtTyCd"]), r["standardPrice"],
                        r.get("standardDeviation"), r.get("sharpRatio"),
                        i.get("kospiRate"), i.get("kospi200Rate"), i.get("kosdaqRate")])

    per = {}
    for r in nav:
        per.setdefault(r["acnutSn"], []).append(r)
    print(f"\n{'계좌':>8} {'유형':<7} {'일수':>6} {'기간':<26} {'기준가':>22} {'누적':>9}")
    print("-" * 84)
    for sn, v in sorted(per.items()):
        f0, l0 = v[0], v[-1]
        print(f"{sn:>8} {INVT.get(f0['invtTyCd'], '?'):<7} {len(v):>6} "
              f"{f0['basicDate']} ~ {l0['basicDate']}  "
              f"{f0['standardPrice']:>9,.2f} → {l0['standardPrice']:>9,.2f} "
              f"{(l0['standardPrice']/f0['standardPrice']-1)*100:>+8.1f}%")
    if idx:
        print(f"\n지수 {len(idx)}일  코스피 {idx[0]['kospiRate']:,.2f} → {idx[-1]['kospiRate']:,.2f} "
              f"({idx[-1]['kospiRate']/idx[0]['kospiRate']-1:+.1%})")
    print(f"\n저장: {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="차수의 알고리즘 목록 출력")
    ap.add_argument("--grep", help="--list 결과 필터 (업체명·알고리즘명)")
    ap.add_argument("--odr-sn", type=int, required=True, help="심사 차수")
    ap.add_argument("--algrth-sn", type=int, help="알고리즘 일련번호 (--list로 확인)")
    ap.add_argument("--sdate", default="2016-01-01", help="시작일 YYYY-MM-DD")
    ap.add_argument("--edate", help="종료일 YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--out", type=Path, help="CSV 출력 경로")
    args = ap.parse_args()

    if args.list:
        list_algorithms(args.odr_sn, args.grep)
        return
    if not args.algrth_sn:
        ap.error("--algrth-sn 이 필요합니다 (--list 로 확인하세요)")
    edate = args.edate or time.strftime("%Y-%m-%d")
    collect(args.odr_sn, args.algrth_sn, args.sdate, edate, args.out)


if __name__ == "__main__":
    main()
