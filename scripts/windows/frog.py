"""frog — 이 PC 의 PowerShell 창을 프로그터미널 브릿지의 세션에 붙인다(tmux 의 윈도우판).

창 하나 = 세션 하나. 같은 프로젝트에서 frog 를 또 띄우면 그 프로젝트의 새 독립 세션(<프로젝트>@2 … @9 — 웹의 ＋ 탭과 같은 것)을 연다.
폰·다른 창에서 열어 둔 세션은 -a 로 골라 붙는다 — 같은 세션을 두 화면이 같이 보면 가장 작은 화면에 크기를 맞춘다.

  frog                   지금 폴더(프로젝트 루트 아래)에 새 창 — 그 프로젝트에 떠 있는 세션이 없으면 기본 세션(마지막 대화를 잇는다),
                         있으면 빈 번호 @2~@9 의 새 대화. 아무 화면도 안 붙은 세션이 남아 있으면 먼저 붙을지 묻는다(Enter = 새 창)
  frog <프로젝트>        그 프로젝트에 새 창(같은 규칙)
  frog <프로젝트> codex | claude | shell
  frog -a [프로젝트]     떠 있는 세션을 골라 붙는다(폰에서 연 세션 이어 쓰기 · 창을 닫아 남은 세션 되찾기)
  frog <프로젝트>@2      그 세션에 바로 붙는다
  frog -l                살아 있는 세션 목록(붙은 화면 수)

떼기: Ctrl+]  — 세션은 그대로 산다(폰·다른 창에서 계속 보인다). 창을 X 로 닫아도 같다 — frog -a 로 되찾는다.
세션을 끝내려면 안에서 exit 하거나 웹의 휴지통.
"""
import asyncio
import ctypes
import json
import os
import re
import shutil
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import aiohttp

APP = Path.home() / ".frogterminal"
BASE = os.environ.get("FROG_BRIDGE", "http://127.0.0.1:7788")      # 시험용 브릿지(다른 포트)에 붙일 때만 바꾼다
DETACH = "\x1d"                                   # Ctrl+]
AGENTS = {"claude", "codex", "shell"}
TAB_RE = re.compile(r"(?P<base>[\w.-]+)@(?P<n>[2-9])")            # 브릿지 sessions._TAB_RE 와 같은 규칙
SLOTS = range(2, 10)
# 원격 앱이 켜 둔 모드를 되돌린다 — 대체 화면 · 마우스 · 붙여넣기 괄호 · 포커스 · 커서 표시 · 서식
RESET_MODES = "\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?1004l\x1b[?9001l\x1b[?1l\x1b[?25h\x1b[0m"


class Taken(Exception):
    """새 창으로 고른 번호를 그새 다른 창이 먼저 열었다(브릿지 409) — 다음 번호로."""


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


def http(cfg: dict) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(headers=cfg["headers"], cookies={"frog_token": cfg["token"]})


async def fetch_live(cfg: dict) -> dict[str, dict]:
    """살아 있는 세션 이름 → {clients, created}. 탭(base@N)까지 이름별로 온다(브릿지 /api/sessions 의 live)."""
    async with http(cfg) as s:
        try:
            async with s.get(f"{BASE}/api/sessions") as r:
                if r.status != 200:
                    die(f"세션 목록을 못 읽었다(HTTP {r.status})", 1)
                data = await r.json()
        except aiohttp.ClientConnectorError:
            die("브릿지가 안 떠 있다(127.0.0.1:7788) — 작업 스케줄러 'FrogTerminal Bridge' 를 확인한다", 1)
    if "live" not in data:
        die("브릿지가 옛 판이라 세션별 목록이 없다 — scripts\\windows\\restart.ps1 로 다시 띄운다(열린 세션은 끝난다)", 1)
    return data["live"]


def of_project(live: dict[str, dict], base: str) -> dict[str, dict]:
    return {n: v for n, v in live.items() if n == base or ((m := TAB_RE.fullmatch(n)) and m["base"] == base)}


def slot_key(name: str) -> tuple:
    m = TAB_RE.fullmatch(name)
    return (m["base"], int(m["n"])) if m else (name, 1)


def describe(name: str, info: dict) -> str:
    mins = int((time.time() - info.get("created", time.time())) // 60)
    age = f"{mins}분" if mins < 60 else f"{mins // 60}시간 {mins % 60}분"
    seen = f"화면 {info['clients']}개 붙음" if info.get("clients") else "아무도 안 봄"
    return f"{name:<24}{seen:<14}{age} 전 시작"


def new_candidates(live: dict[str, dict], base: str) -> list[str]:
    """새 창이 열 이름들(앞에서부터 시도). 프로젝트에 떠 있는 세션이 있으면 기본 세션 자리는 쓰지 않는다 —
    기본 세션은 claude --continue 로 시작해서, 지금 다른 창이 쓰는 대화를 하나 더 열어 버린다(두 창이 한 대화)."""
    mine = of_project(live, base)
    names = [f"{base}@{n}" for n in SLOTS]
    return [n for n in ([] if mine else [base]) + names if n not in mine]


def choose(title: str, names: list[str], live: dict[str, dict], allow_new: bool) -> str | None:
    """번호로 고른다. allow_new 면 Enter 가 '새 창'(None)."""
    print(title)
    for i, n in enumerate(names, 1):
        print(f"  {i}) {describe(n, live[n])}")
    hint = "번호 · Enter=새 창" if allow_new else "번호 · Enter=그만"
    while True:
        try:
            got = input(f"{hint} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        if not got:
            if allow_new:
                return None
            sys.exit(0)
        if got.isdigit() and 1 <= int(got) <= len(names):
            return names[int(got) - 1]
        print(f"1~{len(names)} 중에서")


async def list_sessions(cfg: dict) -> int:
    live = await fetch_live(cfg)
    if not live:
        print("살아 있는 세션 없음")
    for n in sorted(live, key=slot_key):
        print(describe(n, live[n]))
    return 0


# ── 콘솔(Windows) ───────────────────────────────────────────────────────────
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetStdHandle.restype = wintypes.HANDLE
k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.ReadConsoleW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
k32.GetConsoleTitleW.argtypes = [wintypes.LPWSTR, wintypes.DWORD]
k32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
H_IN, H_OUT = k32.GetStdHandle(-10), k32.GetStdHandle(-11)
PROCESSED_INPUT, LINE_INPUT, ECHO_INPUT, VT_INPUT = 0x1, 0x2, 0x4, 0x200
PROCESSED_OUTPUT, VT_OUTPUT = 0x1, 0x4


def get_mode(handle) -> int | None:
    mode = wintypes.DWORD()
    return mode.value if k32.GetConsoleMode(handle, ctypes.byref(mode)) else None


class RawConsole:
    """키 입력을 가공 없이(VT) 받고 VT 출력을 켠다 — 끝나면 원래대로(창 제목까지)."""

    def __init__(self, title: str) -> None:
        self.title = title

    def __enter__(self):
        self.old_in, self.old_out = get_mode(H_IN), get_mode(H_OUT)
        if self.old_in is None or self.old_out is None:
            die("콘솔(PowerShell·Windows Terminal) 안에서 실행해야 한다 — 파이프·리다이렉트로는 못 붙는다")
        self.old_cp = (k32.GetConsoleCP(), k32.GetConsoleOutputCP())
        buf = ctypes.create_unicode_buffer(1024)
        self.old_title = buf.value if k32.GetConsoleTitleW(buf, 1024) else None
        k32.SetConsoleCP(65001)
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleMode(H_IN, (self.old_in & ~(PROCESSED_INPUT | LINE_INPUT | ECHO_INPUT)) | VT_INPUT)
        k32.SetConsoleMode(H_OUT, self.old_out | PROCESSED_OUTPUT | VT_OUTPUT)
        k32.SetConsoleTitleW(self.title)
        return self

    def __exit__(self, *exc):
        write_out(RESET_MODES.encode())
        k32.SetConsoleMode(H_IN, self.old_in)
        k32.SetConsoleMode(H_OUT, self.old_out)
        k32.SetConsoleCP(self.old_cp[0])
        k32.SetConsoleOutputCP(self.old_cp[1])
        if self.old_title is not None:
            k32.SetConsoleTitleW(self.old_title)


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


class TagTitle:
    """세션 안의 앱(클로드 등)이 바꾸는 창 제목(OSC 0/2) 앞에 세션 이름을 붙인다 — 창이 여럿일 때 어느 창이 어느 세션인지
    탭 제목만 보고 안다(frog -a 의 이름과 같다). 시퀀스가 덩이 사이에서 잘려 와도 놓치지 않는다."""
    _OPEN = re.compile(rb"\x1b\][02];")
    _END = re.compile(rb"\x07|\x1b\\")
    _HOLD_MAX = 4096                                    # 끝이 안 오는 시퀀스는 이만큼 모이면 그대로 내보낸다

    def __init__(self, name: str) -> None:
        self.tag = f"{name} · ".encode()
        self.held = b""

    def feed(self, data: bytes) -> bytes:
        data, self.held = self.held + data, b""
        out, pos = [], 0
        while (m := self._OPEN.search(data, pos)):
            end = self._END.search(data, m.end())
            if end is None:
                if len(data) - m.start() < self._HOLD_MAX:
                    self.held = data[m.start():]
                    data = data[:m.start()]
                break
            out.append(data[pos:m.end()] + self.tag + data[m.end():end.end()])
            pos = end.end()
        rest = data[pos:]
        if not self.held:                               # 끝이 여는 머리(ESC ] 0)의 앞부분이면 다음 덩이를 기다린다
            for n in (3, 2, 1):
                if rest.endswith(b"\x1b]0;"[:n]) or rest.endswith(b"\x1b]2;"[:n]):
                    self.held, rest = rest[-n:], rest[:-n]
                    break
        out.append(rest)
        return b"".join(out)


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


async def attach(cfg: dict, name: str, agent: str | None, new: bool = False) -> int:
    size = shutil.get_terminal_size((100, 30))
    params = {"cols": size.columns, "rows": size.lines, "hist": 0}   # hist=0 — 보이는 화면만(스크롤백은 터미널 창이 갖고 있다)
    if agent:
        params["agent"] = agent
    if new:
        params["new"] = 1                                          # 이미 있으면 붙지 말고 409(그새 다른 창이 연 번호)
    loop = asyncio.get_running_loop()
    inq: asyncio.Queue = asyncio.Queue()
    async with http(cfg) as s:
        try:
            ws = await s.ws_connect(f"{BASE}/ws/{name}", params=params, max_msg_size=0)
        except aiohttp.ClientConnectorError:
            die("브릿지가 안 떠 있다(127.0.0.1:7788) — 작업 스케줄러 'FrogTerminal Bridge' 를 확인한다", 1)
        except aiohttp.WSServerHandshakeError as e:
            if e.status == 409 and new:
                raise Taken(name) from None
            die(f"붙지 못했다(HTTP {e.status}) — '{name}' 이 프로젝트 루트 아래 실제 폴더인지 확인한다", 1)
        why = "세션이 끝났다"
        with RawConsole(f"{name} · frog"):
            start_reader(loop, inq)

            async def pump_in() -> str:
                while (text := await inq.get()) is not None:
                    dbg(f"in {text!r}")
                    if DETACH in text:
                        before = text.split(DETACH, 1)[0]
                        if before:
                            await ws.send_bytes(before.encode("utf-8"))
                        return "떼었다(세션은 산다 — frog -a 로 다시 붙는다)"
                    await ws.send_bytes(text.encode("utf-8"))
                return "입력이 끊겼다"

            async def pump_out() -> str:
                strip, tag = StripWin32InputMode(), TagTitle(name)
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        if msg.data and (out := tag.feed(strip.feed(msg.data))):
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


async def open_new(cfg: dict, base: str, agent: str | None) -> int:
    """새 창 — 빈 번호를 앞에서부터 연다. 아무도 안 보는 세션이 남아 있으면 먼저 붙을지 묻는다(Enter = 새 창)."""
    live = await fetch_live(cfg)
    idle = sorted((n for n, v in of_project(live, base).items() if not v.get("clients")), key=slot_key)
    if idle and sys.stdin.isatty():
        picked = choose(f"[frog] {base} 에 아무 화면도 안 붙은 세션이 있다 — 붙을까?", idle, live, allow_new=True)
        if picked:
            return await attach(cfg, picked, None)
    for name in new_candidates(live, base):
        try:
            return await attach(cfg, name, agent, new=True)
        except Taken:
            continue
    die(f"{base} 의 창이 꽉 찼다(@2~@9) — frog -a 로 떠 있는 세션에 붙거나 하나를 끝낸다", 1)


async def pick_existing(cfg: dict, target: str) -> int:
    live = await fetch_live(cfg)
    if target in live and TAB_RE.fullmatch(target):
        return await attach(cfg, target, None)
    names = sorted(of_project(live, target), key=lambda n: (bool(live[n].get("clients")), slot_key(n)))
    if not names:
        die(f"{target} 에 떠 있는 세션이 없다 — 새로 열려면 frog {target}", 1)
    if len(names) == 1:
        return await attach(cfg, names[0], None)
    return await attach(cfg, choose(f"[frog] {target} 에 떠 있는 세션", names, live, allow_new=False), None)


def main() -> int:
    args = sys.argv[1:]
    cfg = load_config()
    if args and args[0] in ("-l", "--list"):
        return asyncio.run(list_sessions(cfg))
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args and args[0] in ("-a", "--attach"):
        run = pick_existing(cfg, args[1] if len(args) > 1 else project_from_cwd(cfg["root"]))
    else:
        name = args[0] if args else project_from_cwd(cfg["root"])
        agent = args[1] if len(args) > 1 else None
        if agent and agent not in AGENTS:
            die(f"에이전트는 claude · codex · shell 중 하나다(받은 값: {agent})")
        if TAB_RE.fullmatch(name):
            run = attach(cfg, name, agent)                          # 번호를 콕 집었다 — 있으면 붙고 없으면 연다
        else:
            run = open_new(cfg, name, agent)
    code = asyncio.run(run)
    sys.stdout.flush()
    os._exit(code)          # 블로킹 중인 입력 스레드가 다음 키를 훔치지 않게 프로세스째 끝낸다


if __name__ == "__main__":
    sys.exit(main())
