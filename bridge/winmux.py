"""Windows 세션 호스트 — 맥판의 tmux 자리를 ConPTY(pywinpty) + 화면 모델(pyte) 로 채운다.

맥판은 프로젝트마다 tmux 세션 1개를 두고 브라우저는 그 세션에 붙었다 떨어진다. 윈도우에는 tmux 가
없으니 브릿지 프로세스가 세션을 직접 들고 있는다:

  * 세션 = ConPTY 로 띄운 자식 프로세스(pwsh → claude/codex) 하나.
  * 브라우저 연결(WebSocket)은 세션에 구독자로 붙고 떨어진다 — 폰이 잠들거나 탭을 닫아도 자식은 계속 돈다.
  * 자식이 내는 글자는 pyte 화면에도 먹인다. 새로 붙는 쪽에는 그 화면(+스크롤백)을 ANSI 로 다시 그려
    보내므로 tmux attach 와 같이 "그 자리"가 뜬다. 대체 화면(vim·codex 의 alt screen)도 따로 들고 있다.

한계(맥판과 다른 점): 세션이 브릿지 프로세스에 묶여 있어서 브릿지를 재시작하거나 PC 를 재부팅하면
세션은 끝난다. 이어서 시작(`--continue`)이 대화를 잇는다.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import threading
import time
from collections import deque

import psutil
import pyte

SCROLLBACK_LINES = 2000                      # 새로 붙는 쪽에 되살려 줄 스크롤백 줄 수(pyte 에 보관)
MAX_QUEUE_BYTES = 8 * 1024 * 1024            # 구독자 하나가 밀릴 수 있는 한도 — 넘으면 끊고 다시 붙게 한다
READ_CHUNK = 65536

# 대체 화면 진입·이탈 — pyte 는 모르는 모드라 우리가 두 화면으로 나눠 먹인다.
_ALT_RE = re.compile(r"\x1b\[\?(?:1049|1047|47)([hl])")
_ALT_SEQS = tuple(f"\x1b[?{n}{c}" for n in (1049, 1047, 47) for c in "hl")
_ALT_MAX = max(map(len, _ALT_SEQS))
# 다시 붙을 때 되살려 줄 DEC 비공개 모드(마우스 리포트·붙여넣기 괄호·포커스 리포트·커서키)
_PRIVATE_RE = re.compile(r"\x1b\[\?([0-9;]+)([hl])")
_TRACKED_MODES = (1, 1000, 1002, 1003, 1004, 1005, 1006, 1015, 2004)
_TAIL_KEEP = 24                              # 청크 경계에서 잘린 시퀀스를 잡기 위한 꼬리 길이

_COLOR_CODES = {"black": 0, "red": 1, "green": 2, "brown": 3, "blue": 4,
                "magenta": 5, "cyan": 6, "white": 7}


class _Screen(pyte.HistoryScreen):
    """DECCOLM(80/132 열 전환)을 무시하는 pyte 화면 — 크기는 브라우저가 정한다."""

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            modes = tuple(m for m in modes if m != 3)
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            modes = tuple(m for m in modes if m != 3)
        super().reset_mode(*modes, **kwargs)

    def _blank_row(self, y: int) -> bool:
        row = self.buffer.get(y)
        return not row or all(c.data in (" ", "") and c.bg == "default" and not c.reverse
                              for c in row.values())

    def resize(self, lines=None, columns=None):
        """높이를 바꿀 때 xterm.js·conhost 와 같게 — 줄어들면 위쪽 줄을 스크롤백으로 올리고(커서는 보이게),
        늘어나면 스크롤백의 마지막 줄들을 위로 되돌려 채운다. pyte 기본은 위쪽 줄을 그냥 지워서,
        리사이즈 뒤 ConPTY 가 보내는 부분 갱신과 어긋나 화면 복원이 깨졌다(실측)."""
        lines = lines or self.lines
        if lines < self.lines:
            used = max([self.cursor.y] + [y for y in range(self.lines) if not self._blank_row(y)])
            push = max(0, used + 1 - lines)               # 쓰인 줄이 새 높이를 넘는 만큼만 위로 올린다
            for y in range(push):
                self.history.top.append(self.buffer[y])
            for y in range(self.lines):
                if y + push < self.lines and y < lines:
                    self.buffer[y] = self.buffer[y + push]
                else:
                    self.buffer.pop(y, None)
            for y in [k for k in self.buffer if k >= lines]:
                del self.buffer[y]
            self.cursor.y = max(0, min(lines - 1, self.cursor.y - push))
            self.dirty.update(range(lines))
            self.lines = lines
        elif lines > self.lines and self.history.top:
            pull = min(lines - self.lines, len(self.history.top))
            for y in range(self.lines - 1, -1, -1):        # 아래로 밀고
                self.buffer[y + pull] = self.buffer[y]
            for y in range(pull - 1, -1, -1):              # 위에 스크롤백 줄을 되돌린다
                self.buffer[y] = self.history.top.pop()
            self.cursor.y += pull
            self.dirty.update(range(lines))
        super().resize(lines, columns)


def _color_sgr(color: str, base: int) -> str | None:
    """pyte 색 이름 → SGR 번호 조각. base = 30(전경) 또는 40(배경). 기본색이면 None."""
    if color == "default":
        return None
    if color.startswith("bright") and color[6:] in _COLOR_CODES:
        return str(base + 60 + _COLOR_CODES[color[6:]])
    if color in _COLOR_CODES:
        return str(base + _COLOR_CODES[color])
    if re.fullmatch(r"[0-9a-fA-F]{6}", color):
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        return f"{base + 8};2;{r};{g};{b}"
    return None


def _sgr(ch) -> str:
    parts = ["0"]
    if ch.bold:
        parts.append("1")
    if ch.italics:
        parts.append("3")
    if ch.underscore:
        parts.append("4")
    if ch.blink:
        parts.append("5")
    if ch.reverse:
        parts.append("7")
    if ch.strikethrough:
        parts.append("9")
    for part in (_color_sgr(ch.fg, 30), _color_sgr(ch.bg, 40)):
        if part:
            parts.append(part)
    return "\x1b[" + ";".join(parts) + "m"


def render_line(line, columns: int) -> str:
    """pyte 한 줄(열 번호 → Char) → ANSI 글자열. 끝의 기본색 공백은 버린다. 너비 2 글자의 빈 뒷칸은 건너뛴다."""
    cells = [line[x] for x in range(columns)]
    end = columns
    while end > 0 and cells[end - 1].data in (" ", "") and \
            cells[end - 1].bg == "default" and not cells[end - 1].reverse:
        end -= 1
    out: list[str] = []
    cur = None
    for x in range(end):
        ch = cells[x]
        if ch.data == "":                    # 너비 2 글자(한글)의 뒤쪽 칸
            continue
        style = (ch.fg, ch.bg, ch.bold, ch.italics, ch.underscore, ch.strikethrough, ch.reverse, ch.blink)
        if style != cur:
            out.append(_sgr(ch))
            cur = style
        out.append(ch.data)
    if cur is not None and cur != ("default", "default", False, False, False, False, False, False):
        out.append("\x1b[0m")
    return "".join(out)


class Subscriber:
    """브라우저 연결 하나 — 스레드에서 오는 글자를 이벤트 루프의 큐로 넘긴다."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.pending = 0                     # 큐에 쌓인 바이트(루프 스레드에서만 만진다)
        self.overflowed = False
        self.closed = False
        self.size: tuple[int, int] | None = None     # 이 화면이 바라는 (cols, rows) — 세션은 붙은 화면 중 가장 작은 쪽에 맞춘다

    def push(self, data: bytes | None) -> None:
        """아무 스레드에서나 부른다."""
        try:
            self.loop.call_soon_threadsafe(self._put, data)
        except RuntimeError:                 # 루프가 이미 닫혔다
            self.closed = True

    def _put(self, data: bytes | None) -> None:
        if self.closed:
            return
        if data is None:
            self.queue.put_nowait(None)
            return
        if self.pending + len(data) > MAX_QUEUE_BYTES:
            self.overflowed = True           # 따라가지 못한다 — 끊고 다시 붙으면 화면을 새로 받는다
            self.closed = True
            self.queue.put_nowait(None)
            return
        self.pending += len(data)
        self.queue.put_nowait(data)

    def consumed(self, n: int) -> None:
        self.pending = max(0, self.pending - n)


class Session:
    def __init__(self, name: str, pty, cols: int, rows: int, argv: list[str], cwd: str | None):
        self.name = name
        self.pty = pty
        self.argv = argv
        self.cwd = cwd
        self.cols, self.rows = cols, rows
        self.created = time.time()
        self.alive = True
        self.exit_code: int | None = None
        self.compact = False
        self.lock = threading.RLock()        # 화면 모델·구독자 목록·모드 표를 지킨다
        self.subscribers: set[Subscriber] = set()
        self.main = _Screen(cols, rows, history=SCROLLBACK_LINES)
        self.alt = _Screen(cols, rows, history=0)
        self.main_stream = pyte.Stream(self.main)
        self.alt_stream = pyte.Stream(self.alt)
        self.in_alt = False
        self.modes: dict[int, bool] = {}
        self._tail = ""
        self._hold = ""                      # 잘려 온 대체 화면 시퀀스의 앞조각(pyte 에 먹이기 전)
        self._thread = threading.Thread(target=self._pump, name=f"winmux-{name}", daemon=True)

    # ── 자식 → 화면·구독자 ───────────────────────────────────────────────
    def start(self) -> None:
        self._thread.start()

    def _pump(self) -> None:
        try:
            while True:
                data = self.pty.read(READ_CHUNK)
                if not data:
                    if not self.pty.isalive():
                        break
                    time.sleep(0.01)
                    continue
                self.feed(data)
        except (EOFError, OSError):          # 자식이 끝났거나 우리가 kill 해서 ConPTY 소켓이 닫혔다 — 정상 종료다
            pass
        except Exception:                    # 읽기 스레드가 소리 없이 죽으면 세션이 유령이 된다
            import logging
            logging.getLogger("frogterminal").exception("winmux 읽기 실패 session=%s", self.name)
        self._finish()

    def feed(self, text: str) -> None:
        raw = text.encode("utf-8", "replace")
        with self.lock:
            self._track_modes(text)
            self._feed_screens(text)
            for sub in tuple(self.subscribers):
                sub.push(raw)

    def _feed_screens(self, text: str) -> None:
        # 청크 끝에서 대체 화면 시퀀스가 잘려 오면 다음 청크까지 그 꼬리만 붙들어 둔다(구독자 쪽 원본은 지연 없음).
        text = self._hold + text
        self._hold = ""
        for k in range(min(len(text), _ALT_MAX), 0, -1):
            tail = text[-k:]
            if any(seq.startswith(tail) and seq != tail for seq in _ALT_SEQS):
                self._hold, text = tail, text[:-k]
                break
        pos = 0
        for m in _ALT_RE.finditer(text):
            self._stream().feed(text[pos:m.start()])
            pos = m.end()
            if m.group(1) == "h":
                if not self.in_alt:
                    self.alt.reset()
                    self.alt.resize(self.rows, self.cols)
                self.in_alt = True
            else:
                self.in_alt = False
        self._stream().feed(text[pos:])

    def _stream(self) -> pyte.Stream:
        return self.alt_stream if self.in_alt else self.main_stream

    def _track_modes(self, text: str) -> None:
        window = self._tail + text
        for m in _PRIVATE_RE.finditer(window):
            on = m.group(2) == "h"
            for num in m.group(1).split(";"):
                if num.isdigit() and int(num) in _TRACKED_MODES:
                    self.modes[int(num)] = on
        self._tail = window[-_TAIL_KEEP:]

    def _finish(self) -> None:
        self.alive = False
        try:
            self.exit_code = self.pty.exitstatus if hasattr(self.pty, "exitstatus") else None
        except Exception:
            self.exit_code = None
        note = f"\r\n\x1b[0m\x1b[2m[프로세스 종료 · 코드 {self.exit_code}]\x1b[0m\r\n".encode()
        with self.lock:
            subs = tuple(self.subscribers)
        for sub in subs:
            sub.push(note)
            sub.push(None)
        MUX._forget(self)

    # ── 다시 붙을 때 그릴 화면 ───────────────────────────────────────────
    def snapshot(self, with_history: bool) -> bytes:
        """지금 화면을 ANSI 로. with_history = 스크롤백도 함께(새로 연 브라우저 탭용)."""
        with self.lock:
            out = ["\x1b[?1049l", "\x1b[0m", "\x1b[?25l", "\x1b[H", "\x1b[2J"]
            lines: list[str] = []
            if with_history:
                lines += [render_line(l, self.cols) for l in self.main.history.top]
            lines += [render_line(self.main.buffer[y], self.cols) for y in range(self.rows)]
            out.append("\r\n".join(lines))
            if self.in_alt:
                out += ["\x1b[?1049h", "\x1b[H", "\x1b[2J"]
                out.append("\r\n".join(render_line(self.alt.buffer[y], self.cols) for y in range(self.rows)))
            screen = self.alt if self.in_alt else self.main
            out.append(f"\x1b[0m\x1b[{screen.cursor.y + 1};{screen.cursor.x + 1}H")
            for num in _TRACKED_MODES:
                if num in self.modes:
                    out.append(f"\x1b[?{num}{'h' if self.modes[num] else 'l'}")
            out.append("\x1b[?25l" if screen.cursor.hidden else "\x1b[?25h")
            return "".join(out).encode("utf-8", "replace")

    def attach(self, sub: Subscriber, with_history: bool) -> None:
        """스냅샷을 큐 맨 앞에 넣고 구독자로 등록한다 — 둘 사이에 글자가 새지 않게 한 잠금 안에서."""
        with self.lock:
            data = self.snapshot(with_history)
            sub._put(data)
            self.subscribers.add(sub)

    def detach(self, sub: Subscriber) -> None:
        with self.lock:
            self.subscribers.discard(sub)
        self.fit()                               # 작은 화면(폰)이 떠나면 남은 화면 크기로 되돌린다

    def set_size(self, sub: Subscriber, cols: int, rows: int) -> None:
        sub.size = (cols, rows)
        self.fit(extra=sub)

    def fit(self, extra: Subscriber | None = None) -> None:
        """붙어 있는 화면(PC 창·폰)이 여럿이면 가장 작은 쪽에 맞춘다 — tmux 와 같다. 누구도 크기를 안 밝혔으면 그대로 둔다."""
        with self.lock:
            sizes = [s.size for s in self.subscribers if s.size and not s.closed]
        if extra is not None and extra.size and extra not in self.subscribers:
            sizes.append(extra.size)
        if sizes:
            self.resize(min(c for c, _ in sizes), min(r for _, r in sizes))

    # ── 브라우저 → 자식 ─────────────────────────────────────────────────
    def write(self, data: bytes | str) -> None:
        if not self.alive:
            return
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
        try:
            self.pty.write(text)
        except (EOFError, OSError):
            pass

    def resize(self, cols: int, rows: int) -> None:
        if not self.alive or (cols, rows) == (self.cols, self.rows):
            return
        with self.lock:
            self.cols, self.rows = cols, rows
            try:
                self.pty.setwinsize(rows, cols)
            except (EOFError, OSError):
                pass
            self.main.resize(rows, cols)
            self.alt.resize(rows, cols)

    def screen_text(self) -> str:
        """지금 보이는 화면의 글자만(tmux capture-pane -p 와 같은 모양)."""
        with self.lock:
            screen = self.alt if self.in_alt else self.main
            return "\n".join(line.rstrip() for line in screen.display)

    def agent(self) -> str | None:
        """이 세션 안에서 지금 도는 에이전트 — 코덱스만 알아본다(맥판 session_agents 와 같은 약속)."""
        try:
            root = psutil.Process(self.pty.pid)
            for proc in [root, *root.children(recursive=True)]:
                try:
                    name = (proc.name() or "").lower()
                    if name in ("codex.exe", "codex"):
                        return "codex"
                    cmdline = proc.cmdline()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                if name in ("node.exe", "node") and any(
                        re.search(r"[\\/]@openai[\\/]codex[\\/]", a) for a in cmdline):
                    return "codex"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return None

    def kill(self) -> None:
        """자식 트리를 통째로 끝낸다 — ConPTY 만 닫으면 손자(node 등)가 남는다."""
        self.alive = False                   # 목록에서 바로 사라진다(읽기 스레드의 마무리는 뒤따라 온다)
        pid = self.pty.pid
        victims: list[psutil.Process] = []
        try:
            root = psutil.Process(pid)
            victims = root.children(recursive=True) + [root]
        except psutil.NoSuchProcess:
            pass
        for p in victims:
            try:
                p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _, alive = psutil.wait_procs(victims, timeout=3)
        for p in alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            self.pty.close(force=True)
        except Exception:
            pass


class Mux:
    """브릿지 하나에 하나뿐인 세션 표."""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}

    def names(self) -> list[str]:
        with self._lock:
            return [n for n, s in self._sessions.items() if s.alive]

    def live(self) -> dict[str, dict]:
        """살아 있는 세션마다 붙은 화면 수와 만든 시각 — frog 가 빈 번호·아무도 안 보는 세션을 고를 때 쓴다."""
        with self._lock:
            alive = [s for s in self._sessions.values() if s.alive]
        out = {}
        for s in alive:
            with s.lock:
                out[s.name] = {"clients": len(s.subscribers), "created": s.created}
        return out

    def get(self, name: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(name)
            return s if s is not None and s.alive else None

    def create(self, name: str, argv: list[str], cwd: str | None, cols: int, rows: int,
               env: dict[str, str] | None = None) -> Session:
        from winpty import PtyProcess
        exe = shutil.which(argv[0]) or argv[0]
        full_env = {**os.environ, **(env or {})}
        for key in [k for k in full_env if k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_")]:
            del full_env[key]                # 브릿지를 클로드 안에서 띄웠어도 자식은 중첩 세션이 아니다
        full_env.update(TERM="xterm-256color", COLORTERM="truecolor", FROGTERMINAL_SESSION=name)
        pty = PtyProcess.spawn([exe, *argv[1:]], cwd=cwd, env=full_env, dimensions=(rows, cols))
        session = Session(name, pty, cols, rows, argv, cwd)
        with self._lock:
            self._sessions[name] = session
        session.start()
        return session

    def get_or_create(self, name: str, argv: list[str], cwd: str | None, cols: int, rows: int,
                      env: dict[str, str] | None = None) -> tuple[Session, bool]:
        """(세션, 새로 만들었나). 동시에 두 연결이 와도 하나만 만든다."""
        with self._lock:
            existing = self.get(name)
            if existing is not None:
                return existing, False
            return self.create(name, argv, cwd, cols, rows, env), True

    def kill(self, name: str) -> bool:
        with self._lock:
            s = self._sessions.get(name)
        if s is None or not s.alive:
            return False
        s.kill()
        return True

    def kill_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            s.kill()

    def _forget(self, session: Session) -> None:
        with self._lock:
            if self._sessions.get(session.name) is session:
                del self._sessions[session.name]


MUX = Mux()


# ── tmux send-keys 인자를 글자로 옮긴다(decisions.send_answer 가 쓰는 모양만) ─────────
_KEY_NAMES = {"Enter": "\r", "Escape": "\x1b", "Tab": "\t", "Space": " ", "BSpace": "\x7f",
              "Up": "\x1b[A", "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D"}


def keys_to_text(keys: tuple[str, ...]) -> str:
    if keys and keys[0] == "-l":
        return keys[-1]
    return "".join(_KEY_NAMES.get(k, k) for k in keys)


def send_keys(name: str, *keys: str) -> None:
    session = MUX.get(name)
    if session is None:
        raise LookupError(name)
    session.write(keys_to_text(keys))


# ── ps 대용: pid → 시작시각 문자열 ──────────────────────────────────────────
def process_starts() -> dict[int, str]:
    out: dict[int, str] = {}
    for proc in psutil.process_iter(["pid", "create_time"]):
        ct = proc.info.get("create_time")
        if ct:
            out[proc.info["pid"]] = format_start(ct)
    return out


def format_start(epoch: float) -> str:
    """훅(hooks/frog_hook.py)과 브릿지가 같은 모양으로 적는 프로세스 시작 시각 — 로케일을 타지 않는다."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))


def boot_time() -> float | None:
    try:
        boot = psutil.boot_time()
    except Exception:
        return None
    return boot if boot <= time.time() else None
