"""브라우저 패널 2단계 — 프로젝트마다 진짜 크롬을 맥에서 띄우고(헤드리스·별도 프로필) CDP 로 화면·입력을 중계한다.

왜 이렇게 하나 (설계 = docs/2026-09-20-브라우저-패널-검토와-계획.md 11절):
  * iframe 은 외부 사이트가 거부하고 뒤로/앞으로·우클릭을 못 다룬다. 코덱스 앱·클로드 앱처럼 "앱 안의 진짜
    브라우저"를 웹에서 내려면 맥의 크롬 한 대를 화면으로 중계하는 길뿐이다.
  * 크롬은 `--headless=new`(사용자 화면에 창이 뜨지 않는다 — 2026-09-20 창 있는 실험이 사용자 화면에 네이버를
    띄운 사고) + `--remote-debugging-port=0`(루프백, 포트는 DevToolsActivePort 로 읽는다) + 프로젝트별
    `--user-data-dir`(크롬 136+ 는 기본 프로필에 디버깅 포트를 거부한다).
  * CDP 는 **페이지 전용 웹소켓**으로 붙는다 — 브라우저 소켓 + Target.attachToTarget 세션에서는 크롬 153 이
    Page.startScreencast 를 "Not attached to an active page" 로 거부했다(실측). 페이지 소켓은 0.6초에 10프레임.
  * 화면은 Page.startScreencast(JPEG) — 프레임마다 screencastFrameAck 를 즉시 보내고 **최신 프레임 하나만**
    들고 있는다(터미널 큐처럼 다 보존하면 느린 폰에서 지연이 쌓인다). 뷰어가 하나도 없으면 캐스트를 끈다.
  * 웹에는 원시 CDP 를 열지 않는다 — browser_api 가 받는 것은 이동·뒤로·앞으로·새로고침·클릭·키·글자·휠·
    뷰포트·탭뿐이고 여기서 CDP 로 번역한다(쿠키 추출·Runtime.evaluate 가 웹에 노출되지 않게).
  * 에이전트(플레이라이트·chrome-devtools MCP·클로드)는 같은 크롬의 브라우저 엔드포인트(http://127.0.0.1:<포트>)
    로 붙는다 — 사람과 같은 탭을 본다. 그 포트는 미리보기(preview) 노출 대상에서 뺀다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import aiohttp

from . import config, sessions

def _find_chrome() -> str:
    if not config.IS_WINDOWS:
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    import shutil
    candidates = [Path(os.environ.get(var, "")) / rel
                  for var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")
                  for rel in (r"Google\Chrome\Application\chrome.exe",)]
    return next((str(p) for p in candidates if p.is_file()), shutil.which("chrome") or str(candidates[0]))


CHROME_BIN = _find_chrome()
DEFAULT_W, DEFAULT_H = 1024, 700
JPEG_QUALITY = 70
BOOT_TIMEOUT_S = 15
# 메모리 정책 (2026-09-20 실측: 프로젝트 크롬 하나 = 8개 프로세스 · RSS 1.1~1.25GB, 닫으면 0 으로 회수 — 누수 없음)
# ★정책 변천(2026-09-20): 유휴 3분 종료(너무 짧아 **보던 브라우저가 꺼졌다** — 감사로그 reap idle 3건)
#   → 자동종료 폐기(이번엔 메모리가 계속 는다) → **10분**으로 확정. 셋으로 막는다:
#   ① 뷰어는 기본이 `start=0` — 살아 있으면 붙고, 없으면 "꺼져 있습니다"로 끝난다(띄우지 않는다).
#      크롬이 새로 서는 건 사용자가 [브라우저 열기]를 누르거나 API 로 명시할 때뿐이다.
#   ② 보는 뷰어 없이 IDLE_STOP_S(10분) 지나면 종료 — 보고 있거나 명령이 오면 시계가 다시 돈다(touch).
#   ③ 동시 MAX_BROWSERS 개 상한 — 새로 열 때 넘치면 '보는 사람 없는 것 중 가장 오래 논 것'만 끈다.
IDLE_STOP_S = 600
MAX_BROWSERS = 2


def chrome_root() -> Path:
    return config.SETTINGS_FILE.parent / "chrome"


def registry_file() -> Path:
    return config.SETTINGS_FILE.parent / "browsers.json"


def load_registry() -> dict[str, dict]:
    try:
        data = json.loads(registry_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_registry(reg: dict[str, dict]) -> None:
    registry_file().parent.mkdir(parents=True, exist_ok=True)
    registry_file().write_text(json.dumps(reg, ensure_ascii=False, indent=1))


def registry_ports() -> set[int]:
    """지금 장부에 있는 CDP 포트 — preview 가 '내줄 수 있는 포트'에서 뺀다."""
    return {int(e["port"]) for e in load_registry().values() if isinstance(e, dict) and "port" in e}


class BrowserError(Exception):
    pass


# ── 크롬 프로세스 ─────────────────────────────────────────────────────────────

def launch_chrome(project: str, w: int = DEFAULT_W, h: int = DEFAULT_H) -> tuple[int, int]:
    """프로젝트 프로필로 헤드리스 크롬을 띄우고 (pid, CDP 포트)를 돌려준다. 이미 살아 있으면 그것을 쓴다."""
    if not sessions.is_valid_name(project):
        raise BrowserError("잘못된 프로젝트 이름")
    reg = load_registry()
    entry = reg.get(project)
    if entry and _alive(entry.get("pid")) and _cdp_ok(entry.get("port")):
        return int(entry["pid"]), int(entry["port"])
    prof = chrome_root() / project
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "DevToolsActivePort").unlink(missing_ok=True)      # 낡은 포트 파일 — 옛 포트에 붙는 사고 방지
    if not os.path.exists(CHROME_BIN):
        raise BrowserError("구글 크롬이 설치돼 있지 않습니다")
    popen_kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                if config.IS_WINDOWS else {"start_new_session": True})
    gpu_flag = "--use-angle=d3d11" if config.IS_WINDOWS else "--use-angle=metal"
    proc = subprocess.Popen(
        [CHROME_BIN, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={prof}",
         "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
         # 디버깅 포트를 열면 navigator.webdriver 가 true 가 되고 구글이 그것만 보고 봇 확인(sorry)을 낸다(2026-09-20 실측:
         # 이 플래그 + 아래 UA 메타 보정으로 "네이버 - Google 검색" 정상). 속이는 게 아니라 '자동화 표식'만 끄는 것.
         "--disable-blink-features=AutomationControlled", gpu_flag, "--enable-unsafe-swiftshader",
         "--disable-extensions", "--disable-component-update", "--mute-audio", "--renderer-process-limit=4",
         # 스크린캐스트는 Emulation 의 deviceScaleFactor 를 무시한다(실측) — 실행 플래그로 배율 2 를 박아야 레티나에서
         # 선명한 2배 프레임이 온다. 배율 1 뷰어(윈도우 등)에는 캐스트 maxWidth 를 CSS 폭으로 주면 크롬이 줄여서 보낸다.
         "--force-device-scale-factor=2",
         f"--window-size={w},{h}", "--lang=ko-KR", "--hide-scrollbars=false", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen_kw)
    t0 = time.time()
    while not (prof / "DevToolsActivePort").exists():
        if proc.poll() is not None:
            raise BrowserError("크롬이 곧바로 종료됐습니다(프로필이 다른 크롬에 잠겨 있을 수 있음)")
        if time.time() - t0 > BOOT_TIMEOUT_S:
            proc.terminate()
            raise BrowserError("크롬 디버깅 포트가 열리지 않습니다")
        time.sleep(0.05)
    port = int((prof / "DevToolsActivePort").read_text().split("\n")[0])
    reg[project] = {"pid": proc.pid, "port": port, "started": time.time()}
    save_registry(reg)
    return proc.pid, port


def _alive(pid) -> bool:
    try:
        if config.IS_WINDOWS:                    # 윈도우의 os.kill(pid, 0)은 CTRL_C_EVENT 를 보낸다 — 존재만 본다
            import psutil
            return psutil.pid_exists(int(pid))
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _cdp_ok(port) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def stop_chrome(project: str) -> bool:
    reg = load_registry()
    entry = reg.pop(project, None)
    save_registry(reg)
    if not entry:
        return False
    if config.IS_WINDOWS:                                # 크롬 프로세스 트리를 통째로 끝낸다
        import psutil
        try:
            root = psutil.Process(int(entry["pid"]))
            for p in root.children(recursive=True) + [root]:
                try:
                    p.kill()
                except psutil.Error:
                    pass
        except (psutil.Error, ValueError, TypeError):
            pass
        return True
    try:
        os.killpg(int(entry["pid"]), signal.SIGTERM)     # start_new_session 으로 띄웠으니 그룹째
    except (OSError, ValueError, TypeError):
        try:
            os.kill(int(entry["pid"]), signal.SIGTERM)
        except Exception:
            pass
    return True


# ── CDP 페이지 세션 ──────────────────────────────────────────────────────────

class PageSession:
    """한 프로젝트의 크롬에서 '지금 보는 탭' 하나에 붙은 CDP 연결 + 최신 프레임 보관소."""

    def __init__(self, project: str, port: int):
        self.project, self.port = project, port
        self.http: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.target_id: str | None = None
        self._n = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self.frame: bytes | None = None
        self.frame_meta: dict = {}
        self.frame_seq = 0
        self.frame_event = asyncio.Event()
        self.state_event = asyncio.Event()      # url·제목·탭이 바뀌었다 — 뷰어가 state 를 다시 보낸다
        self.viewers = 0
        self.casting = False
        self.viewport = (DEFAULT_W, DEFAULT_H)
        self.dpr = 1.0                          # 뷰어 화면의 픽셀 배율(레티나 2) — 이 배율로 그려 받아야 안 흐리다
        self.last_active = time.time()          # 마지막으로 뷰어가 있었거나 명령이 온 시각 — 유휴 종료의 근거
        self.lock = asyncio.Lock()

    def touch(self) -> None:
        self.last_active = time.time()

    async def connect(self, target_id: str | None = None) -> None:
        if self.http is None:
            self.http = aiohttp.ClientSession()
        pages = await self.targets()
        page = next((p for p in pages if p["id"] == target_id), None) if target_id else None
        if page is None:
            page = next((p for p in pages if p["type"] == "page"), None)
        if page is None:                        # 탭이 하나도 없다 — 하나 만든다
            async with self.http.put(f"http://127.0.0.1:{self.port}/json/new?about:blank") as r:
                page = await r.json()
        await self._detach()
        self.ws = await self.http.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=64 * 1024 * 1024)
        self.target_id = page["id"]
        self._reader = asyncio.create_task(self._read())
        await self.call("Page.enable")
        await self._fix_user_agent()
        await self.set_viewport(*self.viewport)
        if self.viewers:
            await self.start_cast()
        self.state_event.set()

    async def _fix_user_agent(self) -> None:
        """헤드리스 크롬은 UA 와 클라이언트 힌트(Sec-CH-UA 브랜드)에 'HeadlessChrome' 을 넣는다 — 구글이 sorry(봇 확인)
        페이지를 낸다(2026-09-20 실측: UA 문자열만 바꿔서는 부족, 브랜드 메타까지 같은 버전의 일반 크롬으로 맞춰야 통과).
        사이트를 속이려는 게 아니라 '헤드리스'라는 표시만 지우는 것이다 — 그 외 지문은 손대지 않는다."""
        import re
        try:
            async with self.http.get(f"http://127.0.0.1:{self.port}/json/version") as r:
                ua = (await r.json()).get("User-Agent", "")
            m = re.search(r"Chrome/(\d+)\.(\S+)", ua)
            if "HeadlessChrome" not in ua or not m:
                return
            major, full = m.group(1), f"{m.group(1)}.{m.group(2)}"
            brands = lambda v: [{"brand": "Chromium", "version": v}, {"brand": "Google Chrome", "version": v},
                                {"brand": "Not?A_Brand", "version": "24" if v == major else "24.0.0.0"}]
            await self.call("Network.enable")
            await self.call("Network.setUserAgentOverride", {
                "userAgent": ua.replace("HeadlessChrome", "Chrome"),
                "acceptLanguage": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7", "platform": "MacIntel",
                "userAgentMetadata": {"brands": brands(major), "fullVersionList": brands(full), "platform": "macOS",
                                      "platformVersion": "15.5.0", "architecture": "arm", "model": "", "mobile": False,
                                      "bitness": "64", "wow64": False}})
        except Exception:
            pass

    async def _detach(self) -> None:
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self.ws and not self.ws.closed:
            await self.ws.close()
        self.ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(BrowserError("연결이 끊겼습니다"))
        self._pending.clear()
        self.casting = False

    async def close(self) -> None:
        await self._detach()
        if self.http:
            await self.http.close()
            self.http = None

    async def targets(self) -> list[dict]:
        async with self.http.get(f"http://127.0.0.1:{self.port}/json") as r:
            data = await r.json()
        return [p for p in data if p.get("type") == "page"]

    async def call(self, method: str, params: dict | None = None, timeout: float = 15) -> dict:
        if not self.ws or self.ws.closed:
            raise BrowserError("브라우저에 연결돼 있지 않습니다")
        self._n += 1
        n = self._n
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[n] = fut
        await self.ws.send_json({"id": n, "method": method, "params": params or {}})
        try:
            res = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(n, None)
        if isinstance(res, dict) and "code" in res and "message" in res and "result" not in res:
            raise BrowserError(f"{method}: {res['message']}")
        return res

    async def _read(self) -> None:
        try:
            async for msg in self.ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                d = json.loads(msg.data)
                if "id" in d and d["id"] in self._pending:
                    fut = self._pending[d["id"]]
                    if not fut.done():
                        fut.set_result(d["result"] if "result" in d else d.get("error", {}))
                    continue
                m = d.get("method")
                if m == "Page.screencastFrame":
                    p = d["params"]
                    try:
                        await self.ws.send_json({"id": 0, "method": "Page.screencastFrameAck",
                                                 "params": {"sessionId": p["sessionId"]}})
                    except Exception:
                        pass
                    self.frame = base64.b64decode(p["data"])
                    self.frame_meta = p.get("metadata", {})
                    self.frame_seq += 1
                    self.frame_event.set()
                elif m in ("Page.frameNavigated", "Page.navigatedWithinDocument", "Page.loadEventFired"):
                    self.state_event.set()
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            self.casting = False
            self.state_event.set()

    # ── 화면 ──
    async def start_cast(self) -> None:
        if self.casting or not self.ws:
            return
        w, h = self.viewport
        # maxWidth/Height 는 물리 픽셀 — 배율만큼 크게 받아야 레티나에서 글자가 선명하다(실측: 배율 1 은 흐림)
        await self._call_retry("Page.startScreencast", {"format": "jpeg", "quality": JPEG_QUALITY,
                                                 "maxWidth": int(w * self.dpr), "maxHeight": int(h * self.dpr),
                                                 "everyNthFrame": 1})
        self.casting = True

    async def stop_cast(self) -> None:
        if not self.casting or not self.ws:
            return
        try:
            await self.call("Page.stopScreencast")
        finally:
            self.casting = False

    async def set_viewport(self, w: int, h: int, dpr: float | None = None) -> None:
        w, h = max(320, min(3000, int(w))), max(240, min(3000, int(h)))
        if dpr is not None:
            self.dpr = max(1.0, min(2.0, float(dpr)))      # 3배(폰)는 2 로 — 프레임 바이트가 배율 제곱으로 는다
        self.viewport = (w, h)
        await self._call_retry("Emulation.setDeviceMetricsOverride",
                        {"width": w, "height": h, "deviceScaleFactor": 2, "mobile": False})   # 실행 플래그와 같게
        if self.casting:                        # 캐스트 크기도 따라가야 글자가 안 뭉갠다
            await self.stop_cast()
            await self.start_cast()

    # ── 이동 ──
    async def navigate(self, url: str) -> None:
        await self.call("Page.navigate", {"url": url})

    async def _call_retry(self, method: str, params: dict | None = None, tries: int = 12) -> dict:
        """교차 출처 이동 중(프로세스 교체) 잠깐 "Not attached to an active page" 가 난다 — 그 순간만 짧게 기다렸다 다시."""
        for i in range(tries):
            try:
                return await self.call(method, params)
            except BrowserError as e:
                if "Not attached" not in str(e) or i == tries - 1:
                    raise
                await asyncio.sleep(0.1)

    async def history(self) -> dict:
        h = await self._call_retry("Page.getNavigationHistory")
        i, entries = h["currentIndex"], h["entries"]
        cur = entries[i] if 0 <= i < len(entries) else {}
        return {"url": cur.get("url", ""), "title": cur.get("title", ""),
                "canBack": i > 0, "canForward": i < len(entries) - 1, "_entries": entries, "_i": i}

    async def go(self, step: int) -> None:
        h = await self.history()
        j = h["_i"] + step
        if 0 <= j < len(h["_entries"]):
            await self.call("Page.navigateToHistoryEntry", {"entryId": h["_entries"][j]["id"]})

    async def reload(self, ignore_cache: bool = False) -> None:
        await self.call("Page.reload", {"ignoreCache": ignore_cache})

    async def state(self) -> dict:
        h = await self.history()
        tabs = await self.targets()
        return {"type": "state", "url": h["url"], "title": h["title"], "canBack": h["canBack"],
                "canForward": h["canForward"], "active": self.target_id,
                "tabs": [{"id": t["id"], "title": t.get("title", ""), "url": t.get("url", "")} for t in tabs],
                "viewport": list(self.viewport)}

    # ── 입력 ──
    async def mouse(self, kind: str, x: float, y: float, button: str = "left",
                    clicks: int = 1, modifiers: int = 0) -> None:
        if kind not in ("mousePressed", "mouseReleased", "mouseMoved"):
            raise BrowserError("잘못된 마우스 이벤트")
        await self.call("Input.dispatchMouseEvent", {
            "type": kind, "x": float(x), "y": float(y), "button": button if kind != "mouseMoved" else "none",
            "clickCount": int(clicks), "modifiers": int(modifiers)})

    async def wheel(self, x: float, y: float, dx: float, dy: float) -> None:
        await self.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": float(x), "y": float(y),
                                                     "deltaX": float(dx), "deltaY": float(dy)})

    async def key(self, kind: str, key: str, code: str = "", text: str = "",
                  vk: int = 0, modifiers: int = 0) -> None:
        if kind not in ("keyDown", "keyUp", "rawKeyDown", "char"):
            raise BrowserError("잘못된 키 이벤트")
        params = {"type": kind, "key": key, "code": code, "modifiers": int(modifiers)}
        if vk:
            params["windowsVirtualKeyCode"] = params["nativeVirtualKeyCode"] = int(vk)
        if text:
            params["text"] = params["unmodifiedText"] = text
        await self.call("Input.dispatchKeyEvent", params)

    async def insert_text(self, text: str) -> None:
        if text:
            await self.call("Input.insertText", {"text": text})

    # ── 탭 ──
    async def new_tab(self, url: str = "about:blank") -> str:
        async with self.http.put(f"http://127.0.0.1:{self.port}/json/new?{url}") as r:
            page = await r.json()
        await self.connect(page["id"])
        return page["id"]

    async def select_tab(self, target_id: str) -> None:
        await self.connect(target_id)

    async def close_tab(self, target_id: str) -> None:
        async with self.http.get(f"http://127.0.0.1:{self.port}/json/close/{target_id}") as r:
            await r.read()
        if target_id == self.target_id:
            await self.connect(None)
        else:
            self.state_event.set()


# ── 프로젝트별 세션 보관 ──────────────────────────────────────────────────────

class Browsers:
    """앱 하나에 프로젝트 → PageSession 하나. 웹소켓 뷰어 여럿이 같은 세션의 최신 프레임을 본다.
    메모리 정책(IDLE_STOP_S·MAX_BROWSERS)도 여기서 — reap() 을 주기적으로 부른다(browser_api 가 태스크로)."""

    def __init__(self, stop=None):
        self.sessions: dict[str, PageSession] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self._stop = stop or stop_chrome        # 테스트에서 진짜 kill 대신 기록용으로 바꾼다

    async def get(self, project: str, start: bool = True) -> PageSession | None:
        """start=False = '있으면 붙고, 없으면 None' — 안 연 프로젝트에 크롬을 새로 세우지 않는다.
        브릿지가 재시작돼 세션 객체가 없어도 장부의 크롬이 살아 있으면 그것에 다시 붙는다."""
        lock = self.locks.setdefault(project, asyncio.Lock())
        async with lock:
            s = self.sessions.get(project)
            if s and s.ws and not s.ws.closed:
                s.touch()
                return s
            entry = load_registry().get(project)
            alive = bool(entry and _alive(entry.get("pid")) and _cdp_ok(entry.get("port")))
            if not alive:
                if not start:
                    return None
                await self.make_room(project)
            _, port = await asyncio.to_thread(launch_chrome, project)   # 살아 있으면 그 포트를 그대로 돌려준다
            s = PageSession(project, port)
            await s.connect(None)
            self.sessions[project] = s
            return s

    def running(self) -> dict[str, dict]:
        """장부 기준으로 지금 살아 있는 크롬(브릿지 재시작 전 것 포함)."""
        return {p: e for p, e in load_registry().items() if isinstance(e, dict) and _alive(e.get("pid"))}

    async def make_room(self, incoming: str) -> list[str]:
        """새로 띄우기 전 — 살아 있는 크롬이 MAX_BROWSERS 이상이면 뷰어 없는 것 중 가장 오래 논 것부터 끈다."""
        alive = self.running()
        alive.pop(incoming, None)
        stopped = []
        while len(alive) >= MAX_BROWSERS:
            idle = [(self.sessions[p].last_active if p in self.sessions else e.get("started", 0), p)
                    for p, e in alive.items() if p not in self.sessions or self.sessions[p].viewers == 0]
            if not idle:
                break                           # 전부 누가 보는 중 — 그래도 새로 띄운다(끊는 것보다 낫다)
            _, victim = min(idle)
            await self.stop(victim)
            alive.pop(victim, None)
            stopped.append(victim)
        return stopped

    async def reap(self, now: float | None = None) -> list[str]:
        """뷰어 없이 IDLE_STOP_S(10분) 지난 크롬을 끄고, 죽은 장부 흔적도 함께 청소한다.

        '유휴'의 기준은 마지막으로 뷰어가 붙어 있었거나 명령이 온 시각(PageSession.last_active)이다 —
        보고 있는 동안에는 절대 꺼지지 않는다. 브릿지가 재시작돼 세션 객체가 없는 크롬은 장부의 시작 시각으로 잰다.
        """
        now = now if now is not None else time.time()
        gone = []
        for project, entry in list(self.running().items()):
            s = self.sessions.get(project)
            if s is not None:
                if s.viewers > 0 or now - s.last_active <= IDLE_STOP_S:
                    continue
            elif now - entry.get("started", now) <= IDLE_STOP_S:
                continue
            await self.stop(project)
            gone.append(project)
        return sorted(set(gone) | set(await self.prune()))

    async def prune(self) -> list[str]:
        """죽은 크롬의 장부·세션 흔적만 지운다 — 살아 있는 것은 건드리지 않는다.
        크롬이 밖에서 죽었을 때 '열린 브라우저' 목록이 유령으로 남지 않게 하는 청소부다."""
        reg = load_registry()
        dead = [p for p, e in reg.items() if not isinstance(e, dict) or not _alive(e.get("pid"))]
        for p in dead:
            s = self.sessions.pop(p, None)
            if s:
                await s.close()
            reg.pop(p, None)
        if dead:
            save_registry(reg)
        return sorted(dead)

    async def stop(self, project: str) -> bool:
        s = self.sessions.pop(project, None)
        if s:
            await s.close()
        return await asyncio.to_thread(self._stop, project)

    async def close_all(self) -> None:
        for s in list(self.sessions.values()):
            await s.close()
        self.sessions.clear()
