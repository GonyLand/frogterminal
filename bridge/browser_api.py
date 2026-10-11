"""브라우저 패널 2단계 API — /api/browser/{project}/* 와 /ws/browser/{project}. 본체는 browser.py.

웹(뷰어)과의 약속:
  웹 → 브릿지 (TEXT JSON): navigate{url} · back · forward · reload{hard} · mouse{kind,x,y,button,clicks,modifiers}
                          · wheel{x,y,dx,dy} · key{kind,key,code,text,vk,modifiers} · text{text}
                          · viewport{w,h} · tabs · tab{id} · newtab{url} · closetab{id} · state
  브릿지 → 웹: TEXT {"type":"frame","seq","w","h"} 바로 뒤에 BINARY(JPEG) · TEXT {"type":"state",…}
              · TEXT {"type":"error","message"}
  프레임은 뷰어마다 '최신 것 하나'만 — 보내는 동안 새로 온 프레임은 다음 차례에 최신 것으로 대체된다.
에이전트용: GET /api/browser/{project} → cdp(브라우저 엔드포인트) — Playwright connectOverCDP 로 같은 크롬에 붙는다.
            POST /api/browser/{project}/navigate {url} — 클로드가 "네이버 열어줘"를 받으면 이걸 부른다.
"""
from __future__ import annotations

import asyncio
import json

from aiohttp import WSMsgType, web

from bridge import browser, sessions

BROWSERS = web.AppKey("browsers", browser.Browsers)


def _audit(request, line: str) -> None:
    from bridge import server
    server._audit(f"browser user={request.headers.get('Tailscale-User-Login')} {line}")


def _project(request) -> str | None:
    name = request.match_info["project"]
    return name if sessions.is_valid_name(name) else None


async def api_status(request):
    """조회만 한다 — 여기서 크롬을 띄우거나 붙지 않는다(장부만 읽는다)."""
    project = _project(request)
    if not project:
        return web.json_response({"error": "bad project"}, status=400)
    running = request.app[BROWSERS].running()
    reg = running.get(project)
    if not reg:
        return web.json_response({"running": False, "browsers": sorted(running)})
    s = request.app[BROWSERS].sessions.get(project)
    port = reg["port"]
    return web.json_response({"running": True, "port": port, "cdp": f"http://127.0.0.1:{port}",
                              "active": s.target_id if s else None,
                              "browsers": sorted(request.app[BROWSERS].running())})   # 지금 살아 있는 프로젝트 크롬들


async def api_start(request):
    project = _project(request)
    if not project:
        return web.json_response({"error": "bad project"}, status=400)
    try:
        s = await request.app[BROWSERS].get(project)
    except browser.BrowserError as e:
        _audit(request, f"start-failed project={project} reason={e}")
        return web.json_response({"error": str(e)}, status=409)
    _audit(request, f"start project={project} port={s.port}")
    return web.json_response({"running": True, "port": s.port, "cdp": f"http://127.0.0.1:{s.port}",
                              "active": s.target_id})


async def api_stop(request):
    project = _project(request)
    if not project:
        return web.json_response({"error": "bad project"}, status=400)
    ok = await request.app[BROWSERS].stop(project)
    _audit(request, f"stop project={project} stopped={ok}")
    return web.json_response({"stopped": ok})


async def api_navigate(request):
    """에이전트·스크립트용 — 같은 인증 관문을 지나므로 curl 한 줄로 그 프로젝트 브라우저를 움직인다."""
    project = _project(request)
    if not project:
        return web.json_response({"error": "bad project"}, status=400)
    try:
        url = str((await request.json())["url"])
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "bad body"}, status=400)
    if not url.startswith(("http://", "https://", "about:")):
        return web.json_response({"error": "http(s) 주소만"}, status=400)
    try:
        s = await request.app[BROWSERS].get(project)
        await s.navigate(url)
    except browser.BrowserError as e:
        return web.json_response({"error": str(e)}, status=409)
    _audit(request, f"navigate project={project} url={url[:200]}")
    return web.json_response({"ok": True})


async def api_screenshot(request):
    """에이전트가 '지금 화면'을 볼 때 — 최신 프레임(JPEG). 뷰어가 없어 캐스트가 꺼져 있으면 한 장 찍는다."""
    project = _project(request)
    if not project:
        return web.json_response({"error": "bad project"}, status=400)
    s = await request.app[BROWSERS].get(project, start=False)
    if not s:
        return web.json_response({"error": "not running"}, status=404)
    if s.frame is None or not s.casting:
        try:
            shot = await s.call("Page.captureScreenshot", {"format": "jpeg", "quality": browser.JPEG_QUALITY})
        except browser.BrowserError as e:
            return web.json_response({"error": str(e)}, status=409)
        import base64
        return web.Response(body=base64.b64decode(shot["data"]), content_type="image/jpeg")
    return web.Response(body=s.frame, content_type="image/jpeg")


async def ws_viewer(request):
    project = _project(request)
    if not project:
        return web.json_response({"error": "bad project"}, status=400)
    # ?start=1 이어야 새로 띄운다 — 패널이 저절로 붙을 때는 0 이라 '안 연 프로젝트'가 메모리를 먹지 않는다.
    start = request.query.get("start", "0") == "1"
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024 * 1024)
    await ws.prepare(request)
    try:
        s = await request.app[BROWSERS].get(project, start=start)
    except browser.BrowserError as e:
        await ws.send_json({"type": "error", "message": str(e)})
        await ws.close()
        return ws
    if s is None:                               # start=0 으로 붙었는데 그 프로젝트 크롬이 없다 — 띄우지 않는다
        await ws.send_json({"type": "closed", "message": f"'{project}' 브라우저가 꺼져 있습니다"})
        await ws.close()
        return ws
    _audit(request, f"viewer-attach project={project}")
    s.viewers += 1
    s.touch()
    try:
        await s.start_cast()
    except browser.BrowserError as e:
        await ws.send_json({"type": "error", "message": str(e)})
    sent_seq = 0

    async def push_state():
        try:
            await ws.send_json(await s.state())
        except Exception:
            pass

    async def pump():
        nonlocal sent_seq
        while not ws.closed:
            if s.frame_seq == sent_seq and not s.state_event.is_set():
                waits = [asyncio.ensure_future(s.frame_event.wait()), asyncio.ensure_future(s.state_event.wait())]
                done, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED, timeout=2)
                for w in pending:
                    w.cancel()
                if not done:                    # 2초 조용하면 state 만 새로 — 에이전트가 밖에서 연 탭·이동이 탭 바에 보인다
                    s.state_event.set()
            if s.state_event.is_set():
                s.state_event.clear()
                await push_state()
            if s.frame_seq != sent_seq and s.frame is not None:
                sent_seq = s.frame_seq
                s.frame_event.clear()
                meta = s.frame_meta
                await ws.send_json({"type": "frame", "seq": sent_seq,
                                    "w": meta.get("deviceWidth"), "h": meta.get("deviceHeight")})
                await ws.send_bytes(s.frame)
            elif s.frame_seq == sent_seq:
                s.frame_event.clear()

    pump_task = asyncio.create_task(pump())
    await push_state()
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                cmd = json.loads(msg.data)
                s.touch()
                await handle(s, cmd)
            except browser.BrowserError as e:
                await ws.send_json({"type": "error", "message": str(e)})
            except (ValueError, KeyError, TypeError) as e:
                await ws.send_json({"type": "error", "message": f"bad command: {e}"})
            if cmd.get("type") in ("navigate", "back", "forward", "reload", "tab", "newtab", "closetab", "state"):
                s.state_event.set()
    finally:
        pump_task.cancel()
        s.viewers = max(0, s.viewers - 1)
        s.touch()                               # 지금부터 IDLE_STOP_S 를 센다
        if s.viewers == 0:
            try:
                await s.stop_cast()             # 보는 사람이 없으면 캐스트를 끈다(CPU) — 크롬은 살려 둔다
            except Exception:
                pass
        _audit(request, f"viewer-detach project={project}")
    return ws


async def handle(s: browser.PageSession, cmd: dict) -> None:
    t = cmd.get("type")
    if t == "navigate":
        url = str(cmd["url"])
        if not url.startswith(("http://", "https://", "about:")):
            raise browser.BrowserError("http(s) 주소만")
        await s.navigate(url)
    elif t == "back":
        await s.go(-1)
    elif t == "forward":
        await s.go(+1)
    elif t == "reload":
        await s.reload(bool(cmd.get("hard")))
    elif t == "mouse":
        await s.mouse(cmd["kind"], cmd["x"], cmd["y"], cmd.get("button", "left"),
                      cmd.get("clicks", 1), cmd.get("modifiers", 0))
    elif t == "wheel":
        await s.wheel(cmd["x"], cmd["y"], cmd.get("dx", 0), cmd.get("dy", 0))
    elif t == "key":
        await s.key(cmd["kind"], cmd["key"], cmd.get("code", ""), cmd.get("text", ""),
                    cmd.get("vk", 0), cmd.get("modifiers", 0))
    elif t == "text":
        await s.insert_text(str(cmd["text"])[:4000])
    elif t == "viewport":
        await s.set_viewport(cmd["w"], cmd["h"], cmd.get("dpr"))
    elif t == "tab":
        await s.select_tab(str(cmd["id"]))
    elif t == "newtab":
        url = str(cmd.get("url") or "about:blank")
        if not url.startswith(("http://", "https://", "about:")):
            raise browser.BrowserError("http(s) 주소만")
        await s.new_tab(url)
    elif t == "closetab":
        await s.close_tab(str(cmd["id"]))
    elif t in ("tabs", "state"):
        pass
    else:
        raise browser.BrowserError(f"모르는 명령: {t}")


def add_routes(app: web.Application) -> None:
    app[BROWSERS] = browser.Browsers()
    app.router.add_get("/api/browser/{project}", api_status)
    app.router.add_post("/api/browser/{project}/start", api_start)
    app.router.add_post("/api/browser/{project}/stop", api_stop)
    app.router.add_post("/api/browser/{project}/navigate", api_navigate)
    app.router.add_get("/api/browser/{project}/screenshot", api_screenshot)
    app.router.add_get("/ws/browser/{project}", ws_viewer)

    async def _reaper(app):
        while True:                              # 60초마다 — 유휴 10분 넘은 크롬 종료 + 죽은 장부 청소
            await asyncio.sleep(60)
            try:
                gone = await app[BROWSERS].reap()
                if gone:
                    from bridge import server
                    server._audit(f"browser reap={gone} (유휴 {browser.IDLE_STOP_S}초 초과 또는 이미 죽음)")
            except Exception:
                pass

    async def _startup(app):
        app["browser_reaper"] = asyncio.create_task(_reaper(app))

    async def _cleanup(app):
        app["browser_reaper"].cancel()
        await app[BROWSERS].close_all()        # 크롬은 살려 둔다(장부로 다시 붙는다) — 소켓만 정리
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
