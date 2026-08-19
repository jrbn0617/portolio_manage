"""KOFIA 펀드공시 목록에서 운용펀드와 종류형(클래스) 펀드를 갈라내고 매핑한다.

price_fetcher 의 `process_classify_and_assign_funds` 를 옮긴 것이다. 원본은
`import re` 가 빠져 있어 그대로는 NameError 가 난다(주석에 "loader 에 추가"라고
적힌 걸 보면 옮기는 중이었던 듯하다).

KOFIA 는 하나의 '운용펀드'(모펀드) 아래 여러 '종류형 펀드'(클래스)를 두고, 공시
목록에서 자식 행의 이름 앞에 `└▶` 를 붙여 계층을 표현한다.

**행 순서가 곧 계층 정보다.** 각 클래스는 "바로 앞에 나온 운용펀드"의 자식이므로,
응답을 받은 순서를 그대로 유지해야 한다. 중간에 정렬하면 매핑이 통째로 어긋난다.
"""
import re

import pandas as pd

# 자식 표시. 계층 판정은 오직 이것만 본다 (아래 split_manage_and_class 주석 참고).
_CHILD_MARK = "└▶"

# 클래스 구분자 추출 패턴. 위에서부터 먼저 걸리는 것을 쓴다.
_CLASS_PATTERNS = [
    r"[A-Za-z][A-Za-z]-[A-Za-z]",
    r"[A-Za-z]-[A-Za-z][A-Za-z][A-Za-z]",
    r"[A-Za-z]-[A-Za-z]\d[A-Za-z]",
    r"[A-Za-z]-[A-Za-z][A-Za-z]\d",
    r"[A-Za-z]-[A-Za-z]\d",
    r"[A-Za-z]-[A-Za-z][A-Za-z]",
    r"[A-Za-z]-\([A-Za-z]\)\d",
    r"[A-Za-z]-\([A-Za-z]\)",
    r"[A-Za-z]-[A-Za-z]",
    r"[A-Za-z]\([A-Za-z]\)\d",
]

# 위 패턴이 하나도 안 걸릴 때 — 문자열을 뒤집어 꼬리쪽 토큰을 잡는다.
_REVERSED_PATTERNS = [r"[A-Za-z][A-Za-z][A-Za-z]", r"[A-Za-z][A-Za-z]", r"\d[A-Za-z]", r"[A-Za-z]"]

# 클래스 이름에 섞여 들어와 구분자 추출을 방해하는 말들
_NOISE = re.compile(
    r"class|CLASS|Class|종류형|종류|EMP|IBK|BNK|Investor|Hi-Korea|Tomorrow|"
    r"Tops Value|KOSPI200|KorChindia|KCGI|TDF|KRX|MAN")

_FALLBACK_KEYWORDS = ["직판", "적립식", "3"]

# 특수 목적 펀드 — 일반 포트폴리오 후보에서 구분해 두려는 용도
_SPECIAL_KEYWORDS = ["주택", "연금", "퇴직", "소득공제", "전환형", "직판",
                     "적립식", "목표전환", "레버리지", "인버스", "월지급"]


def split_manage_and_class(disclosure_df: pd.DataFrame) -> pd.Series:
    """각 행이 운용펀드인지 판정한다 (True = 운용펀드). **`└▶` 유무만 본다.**

    원본 `process_classify_and_assign_funds` 는 여기에 "이름에 '종류'가 없을 것"을
    더하고, 그 때문에 잘못 걸러진 모펀드를 꼬리 패턴('(종류)$' 등)으로 되살렸다.
    그 조건이 틀렸다 — 이름 가운데에 '종류형'이 들어간 모펀드(예: '삼성신종MMF종류형D 2')가
    자식으로 분류되고, 그 자식들은 엉뚱한 앞쪽 운용펀드에 붙는다.

    실측으로 확인했다. 기존 master_fund_map 23,429행과 대조했을 때
      원본 규칙        21,963/21,972 일치 (99.959%)
      `└▶` 유무만      21,972/21,972 일치 (100.000%)
    이 테이블을 만든 load_fund_kr_kofia_disclosure.py 도 `└▶` 만 본다."""
    return ~disclosure_df["공시대상"].str.contains(_CHILD_MARK, regex=False)


def _common_prefix_name(manage_full_name: str, class_full_name: str) -> str:
    """운용펀드 이름 = 자식 이름과의 공통 접두어.

    운용펀드 전체이름과 첫 자식 이름을 100자로 패딩해 처음 갈리는 위치까지 자른다.
    끝이 '(' 로 끝나면 떼어낸다."""
    a = f"{manage_full_name:<100}"
    b = f"{class_full_name:<100}"
    cut = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), 0)
    name = a[:cut]
    if not name:
        return ""
    return name[:-1] if name[-1] == "(" else name


def _extract_class_str(class_name: str, manage_name: str) -> tuple[str, str]:
    """클래스 이름에서 운용펀드 이름을 뺀 나머지에서 구분자를 뽑는다.

    반환: (잡음 제거 후 원문, 추출된 구분자)"""
    raw = _NOISE.sub("", class_name.replace(manage_name, ""))

    for p in _CLASS_PATTERNS:
        m = re.search(p, raw)
        if m:
            return raw, raw[m.start():m.end()]

    # 뒤집어서 꼬리쪽 영숫자 토큰을 잡는다
    reversed_str = re.sub(r"\s", "", "".join(reversed(raw)))
    for rp in _REVERSED_PATTERNS:
        m = re.search(rp, reversed_str)
        if m:
            return raw, "".join(reversed(reversed_str[m.start():m.end()]))

    for kw in _FALLBACK_KEYWORDS:
        if kw in raw:
            return raw, kw
    return raw, ""


def classify_funds(disclosure_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """공시 목록 → (클래스 펀드, 운용펀드).

    disclosure_df: 컬럼 ['펀드코드', '공시대상', '회사'], **KOFIA 응답 순서 그대로**.
    """
    df = disclosure_df.reset_index(drop=True)
    manage_flag = split_manage_and_class(df)

    manage_rows = df[manage_flag]
    class_rows = df[~manage_flag].copy()

    if manage_rows.empty:
        raise ValueError("운용펀드로 판정된 행이 하나도 없습니다 — 응답 형식을 확인하세요.")

    # 각 클래스의 부모 = 바로 앞에 나온 운용펀드 (행 순서 의존)
    parent_pos = manage_rows.index.searchsorted(class_rows.index) - 1
    parent_pos = parent_pos.clip(min=0)
    class_rows["manage_code"] = manage_rows.iloc[parent_pos]["펀드코드"].values

    # 같은 펀드가 1년 안에 보고서를 여러 번 내면 여러 행으로 나온다(최대 12회 관측).
    # 등장마다 앞쪽 운용펀드가 다를 수 있으므로 **첫 등장을 취한다** — 기존 map 을 만든
    # 로더와 같은 규칙이라야 대조가 성립한다(keep="last" 로 하면 인덱스가 중복된다).
    manage_df = (manage_rows[["펀드코드", "공시대상"]]
                 .rename(columns={"펀드코드": "fund_code", "공시대상": "full_name"})
                 .drop_duplicates(subset="fund_code", keep="first")
                 .set_index("fund_code").sort_index())

    class_rows["fund_name"] = class_rows["공시대상"].str.replace(r"^└▶", "", regex=True)
    class_df = (class_rows.rename(columns={"펀드코드": "fund_code"})[
                    ["fund_code", "fund_name", "manage_code"]]
                .drop_duplicates(subset="fund_code", keep="first")
                .set_index("fund_code").sort_index())

    # 운용펀드 표시 이름 — 자식과의 공통 접두어
    first_child = class_df.groupby("manage_code")["fund_name"].first()
    manage_df["fund_name"] = [
        _common_prefix_name(full, first_child[code]) if code in first_child.index else full
        for code, full in manage_df["full_name"].items()
    ]

    # 클래스 구분자
    parent_name = manage_df["fund_name"].reindex(class_df["manage_code"]).values
    extracted = [_extract_class_str(c, p or "")
                 for c, p in zip(class_df["fund_name"].values, parent_name)]
    class_df["class_origin"] = [e[0] for e in extracted]
    class_df["class_str"] = pd.Series([e[1] for e in extracted],
                                      index=class_df.index).str.replace(r"[\-\(\)]", "", regex=True)

    class_df["special"] = [any(k in n for k in _SPECIAL_KEYWORDS)
                           for n in class_df["fund_name"]]
    manage_df["special"] = [any(k in n for k in _SPECIAL_KEYWORDS)
                            for n in manage_df["fund_name"]]

    return class_df, manage_df
