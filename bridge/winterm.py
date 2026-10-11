"""윈도우판 터미널 웹소켓 — 맥판 server.ws_terminal 의 pty+tmux attach 자리를 winmux 세션 구독으로 바꾼 것.

웹이 보내는 약속(바이너리 = 키입력, 텍스트 JSON = ping/resize/compact/unmode)과 받는 약속(바이너리 = 터미널 글자,
빈 바이트 = ping 답)은 맥판과 같다. 다른 점:
  * 붙을 때 pyte 화면을 다시 그려 준다(새 탭이면 스크롤백까지, 같은 탭의 재접속이면 보이는 화면만).
  * 마우스 휠·스와이프의 스크롤은 tmux 복사 모드가 아니라 브라우저 xterm 의 스크롤백이 맡는다(unmode 는 무시).
"""
import asyncio
import json

from aiohttp import WSMsgType, web

from bridge import sessions, winmux

WS_TOO_SLOW = 1013          # 따라오지 못해 끊는다 — 웹은 저절로 다시 붙어 화면을 새로 받는다


def _clamp(value: int, lo: int = 1, hi: int = 500) -> int:
    return max(lo, min(hi, value))


async def serve(request: web.Request, ws: web.WebSocketResponse, name: str, agent: str,
                cols: int, rows: int, reconnect: bool) -> None:
    loop = asyncio.get_running_loop()
    argv, cwd = sessions.win_launch(name, agent=agent)
    session, created = await asyncio.to_thread(winmux.MUX.get_or_create, name, argv, cwd, cols, rows)
    sub = winmux.Subscriber(loop)
    sub.size = (cols, rows)
    if not created:
        await asyncio.to_thread(session.fit, sub)    # PC 창·폰이 같이 붙으면 가장 작은 화면에 맞춘다(스냅샷은 그 크기로 그린다)
    # hist=0 — PC 의 frog 창: 직접 연 것이라 재접속 표시(re=1)는 못 쓰고(끈 세션 되살림 방지에 걸린다), 스크롤백은 터미널 창이 갖고 있어 보이는 화면만 받는다.
    session.attach(sub, with_history=not (reconnect or request.query.get("hist") == "0"))

    async def pump_out() -> None:
        while (data := await sub.queue.get()) is not None:
            sub.consumed(len(data))
            await ws.send_bytes(data)
        await ws.close(code=WS_TOO_SLOW if sub.overflowed else 1000)

    out_task = asyncio.create_task(pump_out())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await asyncio.to_thread(session.write, msg.data)
            elif msg.type == WSMsgType.TEXT:
                try:
                    ctrl = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ctrl, dict):
                    continue
                if ctrl.get("type") == "ping":
                    sub._put(b"")                       # 답은 출력 큐를 타야 동시 send 가 없다
                elif ctrl.get("type") == "resize":
                    try:
                        c, r = _clamp(int(ctrl["cols"])), _clamp(int(ctrl["rows"]))
                    except (KeyError, ValueError, TypeError):
                        continue                         # 망가진 resize 하나로 연결이 끊기면 안 된다
                    await asyncio.to_thread(session.set_size, sub, c, r)
                # compact(여러 칸 HUD 표시)·unmode(복사 모드 해제)는 윈도우판에 대응물이 없다
    finally:
        sub.closed = True
        session.detach(sub)
        out_task.cancel()
