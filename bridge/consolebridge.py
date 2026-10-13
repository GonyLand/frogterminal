"""이미 떠 있는 다른 콘솔 창의 화면을 읽고 키를 넣는 도우미 프로세스 — `python -m bridge.consolebridge <pid>`.

브릿지가 만든 ConPTY 가 아니라 Windows Terminal·VS Code 안에서 사용자가 직접 연 PowerShell/Claude 창을 폰에서 보려는 것이다.
콘솔 API(AttachConsole → ReadConsoleOutputW / WriteConsoleInputW)는 같은 사용자라면 남의 콘솔 버퍼도 읽고 쓴다(ConPTY 창 포함).
AttachConsole 은 프로세스당 하나뿐이라 창마다 이 도우미를 따로 띄운다 — 콘솔 없는 상태(DETACHED_PROCESS)로 떠야 붙을 수 있다.

약속(JSON 한 줄씩):
  stdout  {"t":"screen","cols","rows","cx","cy","cv","title","lines":[ANSI 한 줄씩]}   화면이 바뀔 때만
          {"t":"gone"}                                                                  창(프로세스)이 없어졌다 — 곧 끝난다
          {"t":"error","msg"}                                                           붙지 못했다 — 곧 끝난다
  stdin   {"t":"keys","s":"…"}   웹 터미널(xterm)이 내는 글자 그대로 — 아래 keys_to_records 가 키 이벤트로 바꿔 넣는다
          stdin 이 닫히면(브릿지가 끝나면) 도우미도 끝난다.

색은 콘솔의 16색표로만 읽힌다(24비트 색은 가장 가까운 칸으로 내려온다) — 글자·자리·반전·밑줄은 그대로다.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time

POLL_SECONDS = 0.2

# ── 화면 한 줄 → ANSI (순수 함수 — 콘솔 없이 시험한다) ───────────────────────────
FG_INTENSITY, BG_INTENSITY = 0x08, 0x80
REVERSE, UNDERLINE = 0x4000, 0x8000
LEADING, TRAILING = 0x0100, 0x0200          # 전각 글자의 앞칸·뒷칸 — 뒷칸은 건너뛴다
DEFAULT_COLORS = (7, 0)                      # 흰 글자·검은 바탕 = 터미널 기본색으로 본다


def _ansi_index(bits: int) -> int:
    """콘솔 색 비트(파랑 1 · 초록 2 · 빨강 4)를 ANSI 번호(빨강 1 · 초록 2 · 파랑 4)로."""
    return (1 if bits & 4 else 0) | (2 if bits & 2 else 0) | (4 if bits & 1 else 0)


def sgr(attr: int) -> str:
    fg_bits, bg_bits = attr & 0x07, (attr >> 4) & 0x07
    fg_bright, bg_bright = bool(attr & FG_INTENSITY), bool(attr & BG_INTENSITY)
    codes = ["0"]
    if (fg_bits | (8 if fg_bright else 0)) != 7:
        codes.append(str((90 if fg_bright else 30) + _ansi_index(fg_bits)))
    if bg_bits or bg_bright:
        codes.append(str((100 if bg_bright else 40) + _ansi_index(bg_bits)))
    if attr & REVERSE:
        codes.append("7")
    if attr & UNDERLINE:
        codes.append("4")
    return "\x1b[" + ";".join(codes) + "m"


def cells_to_ansi(cells: list[tuple[str, int]]) -> str:
    """(글자, 속성) 칸들 → 한 줄의 ANSI. 뒤쪽의 기본색 빈칸은 잘라 낸다(줄 끝은 \\x1b[K 가 지운다)."""
    end = len(cells)
    while end > 0:
        ch, attr = cells[end - 1]
        plain = ch in (" ", "\x00", "") and not attr & (REVERSE | UNDERLINE) and ((attr >> 4) & 0x0F) == 0
        if not plain:
            break
        end -= 1
    out: list[str] = []
    cur: int | None = None
    for ch, attr in cells[:end]:
        if attr & TRAILING:
            continue
        style = attr & (0x00FF | REVERSE | UNDERLINE)
        if style != cur:
            out.append(sgr(style))
            cur = style
        out.append(ch if ch not in ("\x00", "") else " ")
    return "".join(out) + "\x1b[0m"


# ── 웹 터미널의 글자 → 키 이벤트 (순수 함수) ───────────────────────────────────
SHIFT, LCTRL, LALT = 0x0010, 0x0008, 0x0002
VK = {"BACK": 0x08, "TAB": 0x09, "RETURN": 0x0D, "ESCAPE": 0x1B, "PRIOR": 0x21, "NEXT": 0x22, "END": 0x23, "HOME": 0x24,
      "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "INSERT": 0x2D, "DELETE": 0x2E}
CSI_LETTER = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT", "H": "HOME", "F": "END"}
CSI_TILDE = {1: "HOME", 2: "INSERT", 3: "DELETE", 4: "END", 5: "PRIOR", 6: "NEXT", 7: "HOME", 8: "END"}
CSI_FKEY = {11: 0x70, 12: 0x71, 13: 0x72, 14: 0x73, 15: 0x74, 17: 0x75, 18: 0x76, 19: 0x77, 20: 0x78, 21: 0x79, 23: 0x7A, 24: 0x7B}
SS3_FKEY = {"P": 0x70, "Q": 0x71, "R": 0x72, "S": 0x73}
_TOKEN = re.compile(r"\x1b\[(?P<p>[0-9;]*)(?P<f>[A-Za-z~])|\x1bO(?P<o>[A-Za-z])|\x1b(?P<alt>[\s\S])|(?P<ch>[\s\S])")


def _mods(param: str) -> int:
    """CSI 의 두 번째 숫자(1;5A 의 5)를 제어 키 상태로 — 2 시프트 · 3 알트 · 5 컨트롤."""
    parts = param.split(";")
    if len(parts) < 2 or not parts[1].isdigit():
        return 0
    m = int(parts[1]) - 1
    return (SHIFT if m & 1 else 0) | (LALT if m & 2 else 0) | (LCTRL if m & 4 else 0)


def keys_to_records(text: str) -> list[tuple[int, str, int]]:
    """xterm.js 가 보내는 글자 → [(가상키, 글자, 제어키상태)] — 눌림·뗌은 쓰는 쪽에서 쌍으로 만든다.

    한 번에 온 덩어리 안에서 ESC 단독은 Esc 키, ESC+글자는 Alt+글자, ESC[…/ESC O… 는 방향·기능키다.
    """
    out: list[tuple[int, str, int]] = []
    for m in _TOKEN.finditer(text):
        if m.group("f"):
            p, f = m.group("p"), m.group("f")
            mods = _mods(p)
            if f in CSI_LETTER:
                out.append((VK[CSI_LETTER[f]], "", mods))
            elif f == "Z":
                out.append((VK["TAB"], "", SHIFT))
            elif f == "~":
                n = int(p.split(";")[0] or 0) if p.split(";")[0].isdigit() else 0
                if n in CSI_TILDE:
                    out.append((VK[CSI_TILDE[n]], "", mods))
                elif n in CSI_FKEY:
                    out.append((CSI_FKEY[n], "", mods))
                # 200~/201~ (붙여넣기 표시)는 버린다 — 붙여넣은 글자만 들어간다
        elif m.group("o"):
            o = m.group("o")
            if o in CSI_LETTER:
                out.append((VK[CSI_LETTER[o]], "", 0))
            elif o in SS3_FKEY:
                out.append((SS3_FKEY[o], "", 0))
        elif m.group("alt") is not None:
            alt = m.group("alt")
            out.append((VK["ESCAPE"], "\x1b", 0) if alt == "\x1b" else (_vk_of(alt), alt, LALT))
        else:
            ch = m.group("ch")
            if ch == "\r":
                out.append((VK["RETURN"], "\r", 0))
            elif ch == "\n":
                out.append((VK["RETURN"], "\r", SHIFT))      # 붙여넣은 글자 속 줄바꿈 — 제출이 아니라 줄 바꿈으로
            elif ch in ("\x7f", "\b"):
                out.append((VK["BACK"], "\b", 0))
            elif ch == "\t":
                out.append((VK["TAB"], "\t", 0))
            elif ch == "\x1b":
                out.append((VK["ESCAPE"], "\x1b", 0))
            elif "\x01" <= ch <= "\x1a":
                out.append((0x40 + ord(ch), ch, LCTRL))      # Ctrl+A..Z — 글자는 제어문자 그대로
            else:
                out.append((_vk_of(ch), ch, SHIFT if ch.isupper() and ch.isascii() else 0))
    return out


def _vk_of(ch: str) -> int:
    if ch.isascii() and ch.isalpha():
        return ord(ch.upper())
    if ch.isascii() and ch.isdigit():
        return ord(ch)
    if ch == " ":
        return 0x20
    return 0            # 한글·기호 등 — 가상키 없이 글자만 넣는다(콘솔이 글자 그대로 받는다)


# ── 콘솔 접근 (윈도우 전용) ────────────────────────────────────────────────────
def _main(pid: int) -> int:
    import ctypes
    from ctypes import wintypes as wt

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u = ctypes.WinDLL("user32", use_last_error=True)

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short), ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

    class CSBI(ctypes.Structure):
        _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wt.WORD),
                    ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]

    class CHAR_INFO(ctypes.Structure):
        _fields_ = [("Char", wt.WCHAR), ("Attributes", wt.WORD)]

    class CURSOR_INFO(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("bVisible", wt.BOOL)]

    class KEY_EVENT(ctypes.Structure):
        _fields_ = [("bKeyDown", wt.BOOL), ("wRepeatCount", wt.WORD), ("wVirtualKeyCode", wt.WORD),
                    ("wVirtualScanCode", wt.WORD), ("uChar", wt.WCHAR), ("dwControlKeyState", wt.DWORD)]

    class _EVT(ctypes.Union):
        _fields_ = [("KeyEvent", KEY_EVENT), ("pad", ctypes.c_byte * 16)]

    class INPUT_RECORD(ctypes.Structure):
        _fields_ = [("EventType", wt.WORD), ("Event", _EVT)]

    k.OpenProcess.restype = wt.HANDLE
    k.CreateFileW.restype = wt.HANDLE
    invalid = wt.HANDLE(-1).value

    def emit(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def alive() -> bool:
        h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wt.DWORD()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259         # STILL_ACTIVE

    k.FreeConsole()
    if not alive():
        emit({"t": "gone"})
        return 0
    if not k.AttachConsole(pid):
        emit({"t": "error", "msg": f"AttachConsole 실패 err={ctypes.get_last_error()}"})
        return 1
    GENERIC_RW, SHARE_RW, OPEN_EXISTING = 0xC0000000, 3, 3
    out_h = k.CreateFileW("CONOUT$", GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, 0, None)
    in_h = k.CreateFileW("CONIN$", GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, 0, None)
    if out_h in (None, invalid):
        emit({"t": "error", "msg": f"CONOUT$ 열기 실패 err={ctypes.get_last_error()}"})
        return 1

    pending: list[str] = []
    lock = threading.Lock()
    stop = threading.Event()

    def read_stdin() -> None:
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("t") == "keys" and isinstance(msg.get("s"), str):
                with lock:
                    pending.append(msg["s"])
        stop.set()                                     # 브릿지가 끝났다

    threading.Thread(target=read_stdin, daemon=True).start()

    def send_keys(text: str) -> None:
        recs: list[tuple[int, str, int]] = []
        for vk, ch, state in keys_to_records(text):
            if len(ch) == 1 and ord(ch) > 0xFFFF:             # 이모지 등 — UTF-16 대용쌍 두 개로 넣는다(WCHAR 는 16비트)
                pair = ch.encode("utf-16-le")
                recs += [(0, pair[i:i + 2].decode("utf-16-le", "surrogatepass"), state) for i in (0, 2)]
            else:
                recs.append((vk, ch or "\x00", state))      # 방향키처럼 글자 없는 키는 NUL
        if in_h in (None, invalid) or not recs:
            return
        arr = (INPUT_RECORD * (len(recs) * 2))()
        i = 0
        for vk, ch, state in recs:
            scan = u.MapVirtualKeyW(vk, 0) if vk else 0
            for down in (True, False):
                r = arr[i]
                r.EventType = 1
                ke = r.Event.KeyEvent
                ke.bKeyDown = down
                ke.wRepeatCount = 1
                ke.wVirtualKeyCode = vk
                ke.wVirtualScanCode = scan
                ke.uChar = ch
                ke.dwControlKeyState = state
                i += 1
        written = wt.DWORD()
        k.WriteConsoleInputW(in_h, arr, len(arr), ctypes.byref(written))

    def read_screen() -> dict | None:
        info = CSBI()
        if not k.GetConsoleScreenBufferInfo(out_h, ctypes.byref(info)):
            return None
        w = info.srWindow.Right - info.srWindow.Left + 1
        rows = info.srWindow.Bottom - info.srWindow.Top + 1
        lines: list[str] = []
        for y in range(info.srWindow.Top, info.srWindow.Bottom + 1):
            buf = (CHAR_INFO * w)()
            region = SMALL_RECT(info.srWindow.Left, y, info.srWindow.Right, y)
            if not k.ReadConsoleOutputW(out_h, buf, COORD(w, 1), COORD(0, 0), ctypes.byref(region)):
                return None
            lines.append(cells_to_ansi([(c.Char, c.Attributes) for c in buf]))
        cur = CURSOR_INFO()
        k.GetConsoleCursorInfo(out_h, ctypes.byref(cur))
        title = ctypes.create_unicode_buffer(256)
        k.GetConsoleTitleW(title, 256)
        return {"t": "screen", "cols": w, "rows": rows, "title": title.value, "lines": lines,
                "cx": info.dwCursorPosition.X - info.srWindow.Left, "cy": info.dwCursorPosition.Y - info.srWindow.Top,
                "cv": bool(cur.bVisible)}

    last: dict | None = None
    last_alive_check = 0.0
    while not stop.is_set():
        with lock:
            chunks, pending[:] = list(pending), []
        for text in chunks:
            send_keys(text)
        snap = read_screen()
        if snap is None:
            emit({"t": "gone"})
            return 0
        if snap != last:
            emit(snap)
            last = snap
        now = time.monotonic()
        if now - last_alive_check > 2:
            last_alive_check = now
            if not alive():
                emit({"t": "gone"})
                return 0
        stop.wait(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("usage: python -m bridge.consolebridge <pid>")
    sys.exit(_main(int(sys.argv[1])))
