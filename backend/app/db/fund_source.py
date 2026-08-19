"""펀드 소스 DB(price_fetcher, MySQL `finance`) 읽기 전용 접속.

**이 DB에는 절대 쓰지 않는다.** 다른 프로젝트가 수집·적재하는 원본이라, 여기서 건드리면
그쪽 수집이 깨진다. 백필과 검증을 위해 읽기만 한다.

읽기 전용은 관례가 아니라 코드로 막는다 — `fund_source_query()`가 SELECT/WITH/SHOW로
시작하지 않는 문장을 거부하고, 세션도 autocommit 읽기 전용으로 연다. 실수로 UPDATE를
날리는 경로 자체를 없애기 위해서다.

접속 정보는 `FUND_SOURCE_DATABASE_URL` 환경변수로 준다 (backend/.env).
  FUND_SOURCE_DATABASE_URL=mysql+pymysql://user:pw@localhost:3306/finance
"""
import os
import re
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# 허용하는 문장 — 앞의 주석·공백을 걷어낸 뒤 첫 토큰으로 판정한다.
_READ_ONLY = re.compile(r"^\s*(?:/\*.*?\*/\s*|--[^\n]*\n\s*)*(select|with|show|describe|explain)\b",
                        re.IGNORECASE | re.DOTALL)


class FundSourceNotConfigured(RuntimeError):
    pass


class FundSourceWriteAttempt(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = os.getenv("FUND_SOURCE_DATABASE_URL")
    if not url:
        raise FundSourceNotConfigured(
            "FUND_SOURCE_DATABASE_URL 이 설정되지 않았습니다. backend/.env 에 "
            "mysql+pymysql://user:pw@host:3306/finance 형태로 넣어주세요.")
    # pool_pre_ping — 로컬 MySQL 이 idle 커넥션을 끊어도 다음 질의에서 되살린다.
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600, hide_parameters=True)


@contextmanager
def fund_source_connection():
    """읽기 전용 커넥션. 트랜잭션을 열지 않으므로 커밋할 것도 없다."""
    conn = get_engine().connect().execution_options(readonly=True, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def fund_source_query(sql: str, params: dict | None = None) -> list:
    """SELECT 계열만 실행한다. 그 외 문장은 실행 전에 거부한다."""
    if not _READ_ONLY.match(sql):
        raise FundSourceWriteAttempt(
            f"읽기 전용 접속입니다. SELECT/WITH/SHOW 만 허용됩니다: {sql.strip()[:60]}...")
    with fund_source_connection() as conn:
        return conn.execute(text(sql), params or {}).all()


def is_configured() -> bool:
    return bool(os.getenv("FUND_SOURCE_DATABASE_URL"))
