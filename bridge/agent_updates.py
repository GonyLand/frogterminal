"""클로드·코덱스 최신판 조회와 사람이 누른 업데이트 실행.

최신판 HTTP 조회와 상태를 바꾸는 update 명령을 설정 저장 코드에서 떼어 둔다. 두 명령은 이
파일의 고정 목록만 실행하며 설정의 별칭이나 요청 본문을 인자로 받지 않는다. shell=True도 쓰지
않는다. 실행 성공 문구는 CLI마다 바뀔 수 있으므로 실제 설치 여부는 전후 버전 상승으로 판정한다.
"""
import asyncio
import re
import subprocess
import time

import aiohttp

from bridge import config, settings

LATEST_SPECS = {
    "claude": ("https://registry.npmjs.org/@anthropic-ai/claude-code/latest", "version"),
    "codex": ("https://api.github.com/repos/openai/codex/releases/latest", "tag_name"),
}
# 이 튜플만 subprocess 인자로 흘러간다. 웹의 agent 값은 키 선택까지만 하고 인자를 만들지 않는다.
UPDATE_ARGS = {"claude": ("update",), "codex": ("update",)}
_VERSION_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)")


def version_parts(value: str | None) -> tuple[int, ...] | None:
    """CLI 장식(`codex-cli`, `rust-v`, 괄호)을 버리고 숫자 버전만 튜플로 읽는다."""
    if not isinstance(value, str) or not (match := _VERSION_RE.search(value)):
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def compare_versions(left: str | None, right: str | None) -> int | None:
    """숫자 파트 기준 -1/0/1. 읽을 수 없는 값은 추측하지 않고 None."""
    a, b = version_parts(left), version_parts(right)
    if a is None or b is None:
        return None
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return (a > b) - (a < b)


def display_version(value: str | None) -> str | None:
    parts = version_parts(value)
    return ".".join(map(str, parts)) if parts is not None else None


def merge_latest(status: dict, latest: dict) -> dict:
    """연결 상태 복사본에 비교 가능한 최신판만 붙인다. 조회 실패면 배지 필드 자체가 없다."""
    merged = {name: dict(info) for name, info in status.items()}
    for name, info in merged.items():
        remote = latest.get(name)
        comparison = compare_versions(info.get("version"), remote)
        shown = display_version(remote)
        if comparison is None or shown is None:
            continue
        info["latestVersion"] = shown
        # 설치본이 공개 API보다 앞선 개발판이어도 '새 버전'이라고 거꾸로 알리지 않는다.
        info["updateAvailable"] = comparison < 0
    return merged


async def _fetch_one(session: aiohttp.ClientSession, url: str, field: str) -> str | None:
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, TypeError):
        return None
    value = data.get(field) if isinstance(data, dict) else None
    return value[:80] if isinstance(value, str) and version_parts(value) is not None else None


async def _fetch_latest_versions() -> dict:
    timeout = aiohttp.ClientTimeout(total=config.AGENT_LATEST_TIMEOUT)
    # GitHub는 명시적인 UA를 권장한다. 제품명만 보내고 계정·기기 정보는 보내지 않는다.
    headers = {"Accept": "application/json", "User-Agent": "frogterminal-version-check"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            values = await asyncio.gather(*(
                _fetch_one(session, url, field) for url, field in LATEST_SPECS.values()))
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        values = [None] * len(LATEST_SPECS)
    return dict(zip(LATEST_SPECS, values))


# 프로세스 메모리 캐시라 토큰·계정 파일을 만들지 않는다. 브릿지는 상시 프로세스이므로 폰과 맥의
# 설정 열기가 같은 6시간 결과를 공유한다. 실패(None)도 캐시해 끊긴 네트워크를 매번 재시도하지 않는다.
_LATEST_CACHE: list = [0.0, None]
_LATEST_FETCH_TASK: asyncio.Task | None = None


async def latest_versions(now=time.monotonic) -> dict:
    global _LATEST_FETCH_TASK
    at, cached = _LATEST_CACHE
    if cached is not None and now() - at < config.AGENT_LATEST_TTL:
        return cached
    # 설정을 두 기기에서 동시에 열어도 공개 API 요청은 한 묶음만 보낸다.
    if _LATEST_FETCH_TASK is None:
        _LATEST_FETCH_TASK = asyncio.create_task(_fetch_latest_versions())
    try:
        result = await _LATEST_FETCH_TASK
    finally:
        _LATEST_FETCH_TASK = None
    _LATEST_CACHE[:] = [now(), result]
    return result


def invalidate_latest() -> None:
    _LATEST_CACHE[:] = [0.0, None]


def _output_summary(stdout, stderr, success: bool, returncode: int | None = None) -> str:
    def text(value) -> str:
        return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
    raw = "\n".join(part.strip() for part in map(text, (stdout, stderr)) if part.strip())
    # ANSI 색·제어문자를 걷어 textContent 안에서도 로그가 난잡해지지 않게 한다.
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
    clean = "\n".join(line.strip() for line in clean.splitlines() if line.strip())
    if clean:
        return clean[-1200:]
    if success:
        return "업데이트 명령이 정상 종료됐습니다."
    if returncode is None:
        return "업데이트 시간이 너무 오래 걸려 중단했습니다."
    return f"업데이트 명령이 종료 코드 {returncode}로 끝났습니다."


def run_update(agent: str) -> dict:
    """허용된 CLI의 고정 update만 실행하고 전후 버전으로 실제 설치 여부를 돌려준다."""
    args = UPDATE_ARGS.get(agent)
    if args is None:
        raise ValueError("unknown agent")
    resolved = settings.which(agent)
    before = settings._version(agent)
    if not resolved or before is None:
        return {"agent": agent, "success": False, "updated": False,
                "beforeVersion": before, "afterVersion": before,
                "output": "설치된 실행 파일을 찾지 못했습니다."}
    try:
        # resolved와 args는 모두 서버 코드의 고정 값이다. 설정 별칭·사용자 문자열은 섞지 않는다.
        completed = subprocess.run([resolved, *args], capture_output=True, text=True,
                                   timeout=config.AGENT_UPDATE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        after = settings._version(agent)       # 시간초과 전에 설치가 끝났다면 재시작 안내는 해야 한다.
        updated = compare_versions(before, after) == -1
        settings.invalidate_status()
        invalidate_latest()
        return {"agent": agent, "success": False, "updated": updated,
                "beforeVersion": before, "afterVersion": after,
                "output": _output_summary(exc.stdout, exc.stderr, False, None)}
    except OSError:
        return {"agent": agent, "success": False, "updated": False,
                "beforeVersion": before, "afterVersion": before,
                "output": "업데이트 명령을 실행하지 못했습니다."}

    success = completed.returncode == 0
    after = settings._version(agent)           # 비정상 종료여도 실제 설치됐다면 재시작 안내는 빠지면 안 된다.
    updated = compare_versions(before, after) == -1
    settings.invalidate_status()
    invalidate_latest()                       # 갱신 뒤 다음 상태 요청은 새 공개판과 다시 맞춘다.
    return {"agent": agent, "success": success, "updated": updated,
            "beforeVersion": before, "afterVersion": after,
            "output": _output_summary(completed.stdout, completed.stderr,
                                      success, completed.returncode)}
