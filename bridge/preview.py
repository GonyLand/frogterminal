"""브라우저 패널의 '미리보기 열기' — 프로젝트가 띄운 로컬 개발 서버를 테일넷 https 로 내준다.

왜 이렇게 하나 (설계 = docs/2026-09-20-브라우저-패널-검토와-계획.md 9절):
  * 폰·윈도우에서 "맥의 localhost:3000" 은 자기 자신이라 못 닿는다. 브릿지 프록시(같은 출처)는
    iframe 안의 앱 스크립트가 대표 신원으로 브릿지 API 를 부를 수 있어 안 쓴다. 대신 Tailscale Serve 에
    별도 https 포트를 붙인다(별도 출처·테일넷 전용·경로 재작성 없음 — 2026-09-20 실측 200).
  * 임의 포트를 받는 API 는 위험하다(브릿지 7788·CDP 포트도 통과) — 서버가 lsof 로 만든 서비스 목록의
    ID 만 받고, 7788·브라우저 금지 포트·목록 밖 포트는 거절한다.
  * 외부 포트는 내부 포트와 분리한다: 내부 포트를 그대로 쓸 수 없으면(브라우저 금지 포트·이미 Serve 가
    다른 곳에 쓰는 포트) 13000 대에서 빈 것을 고른다.
  * `serve --bg` 매핑은 패널을 닫아도·재부팅해도 남는다 — 소유 장부(registry)에 만료 시각을 두고 웹이
    주기적으로 갱신한다. 만료된 것·장부에 없는데 우리 대역에 남은 것은 기동·조회 때 거둔다.
  * 열기 전·후에 serve status 를 읽어 그 포트가 Funnel 이 아니고, 어떤 Funnel 입구도 그 목적지로
    가지 않는지 본다(기존 게이트는 7788 도달만 본다 — security._reaches_us).
"""
from __future__ import annotations

import http.client
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

# Fetch 표준의 금지 포트 — Serve 등록은 되지만 브라우저가 https://host:6000 을 거부한다.
BAD_PORTS = frozenset({
    1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87, 95, 101, 102,
    103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137, 139, 143, 161, 179, 389, 427, 465,
    512, 513, 514, 515, 526, 530, 531, 532, 540, 548, 554, 556, 563, 587, 601, 636, 989, 990, 993,
    995, 1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668,
    6669, 6697, 10080,
})
EXT_RANGE = range(13000, 13100)              # 내부 포트를 그대로 못 쓸 때 고르는 외부 포트 대역
TTL_S = 15 * 60                              # 웹이 갱신하지 않으면 이 뒤에 거둔다(폰 절전·탭 강제종료 대비)
RESERVED = frozenset({config.PORT})          # 절대 내주지 않는 포트 — 브릿지 자신


def registry_file() -> Path:
    return config.SETTINGS_FILE.parent / "previews.json"


# ── 서비스 목록: lsof 두 번(LISTEN 소켓 → pid 의 cwd) ─────────────────────────

def _lsof(args: list[str]) -> str:
    try:
        out = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", *args, "-Fpcn"],
                             capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout


def parse_lsof_listen(text: str) -> list[dict]:
    """`-Fpcn` 출력(p<pid>/c<이름>/n<주소> 줄) → [{pid, cmd, port}] — 127.0.0.1 LISTEN 만."""
    found, pid, cmd = [], None, ""
    for line in text.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            pid, cmd = int(value) if value.isdigit() else None, ""
        elif tag == "c":
            cmd = value
        elif tag == "n" and pid is not None:
            m = re.fullmatch(r"127\.0\.0\.1:(\d+)", value)
            if m:
                found.append({"pid": pid, "cmd": cmd, "port": int(m[1])})
    return found


def parse_lsof_cwd(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


PROBE_TIMEOUT_S = 0.5


def http_status(port: int) -> int | None:
    """그 포트가 평문 HTTP 에 답하나 — 상태 코드, 아니면 None. '듣고 있는 포트'가 곧 웹 서버는 아니다
    (2026-09-20 실사고: pytest 의 https 가짜 브릿지가 프로젝트 서비스로 잡혀 자동으로 열려 흰 화면)."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=PROBE_TIMEOUT_S)
        conn.request("GET", "/", headers={"Host": f"localhost:{port}"})
        status = conn.getresponse().status
        conn.close()
        return status
    except (OSError, http.client.HTTPException, ValueError):
        return None


def list_services(root: Path | None = None, probe=None) -> list[dict]:
    """127.0.0.1 에서 LISTEN 중인 서비스 — pid 의 cwd 가 프로젝트 폴더 아래면 그 프로젝트로 매긴다.

    cwd 는 추정이지 소유 증명이 아니다 — 그래도 '이 프로젝트가 띄운 것'을 고르는 힌트로는 충분하고,
    내주기(expose)는 어차피 이 목록의 ID 로만 받는다. 브릿지 자신·금지 포트는 처음부터 뺀다.
    `http` = 평문 HTTP 응답 코드(없으면 None) — 웹은 답하는 것만 자동으로 열고 나머지는 표시만 한다.
    """
    root = root if root is not None else config.projects_root()
    probe = probe if probe is not None else http_status
    listen = parse_lsof_listen(_lsof(["-i4TCP@127.0.0.1", "-sTCP:LISTEN"]))
    from . import browser                            # 2단계 크롬의 CDP 포트 — 절대 내주지 않는다(원격 디버깅 노출)
    reserved = RESERVED | browser.registry_ports()
    seen, out = set(), []
    for s in listen:
        if s["port"] in reserved or s["port"] in BAD_PORTS or s["port"] in seen:
            continue
        seen.add(s["port"])
        cwd = parse_lsof_cwd(_lsof(["-p", str(s["pid"]), "-d", "cwd"])) or ""
        project = None
        try:
            rel = Path(cwd).resolve().relative_to(root.resolve())
            project = rel.parts[0] if rel.parts else None
        except (ValueError, OSError):
            project = None
        out.append({"id": f"{s['pid']}:{s['port']}", "port": s["port"], "pid": s["pid"],
                    "cmd": s["cmd"], "cwd": cwd, "project": project})
    if out:                                         # 포트마다 0.5초 상한 — 병렬이라 전체도 그 언저리
        with ThreadPoolExecutor(max_workers=min(16, len(out))) as pool:
            for svc, status in zip(out, pool.map(lambda x: probe(x["port"]), out)):
                svc["http"] = status
    return sorted(out, key=lambda s: (s["project"] is None, s["http"] is None, s["port"]))


# ── Serve 상태 읽기 ───────────────────────────────────────────────────────────

def serve_status() -> dict | None:
    try:
        out = subprocess.run([config.TAILSCALE_BIN, "serve", "status", "--json"],
                             capture_output=True, text=True, timeout=10, check=True).stdout
        data = json.loads(out)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def serve_mappings(status: dict) -> dict[int, str]:
    """외부 https 포트 → 프록시 목적지(예: 'http://127.0.0.1:3000'). 웹 핸들러가 있는 것만."""
    out = {}
    for hostport, entry in (status.get("Web") or {}).items():
        port = str(hostport).rsplit(":", 1)[-1]
        handlers = (entry or {}).get("Handlers") or {}
        proxy = ((handlers.get("/") or {}).get("Proxy")) if isinstance(handlers, dict) else None
        if port.isdigit() and isinstance(proxy, str):
            out[int(port)] = proxy
    return out


def funnel_reaches(status: dict, ext_port: int, dest: str) -> bool | None:
    """이 외부 포트가 Funnel 이거나, 어떤 Funnel 입구든 이 목적지로 가면 True. 모르면 None."""
    allow = status.get("AllowFunnel") or {}
    if not isinstance(allow, dict):
        return None
    mappings = serve_mappings(status)
    for hostport, on in allow.items():
        if not on:
            continue
        port = str(hostport).rsplit(":", 1)[-1]
        if not port.isdigit():
            return None
        if int(port) == ext_port or mappings.get(int(port)) == dest:
            return True
    return False


# ── 소유 장부 ─────────────────────────────────────────────────────────────────

def load_registry() -> dict[str, dict]:
    try:
        data = json.loads(registry_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_registry(reg: dict[str, dict]) -> None:
    path = registry_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=1))


def _serve(args: list[str]) -> tuple[bool, str]:
    try:
        out = subprocess.run([config.TAILSCALE_BIN, "serve", *args],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return out.returncode == 0, (out.stderr or out.stdout).strip()[:300]


def pick_ext_port(port: int, mappings: dict[int, str], dest: str) -> int | None:
    """내부 포트를 그대로 쓸 수 있으면 그대로, 아니면 13000 대의 빈 포트. 이미 그 목적지로 가는 매핑이
    있으면 그것을 재사용한다(다른 기기가 먼저 열어 둔 것 — 같은 것을 두 번 열지 않는다)."""
    for ext, d in mappings.items():
        if d == dest:
            return ext
    if port not in BAD_PORTS and port not in RESERVED and port not in mappings and port not in EXT_RANGE:
        return port
    for ext in EXT_RANGE:
        if ext not in mappings and ext not in BAD_PORTS:
            return ext
    return None


class PreviewError(Exception):
    pass


def open_preview(service_id: str, project: str, owner: str,
                 services: list[dict] | None = None, now: float | None = None) -> dict:
    """서비스 ID(pid:port)를 외부 https 포트로 내준다. 돌려주는 값 = {port(외부), dest, expires}."""
    now = now if now is not None else time.time()
    services = services if services is not None else list_services()
    svc = next((s for s in services if s["id"] == service_id), None)
    if svc is None:
        raise PreviewError("그 서비스가 지금 LISTEN 중이 아닙니다 — 목록을 새로 읽으세요")
    if svc["port"] in RESERVED or svc["port"] in BAD_PORTS:
        raise PreviewError("내줄 수 없는 포트입니다")
    dest = f"http://127.0.0.1:{svc['port']}"
    status = serve_status()
    if status is None:
        raise PreviewError("tailscale serve status 를 읽지 못했습니다 — 판정불가라 열지 않습니다")
    mappings = serve_mappings(status)
    ext = pick_ext_port(svc["port"], mappings, dest)
    if ext is None:
        raise PreviewError("빈 외부 포트가 없습니다")
    if funnel_reaches(status, ext, dest) is not False:
        raise PreviewError("이 포트나 목적지가 Funnel(공개 인터넷)에 걸려 있어 열지 않습니다")
    if mappings.get(ext) != dest:
        ok, msg = _serve(["--bg", f"--https={ext}", dest])
        if not ok:
            raise PreviewError(f"serve 등록 실패: {msg}")
        after = serve_status()
        if after is None or funnel_reaches(after, ext, dest) is not False:
            _serve([f"--https={ext}", "off"])           # 연 뒤 판정이 바뀌면 곧바로 거둔다
            raise PreviewError("연 뒤 Funnel 판정이 안전하지 않아 되돌렸습니다")
    reg = load_registry()
    reg[str(ext)] = {"port": svc["port"], "pid": svc["pid"], "project": project, "owner": owner,
                     "dest": dest, "expires": now + TTL_S, "opened": reg.get(str(ext), {}).get("opened", now)}
    save_registry(reg)
    return {"port": ext, "dest": dest, "expires": reg[str(ext)]["expires"]}


def renew_preview(ext_port: int, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    reg = load_registry()
    entry = reg.get(str(ext_port))
    if not entry:
        return False
    entry["expires"] = now + TTL_S
    save_registry(reg)
    return True


def close_preview(ext_port: int) -> bool:
    """장부에 있는 것만 거둔다 — 남이 연 Serve 매핑(예: 8443)은 절대 건드리지 않는다."""
    reg = load_registry()
    if str(ext_port) not in reg:
        return False
    _serve([f"--https={ext_port}", "off"])
    reg.pop(str(ext_port), None)
    save_registry(reg)
    return True


def reap(now: float | None = None) -> list[int]:
    """만료된 장부 항목을 거둔다(기동·목록 조회 때). 거둔 외부 포트 목록을 돌려준다."""
    now = now if now is not None else time.time()
    reg = load_registry()
    gone = [int(p) for p, e in reg.items() if not isinstance(e, dict) or e.get("expires", 0) < now]
    for p in gone:
        _serve([f"--https={p}", "off"])
        reg.pop(str(p), None)
    if gone:
        save_registry(reg)
    return gone


def active(now: float | None = None) -> dict[str, dict]:
    now = now if now is not None else time.time()
    return {p: e for p, e in load_registry().items()
            if isinstance(e, dict) and e.get("expires", 0) >= now}
