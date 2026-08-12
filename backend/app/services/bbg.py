"""블룸버그 데이터 수집 — 사내 터미널 PC에 SSH로 붙어 bbgripper CLI를 실행한다.

블룸버그 API는 터미널이 설치된 윈도우 PC에서만 동작하므로, 그 PC에 SSH로 명령을 보내고
표준출력으로 CSV를 받아온다. paramiko 대신 시스템 `ssh` 바이너리를 쓴다(추가 설치 불필요).

.env 설정:
  BBG_KEYFILE_PATH   SSH 개인키 경로
  BBG_REMOTE_PATH    원격 파이썬이 있는 디렉토리 (윈도우 경로, 역슬래시)
  BBG_HOST / BBG_USER

응답 CSV의 열 이름은 `필드|티커` 형식이다 (예: `PX_LAST|KOSPI2 Index`).
"""
import io
import os
import subprocess

import pandas as pd

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15"]
DEFAULT_TIMEOUT = 600


def _cfg(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} 환경변수가 없습니다 (.env 확인)")
    return v


def ssh_exec_command(command: str, hostname: str | None = None, username: str | None = None,
                     key_filepath: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str]:
    """원격 명령을 실행하고 (stdout, stderr)를 반환한다."""
    host = hostname or _cfg("BBG_HOST")
    user = username or _cfg("BBG_USER")
    key = key_filepath or _cfg("BBG_KEYFILE_PATH")
    proc = subprocess.run(["ssh", "-i", key, *SSH_OPTS, f"{user}@{host}", command],
                          capture_output=True, text=True, timeout=timeout)
    return proc.stdout, proc.stderr


def fetch_bbg_timeseries(tickers: list[str], start_date, end_date,
                         fields: str | list[str] = "PX_LAST") -> pd.DataFrame | None:
    """블룸버그 시계열을 DataFrame으로 받는다 (index=date, columns=`필드|티커`).

    bbgripper CLI 는 stdout 앞에 로그 한 줄을 붙이므로 'date,' 로 시작하는 줄부터 잘라 읽는다.
    """
    if isinstance(fields, (list, tuple)):
        fields = ",".join(fields)
    remote = _cfg("BBG_REMOTE_PATH")
    ticker_str = ",".join(tickers)
    start = pd.Timestamp(start_date).strftime("%Y-%m-%d")
    end = pd.Timestamp(end_date).strftime("%Y-%m-%d")
    command = (f'{remote}\\python -m bbgripper.cli run '
               f'-t "{ticker_str}" -f "{fields}" --start {start} --end {end}')

    out, err = ssh_exec_command(command)
    lines = out.splitlines()
    head = next((i for i, ln in enumerate(lines) if ln.startswith("date,")), None)
    if head is None:
        print(err or out or "(빈 응답)")
        return None

    df = pd.read_csv(io.StringIO("\n".join(lines[head:])))
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()
