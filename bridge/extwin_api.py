"""외부 클로드 창(extwin.py)의 HTTP·WebSocket 얼굴 — /api/ext, /ws/ext/<pid>. 인증은 기존 미들웨어가 그대로 씌운다."""
from __future__ import annotations

import asyncio
import json

from aiohttp import WSMsgType, web

from bridge import extwin

WS_TOO_SLOW = 1013


async def api_ext(request: web.Request) -> web.Response:
    windows = await asyncio.to_thread(extwin.CACHE.get)
    return web.json_response({"windows": windows, "summary": extwin.summary(windows)})


async def ws_ext(request: web.Request) -> web.StreamResponse:
    try:
        pid = int(request.match_info["pid"])
    except ValueError:
        raise web.HTTPNotFound(text="unknown window")
    win = await asyncio.to_thread(extwin.REGISTRY.get_or_create, pid)
    if win is None:
        raise web.HTTPNotFound(text="unknown window")        # 목록에 없는 pid 에는 붙지 않는다
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    loop = asyncio.get_running_loop()
    sub = extwin.ExtSub(loop)
    win.attach(sub)

    async def pump_out() -> None:
        while (item := await sub.queue.get()) is not None:
            kind, payload = item
            if kind == "b":
                if payload:
                    await ws.send_bytes(payload)
            else:
                await ws.send_str(json.dumps({"type": "ext", "input": sub.input_on, **payload}, ensure_ascii=False))
        await ws.close(code=1000)

    out_task = asyncio.create_task(pump_out())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                if sub.input_on:
                    await asyncio.to_thread(win.write, msg.data.decode("utf-8", "replace"))
            elif msg.type == WSMsgType.TEXT:
                try:
                    ctrl = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ctrl, dict):
                    continue
                if ctrl.get("type") == "ping":
                    sub.push(("b", b""))                    # 답은 출력 큐를 타야 동시 send 가 없다
                elif ctrl.get("type") == "input":
                    sub.input_on = bool(ctrl.get("on"))
                # resize 는 무시한다 — 남의 창 크기는 우리가 못 바꾼다(웹이 서버가 준 cols×rows 로 맞춘다)
    finally:
        sub.closed = True
        win.detach(sub)
        out_task.cancel()
    return ws


def add_routes(app: web.Application) -> None:
    app.router.add_get("/api/ext", api_ext)
    app.router.add_get("/ws/ext/{pid}", ws_ext)

    async def _stop(_app):
        await asyncio.to_thread(extwin.REGISTRY.close_all)
    app.on_cleanup.append(_stop)
