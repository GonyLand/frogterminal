"""브라우저 패널 API — /api/preview/* . 본체는 preview.py, 여기는 HTTP 껍데기와 감사로그뿐.

server.py 에서 분리한 이유: 그 파일이 이미 780줄이라(전역 줄수 표준 800 상한) 새 책임은 밖에 둔다.
인증·Origin 검사는 server.auth_middleware 가 앱 전체에 걸어 주므로 여기서 다시 하지 않는다.
"""
from __future__ import annotations

import asyncio

from aiohttp import web

from bridge import preview, sessions


def _audit(request, line: str) -> None:
    from bridge import server                  # 순환 import 회피 — 감사로그는 server 가 소유
    server._audit(f"preview user={request.headers.get('Tailscale-User-Login')} {line}")


async def api_services(request):
    """지금 127.0.0.1 에서 LISTEN 중인 서비스 목록. ?project= 를 주면 그 프로젝트 것을 앞에 세운다.
    조회 김에 만료된 미리보기를 거둔다(웹이 갱신을 멈춘 것 = 패널이 죽은 것)."""
    project = request.query.get("project", "")
    reaped = await asyncio.to_thread(preview.reap)
    if reaped:
        _audit(request, f"reap ports={reaped}")
    services = await asyncio.to_thread(preview.list_services)
    if project:
        services.sort(key=lambda s: (s["project"] != project, s["project"] is None, s["port"]))
    active = await asyncio.to_thread(preview.active)
    return web.json_response({"services": services,
                              "active": {p: {"dest": e["dest"], "project": e["project"]}
                                         for p, e in active.items()}})


async def api_open(request):
    try:
        body = await request.json()
        service_id, project = str(body["id"]), str(body.get("project", ""))
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "bad body"}, status=400)
    if not sessions.is_valid_name(project):
        return web.json_response({"error": "bad project"}, status=400)
    owner = request.headers.get("Tailscale-User-Login", "")
    try:
        got = await asyncio.to_thread(preview.open_preview, service_id, project, owner)
    except preview.PreviewError as e:
        _audit(request, f"open-denied project={project} id={service_id} reason={e}")
        return web.json_response({"error": str(e)}, status=409)
    _audit(request, f"open project={project} id={service_id} ext={got['port']} dest={got['dest']}")
    return web.json_response(got)


def _port_of(body) -> int | None:
    try:
        port = int(body["port"])
    except (KeyError, TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


async def api_renew(request):
    try:
        port = _port_of(await request.json())
    except ValueError:
        port = None
    if port is None:
        return web.json_response({"error": "bad body"}, status=400)
    ok = await asyncio.to_thread(preview.renew_preview, port)
    return web.json_response({"renewed": ok}, status=200 if ok else 404)


async def api_close(request):
    try:
        port = _port_of(await request.json())
    except ValueError:
        port = None
    if port is None:
        return web.json_response({"error": "bad body"}, status=400)
    ok = await asyncio.to_thread(preview.close_preview, port)
    if ok:
        _audit(request, f"close ext={port}")
    return web.json_response({"closed": ok})


def add_routes(app: web.Application) -> None:
    app.router.add_get("/api/preview/services", api_services)
    app.router.add_post("/api/preview/open", api_open)
    app.router.add_post("/api/preview/renew", api_renew)
    app.router.add_post("/api/preview/close", api_close)
