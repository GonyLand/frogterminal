"""PC 에서 이미 떠 있는 클로드 창(Windows Terminal·VS Code 등 — frog 로 연 게 아님)을 폰에서 보고 조작하는 길.

두 가지를 한다.
  * list_windows() — 훅이 남긴 카드(inTmux 가 아닌 것)에서 pid 가 아직 도는 창만 골라 작업중·결재·대기·완료로 분류한다.
    overview()(decisions.py)는 일부러 frog 세션만 다루므로 건드리지 않고, 이쪽은 따로 읽는다.
  * ExtWindow — 창 하나의 콘솔 화면을 consolebridge 도우미(AttachConsole)로 읽어 xterm 이 그릴 ANSI 로 흘려보낸다.
    크기는 원래 창 것이 고정이다(남의 창이라 우리가 못 바꾼다) — 웹은 서버가 알려 주는 cols×rows 로 터미널을 맞춘다.
    키 입력은 웹에서 '입력 켜기'를 눌렀을 때만 도우미가 콘솔 입력 버퍼에 넣는다.

붙을 수 있는 pid 는 list_windows() 에 있는 것뿐이다 — 아무 프로세스에나 붙는 구멍을 만들지 않는다.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from bridge import decisions, sessions

LINGER_SECONDS = 15          # 마지막 화면이 떠난 뒤에도 도우미를 잠깐 둔다 — 폰이 끊겼다 바로 붙는 흔한 경우에 다시 띄우지 않으려고
CACHE_SECONDS = 2.0
REPO_ROOT = Path(__file__).resolve().parent.parent
DETACHED_PROCESS = 0x00000008

# ── 창 목록 ───────────────────────────────────────────────────────────────────
WAITING_KINDS = {"permission", "picker"}


def _state(kind: str | None) -> str:
    if kind == "working":
        return "working"
    if kind in WAITING_KINDS:
        return "waiting"          # 클로드가 사람의 승인·선택을 기다린다
    return "done"                 # idle/done — 한 턴이 끝났다


def list_windows() -> list[dict]:
    """살아 있는 외부 클로드 창들. 오래 안 건드린 것이 아래로 간다."""
    starts = sessions.process_starts()
    out: list[dict] = []
    for card in decisions._load_cards():
        if card.get("inTmux") is True:
            continue                                  # frog(tmux/ConPTY) 세션은 기존 목록이 맡는다
        pid, start = card.get("pid"), card.get("pidStart")
        if not isinstance(pid, int) or not isinstance(start, str):
            continue
        if starts is not None and starts.get(pid) != start:
            continue                                  # 이 pid 의 클로드는 이미 끝났다(또는 pid 가 재사용됐다)
        try:
            ctx = decisions.context_for(card)
        except Exception:
            ctx = {"asked": "", "said": "", "lastAt": ""}
        kind = card.get("kind")
        if kind != "working" and decisions._behind({**card, **ctx}):
            kind = "working"                          # 훅이 못 알린 사이 대화가 이어졌다
        out.append({
            "pid": pid, "sessionId": card.get("sessionId"), "project": Path(card.get("cwd") or "").name or card.get("project") or "?",   # 워크트리는 폴더 이름이 구분해 준다(project 는 모두 _claude_worktrees)
            "cwd": card.get("cwd") or "", "kind": kind, "state": _state(kind), "message": card.get("message") or "",
            "updatedAt": card.get("updatedAt") or "", "asked": ctx.get("asked", ""), "said": ctx.get("said", ""),
            "lastAt": ctx.get("lastAt", ""), "open": REGISTRY.is_open(pid),
        })
    out.sort(key=lambda w: (w["project"], w["pid"]))
    return out


class _Cache:
    def __init__(self) -> None:
        self._at = 0.0
        self._value: list[dict] = []
        self._lock = threading.Lock()

    def get(self) -> list[dict]:
        with self._lock:
            if time.monotonic() - self._at > CACHE_SECONDS:
                self._value = list_windows()
                self._at = time.monotonic()
            return self._value

    def invalidate(self) -> None:
        with self._lock:
            self._at = 0.0


CACHE = _Cache()


def summary(windows: list[dict] | None = None) -> dict:
    ws = CACHE.get() if windows is None else windows
    return {"total": len(ws), "working": sum(w["state"] == "working" for w in ws),
            "waiting": sum(w["state"] == "waiting" for w in ws), "done": sum(w["state"] == "done" for w in ws)}


# ── 창 하나의 화면 ────────────────────────────────────────────────────────────
class ExtSub:
    """브라우저 연결 하나 — ('b', bytes) 터미널 글자 · ('m', dict) 크기·제목 · None 끝."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.input_on = False          # 연결마다 기본은 '보기만' — 웹이 명시적으로 켜야 키가 들어간다

    def push(self, item) -> None:
        if self.closed:
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, item)
        except RuntimeError:
            self.closed = True


def _paint(lines: list[str], rows: range) -> bytes:
    parts = []
    for y in rows:
        parts.append(f"\x1b[{y + 1};1H{lines[y]}\x1b[K")
    return "".join(parts).encode("utf-8", "replace")


def _cursor(snap: dict) -> str:
    return f"\x1b[{snap['cy'] + 1};{snap['cx'] + 1}H" + ("\x1b[?25h" if snap.get("cv") else "\x1b[?25l")


class ExtWindow:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.alive = True
        self.lock = threading.RLock()
        self.subscribers: set[ExtSub] = set()
        self.snap: dict | None = None
        self._linger: threading.Timer | None = None
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "bridge.consolebridge", str(pid)], cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=DETACHED_PROCESS)
        threading.Thread(target=self._read_loop, name=f"extwin-{pid}", daemon=True).start()

    # 도우미 → 구독자
    def _read_loop(self) -> None:
        try:
            for raw in self.proc.stdout:
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if msg.get("t") == "screen":
                    self._on_screen(msg)
                elif msg.get("t") in ("gone", "error"):
                    break
        finally:
            self._finish()

    def _on_screen(self, snap: dict) -> None:
        with self.lock:
            old = self.snap
            self.snap = snap
            subs = list(self.subscribers)
        if not subs:
            return
        resized = old is None or (old["cols"], old["rows"]) != (snap["cols"], snap["rows"])
        if resized:
            meta = {"cols": snap["cols"], "rows": snap["rows"], "title": snap.get("title", "")}
            for s in subs:
                s.push(("m", meta))
                s.push(("b", self._full(snap)))
            return
        changed = [y for y in range(snap["rows"]) if snap["lines"][y] != old["lines"][y]]
        if old.get("title") != snap.get("title"):
            for s in subs:
                s.push(("m", {"cols": snap["cols"], "rows": snap["rows"], "title": snap.get("title", "")}))
        if changed or (old["cx"], old["cy"], old.get("cv")) != (snap["cx"], snap["cy"], snap.get("cv")):
            data = _paint(snap["lines"], changed).decode("utf-8") + "\x1b[0m" + _cursor(snap)
            payload = data.encode("utf-8", "replace")
            for s in subs:
                s.push(("b", payload))

    @staticmethod
    def _full(snap: dict) -> bytes:
        head = "\x1b[?1049l\x1b[0m\x1b[?7l\x1b[?25l\x1b[H\x1b[2J"
        body = _paint(snap["lines"], range(snap["rows"])).decode("utf-8")
        return (head + body + "\x1b[0m" + _cursor(snap)).encode("utf-8", "replace")

    def _finish(self) -> None:
        with self.lock:
            self.alive = False
            subs, self.subscribers = list(self.subscribers), set()
        for s in subs:
            s.push(("b", "\r\n\x1b[0m[창이 닫혔다]\r\n".encode("utf-8")))
            s.push(None)
        REGISTRY.forget(self)
        self.close()

    # 구독
    def attach(self, sub: ExtSub) -> None:
        with self.lock:
            if self._linger:
                self._linger.cancel()
                self._linger = None
            snap = self.snap
            self.subscribers.add(sub)
        if snap is not None:
            sub.push(("m", {"cols": snap["cols"], "rows": snap["rows"], "title": snap.get("title", "")}))
            sub.push(("b", self._full(snap)))

    def detach(self, sub: ExtSub) -> None:
        with self.lock:
            self.subscribers.discard(sub)
            if self.subscribers or not self.alive:
                return
            self._linger = threading.Timer(LINGER_SECONDS, self._idle_stop)
            self._linger.daemon = True
            self._linger.start()

    def _idle_stop(self) -> None:
        with self.lock:
            if self.subscribers:
                return
        self.close()

    def write(self, text: str) -> None:
        if not self.alive or not text:
            return
        try:
            self.proc.stdin.write((json.dumps({"t": "keys", "s": text}, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        self.alive = False
        try:
            self.proc.stdin.close()               # 도우미는 stdin 이 닫히면 스스로 끝난다
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        REGISTRY.forget(self)


class _Registry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._wins: dict[int, ExtWindow] = {}

    def is_open(self, pid: int) -> bool:
        with self._lock:
            w = self._wins.get(pid)
            return bool(w and w.alive and w.subscribers)

    def get_or_create(self, pid: int) -> ExtWindow | None:
        """list_windows() 에 있는 pid 만 연다. 없으면 None."""
        with self._lock:
            w = self._wins.get(pid)
            if w and w.alive:
                return w
        # 목록 확인은 레지스트리 락 밖에서 — 목록 갱신은 캐시 락을 쥔 채 is_open(레지스트리 락)을 부르니, 반대 순서로 잡으면 서로 물려 API 스레드가 다 멎는다.
        if not any(x["pid"] == pid for x in CACHE.get()):
            return None
        with self._lock:
            w = self._wins.get(pid)
            if w and w.alive:
                return w                                     # 그 사이 다른 연결이 먼저 열었다
            w = self._wins[pid] = ExtWindow(pid)
            return w

    def forget(self, win: ExtWindow) -> None:
        with self._lock:
            if self._wins.get(win.pid) is win:
                del self._wins[win.pid]

    def close_all(self) -> None:
        with self._lock:
            wins = list(self._wins.values())
        for w in wins:
            w.close()


REGISTRY = _Registry()
