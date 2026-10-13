"""웹푸시 HTTP 얼굴 — /api/push/*. 인증·Origin 검사는 기존 미들웨어가 그대로 씌운다."""
from __future__ import annotations

import asyncio

from aiohttp import web

from bridge import notifier, webpush


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="json")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="json")
    return data


async def api_key(request: web.Request) -> web.Response:
    key = await asyncio.to_thread(webpush.public_key)
    return web.json_response({"key": key, "subs": len(await asyncio.to_thread(webpush.load_subs))})


async def api_subscribe(request: web.Request) -> web.Response:
    data = await _json(request)
    if not await asyncio.to_thread(webpush.add_sub, data.get("subscription")):
        raise web.HTTPBadRequest(text="subscription")
    return web.json_response({"ok": True})


async def api_unsubscribe(request: web.Request) -> web.Response:
    data = await _json(request)
    removed = await asyncio.to_thread(webpush.remove_sub, data.get("endpoint"))
    return web.json_response({"ok": True, "removed": removed})


async def api_test(request: web.Request) -> web.Response:
    res = await asyncio.to_thread(webpush.send_all, {"title": "frogterminal 알림 시험", "body": "이 알림이 보이면 결재·완료 알림이 간다",
                                                     "tag": "push-test", "kind": "test", "url": "/"})
    return web.json_response(res)


def add_routes(app: web.Application) -> None:
    app.router.add_get("/api/push/key", api_key)
    app.router.add_post("/api/push/subscribe", api_subscribe)
    app.router.add_post("/api/push/unsubscribe", api_unsubscribe)
    app.router.add_post("/api/push/test", api_test)
    notifier.setup(app)                              # PC 클로드 창 상태 전이 감시 → 푸시
