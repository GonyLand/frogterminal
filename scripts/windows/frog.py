"""frog — 이 PC 의 PowerShell 창을 프로그터미널 브릿지의 세션에 붙인다(tmux attach 의 윈도우판).

폰에서 열어 둔 세션을 PC 창에서 이어 쓰고, PC 창에서 연 세션을 폰에서 이어 쓴다 — 같은 세션을 두 화면이 같이 본다.
창이 둘이면 가장 작은 화면에 크기를 맞춘다(tmux 와 같다).

  frog                  지금 폴더(프로젝트 루트 아래)의 세션에 붙는다 — 없으면 만든다
  frog <프로젝트>       그 프로젝트의 세션에 붙는다(시작 에이전트는 설정의 기본값)
  frog <프로젝트> codex | claude | shell
  frog -l               살아 있는 세션 목록

떼기: Ctrl+]  — 세션은 그대로 산다(폰·다른 창에서 계속 보인다). 세션을 끝내려면 안에서 exit 하거나 웹의 휴지통.
"""
import asyncio
import ctypes
import json
import os
import shutil
import sys
import threading
from ctypes import wintypes
from pathlib import Path

import aiohttp

APP = Path.home() / ".frogterminal"
BASE = os.environ.get("FROG_BRIDGE", "http://127.0.0.1:7788")      # 시험용 브릿지(다른 포트)에 붙일 때만 바꾼다
DETACH = "\x1d"                                   # Ctrl+]
AGENTS = {"claude", "codex", "shell"}
# 원격 앱이 켜 둔 모드를 되돌린다 — 대체 화면 · 마우스 · 붙여넣기 괄호 · 포커스 · 커서 표시 · 서식
RESET_MODES = "\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?1004l\x1b[?9001l\x1b[?1l\x1b[?25h\x1b[0m"


def dbg(msg: str) -> None:
    """FROG_DEBUG=<파일> 이면 그 파일에 한 줄씩 남긴다(입력이 어떻게 읽히는지 볼 때)."""
    path = os.environ.get("FROG_DEBUG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"[frog] {msg}\n")
    sys.exit(code)


def load_config() -> dict:
    try:
        token = (APP / "token").read_text(encoding="utf-8").strip()
    except OSError:
        die(f"{APP / 'token'} 이 없다 — 브릿지를 한 번 띄운 뒤에 쓴다")
    try:
        bridge = json.loads((APP / "bridge.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        bridge = {}
    headers = {}
    if bridge.get("allowedLogins"):
        headers["Tailscale-User-Login"] = bridge["allowedLogins"][0]
    if bridge.get("allowedOrigins"):
        headers["Origin"] = bridge["allowedOrigins"][0]
    return {"token": token, "headers": headers, "root": bridge.get("defaultProjectsRoot")}


def project_from_cwd(root: str | None) -> str:
    if not root:
        die("bridge.json 의 defaultProjectsRoot 가 없다 — 프로젝트 이름을 적어라: frog <프로젝트>")
    try:
        rel = Path.cwd().resolve().relative_to(Path(root).resolve())
    except ValueError:
        die(f"지금 폴더가 프로젝트 루트({root}) 아래가 아니다 — 프로젝트 이름을 적어라: frog <프로젝트>")
    if not rel.parts:
        die(f"프로젝트 루트({root}) 자체다 — 프로젝트 폴더 안에서 실행하거나 이름을 적어라: frog <프로젝트>")
    return rel.parts[0]


async def list_sessions(cfg: dict) -> int:
    async with aiohttp.ClientSession(headers=cfg["headers"], cookies={"frog_token": cfg["token"]}) as s:
        try:
            async with s.get(f"{BASE}/api/sessions") as r:
                if r.status != 200:
                    die(f"세션 목록을 못 읽었다(HTTP {r.status})", 1)
                data = await r.json()
        except aiohttp.ClientConnectorError:
            die("브릿지가 안 떠 있다(127.0.0.1:7788) — 작업 스케줄러 'FrogTerminal Bridge' 를 확인한다", 1)
    alive = [x for x in data.get("sessions", data) if x.get("alive")]
    if not alive:
        print("살아 있는 세션 없음")
    for x in alive:
        print(f"{x['name']:<24}{x.get('status') or ''}")
    return 0


# ── 콘솔(Windows) ───────────────────────────────────────────────────────────
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetStdHandle.restype = wintypes.HANDLE
k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.ReadConsoleW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
H_IN, H_OUT = k32.GetStdHandle(-10), k32.GetStdHandle(-11)
PROCESSED_INPUT, LINE_INPUT, ECHO_INPUT, VT_INPUT = 0x1, 0x2, 0x4, 0x200
PROCESSED_OUTPUT, VT_OUTPUT = 0x1, 0x4


def get_mode(handle) -> int | None:
    mode = wintypes.DWORD()
    return mode.value if k32.GetConsoleMode(handle, ctypes.byref(mode)) else None


class RawConsole:
    """키 입력을 가공 없이(VT) 받고 VT 출력을 켠다 — 끝나면 원래대로."""

    def __enter__(self):
        self.old_in, self.old_out = get_mode(H_IN), get_mode(H_OUT)
        if self.old_in is None or self.old_out is None:
            die("콘솔(PowerShell·Windows Terminal) 안에서 실행해야 한다 — 파이프·리다이렉트로는 못 붙는다")
        self.old_cp = (k32.GetConsoleCP(), k32.GetConsoleOutputCP())
        k32.SetConsoleCP(65001)
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleMode(H_IN, (self.old_in & ~(PROCESSED_INPUT | LINE_INPUT | ECHO_INPUT)) | VT_INPUT)
        k32.SetConsoleMode(H_OUT, self.old_out | PROCESSED_OUTPUT | VT_OUTPUT)
        return self

    def __exit__(self, *exc):
        write_out(RESET_MODES.encode())
        k32.SetConsoleMode(H_IN, self.old_in)
        k32.SetConsoleMode(H_OUT, self.old_out)
        k32.SetConsoleCP(self.old_cp[0])
        k32.SetConsoleOutputCP(self.old_cp[1])


def write_out(data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(1, view)
        view = view[n:]


class StripWin32InputMode:
    """세션 안의 pwsh(PSReadLine)가 보내는 win32 입력 모드 요청(ESC[?9001h/l)을 로컬 콘솔에 넘기지 않는다.

    넘기면 이 콘솔이 키를 `ESC[17;29;0;1;8;1_` 같은 숫자 형식으로 보내기 시작해 Ctrl+] 를 못 알아보고,
    브릿지의 세션도 평범한 VT 입력(웹과 같은 것)을 기대한다. 시퀀스가 두 덩이로 쪼개져 와도 놓치지 않는다."""
    _SEQS = (b"\x1b[?9001h", b"\x1b[?9001l")

    def __init__(self) -> None:
        self.held = b""

    def feed(self, data: bytes) -> bytes:
        data = self.held + data
        self.held = b""
        for seq in self._SEQS:
            data = data.replace(seq, b"")
        for n in range(len(self._SEQS[0]) - 1, 0, -1):          # 끝이 시퀀스의 앞부분이면 다음 덩이를 기다린다
            if any(data.endswith(seq[:n]) for seq in self._SEQS):
                self.held, data = data[-n:], data[:-n]
                break
        return data


def start_reader(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
    """콘솔 입력은 블로킹이라 스레드로 읽는다. 끝나면 프로세스째 종료하므로 데몬이다."""
    def run() -> None:
        buf = ctypes.create_unicode_buffer(4096)
        got = wintypes.DWORD()
        held = ""
        while k32.ReadConsoleW(H_IN, buf, 4096, ctypes.byref(got), None) and got.value:
            text = held + buf[:got.value]
            held = ""
            if text and "\ud800" <= text[-1] <= "\udbff":          # 서로게이트 쌍이 잘려 왔다 — 다음 읽기에 붙인다
                held, text = text[-1], text[:-1]
            if text:
                text = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
                loop.call_soon_threadsafe(queue.put_nowait, text)
        loop.call_soon_threadsafe(queue.put_nowait, None)
    threading.Thread(target=run, daemon=True).start()


async def attach(cfg: dict, name: str, agent: str | None) -> int:
    size = shutil.get_terminal_size((100, 30))
    params = {"cols": size.columns, "rows": size.lines, "hist": 0}   # hist=0 — 보이는 화면만(스크롤백은 터미널 창이 갖고 있다)
    if agent:
        params["agent"] = agent
    loop = asyncio.get_running_loop()
    inq: asyncio.Queue = asyncio.Queue()
    async with aiohttp.ClientSession(headers=cfg["headers"], cookies={"frog_token": cfg["token"]}) as s:
        try:
            ws = await s.ws_connect(f"{BASE}/ws/{name}", params=params, max_msg_size=0)
        except aiohttp.ClientConnectorError:
            die("브릿지가 안 떠 있다(127.0.0.1:7788) — 작업 스케줄러 'FrogTerminal Bridge' 를 확인한다", 1)
        except aiohttp.WSServerHandshakeError as e:
            die(f"붙지 못했다(HTTP {e.status}) — '{name}' 이 프로젝트 루트 아래 실제 폴더인지 확인한다", 1)
        why = "세션이 끝났다"
        with RawConsole():
            start_reader(loop, inq)

            async def pump_in() -> str:
                while (text := await inq.get()) is not None:
                    dbg(f"in {text!r}")
                    if DETACH in text:
                        before = text.split(DETACH, 1)[0]
                        if before:
                            await ws.send_bytes(before.encode("utf-8"))
                        return "떼었다(세션은 산다)"
                    await ws.send_bytes(text.encode("utf-8"))
                return "입력이 끊겼다"

            async def pump_out() -> str:
                strip = StripWin32InputMode()
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        if msg.data and (out := strip.feed(msg.data)):
                            write_out(out)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        break
                return "느려서 끊겼다 — 다시 붙는다" if ws.close_code == 1013 else "세션이 끝났다"

            async def watch_size() -> str:
                last = (size.columns, size.lines)
                while True:
                    await asyncio.sleep(0.25)
                    now = shutil.get_terminal_size((last[0], last[1]))
                    if (now.columns, now.lines) != last:
                        last = (now.columns, now.lines)
                        await ws.send_json({"type": "resize", "cols": last[0], "rows": last[1]})

            tasks = [asyncio.create_task(c()) for c in (pump_in, pump_out, watch_size)]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            first = next(iter(done))
            why = first.result() if first.exception() is None else "연결이 끊겼다"
            dbg(f"done {why!r} exc={first.exception()!r}")
            await ws.close()
            dbg("ws closed")
    sys.stdout.write(f"\r\n[frog] {name}: {why}\r\n")
    sys.stdout.flush()
    return 0


def main() -> int:
    args = sys.argv[1:]
    cfg = load_config()
    if args and args[0] in ("-l", "--list"):
        return asyncio.run(list_sessions(cfg))
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    name = args[0] if args else project_from_cwd(cfg["root"])
    agent = args[1] if len(args) > 1 else None
    if agent and agent not in AGENTS:
        die(f"에이전트는 claude · codex · shell 중 하나다(받은 값: {agent})")
    code = asyncio.run(attach(cfg, name, agent))
    sys.stdout.flush()
    os._exit(code)          # 블로킹 중인 입력 스레드가 다음 키를 훔치지 않게 프로세스째 끝낸다


if __name__ == "__main__":
    sys.exit(main())
