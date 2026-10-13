"""PC 클로드 창 상태 전이를 지켜보다가 웹푸시를 낸다.

  * Tracker.step(windows) — 순수 판정. 창 목록(extwin.list_windows 의 dict)을 받아 '지금 알릴 것' 목록을 돌려준다.
  * run(app) — 브릿지에 붙는 감시 루프. 구독이 하나도 없으면 목록을 읽지도 않는다(폰이 없으면 PC 가 일하지 않는다).

알리는 때: 작업중→결재 대기 · 작업중/결재→완료 · 쉬던 창이 결재를 만났을 때 · 일하던 창이 전부 쉬게 됐을 때.
새로 보이는 창은 조용히 현재 상태만 외운다(브릿지 재시작 때 한꺼번에 울리지 않게). 상태가 STABLE_POLLS 번 연속 같아야 바뀐 것으로
센다 — 훅이 못 알린 사이 대화가 이어졌다고 보는 판정(_behind)이 한 번 출렁이는 것으로는 울리지 않는다.
"""
from __future__ import annotations

import asyncio
import logging

from bridge import extwin, webpush

log = logging.getLogger("frogterminal.notifier")

POLL_SECONDS = 4.0
IDLE_POLL_SECONDS = 15.0        # 구독이 없을 때 구독이 생겼는지 보는 주기(목록은 읽지 않는다)
STABLE_POLLS = 2


def _one_line(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


class Tracker:
    def __init__(self, stable: int = STABLE_POLLS) -> None:
        self.stable = stable
        self.state: dict[int, str] = {}                 # pid → 확정된 상태
        self.pending: dict[int, tuple[str, int]] = {}   # pid → (바뀌려는 상태, 연속 횟수)
        self.busy = False                               # 마지막 '전부 끝남' 이후 일한 창이 있었나

    def step(self, windows: list[dict]) -> list[dict]:
        alerts: list[dict] = []
        finished: list[dict] = []
        live = {w["pid"]: w for w in windows}
        for pid in [p for p in self.state if p not in live]:
            self.state.pop(pid, None)                   # 끝난 창
            self.pending.pop(pid, None)
        for pid, w in live.items():
            cand = w["state"]
            old = self.state.get(pid)
            if old is None:
                self.state[pid] = cand                  # 처음 보는 창 — 조용히 외운다
                self.busy = self.busy or cand == "working"
                continue
            if cand == old:
                self.pending.pop(pid, None)
                continue
            want, n = self.pending.get(pid, (cand, 0))
            n = n + 1 if want == cand else 1
            if n < self.stable:
                self.pending[pid] = (cand, n)
                continue
            self.pending.pop(pid, None)
            self.state[pid] = cand
            if cand == "working":
                self.busy = True
            elif cand == "waiting":
                alerts.append(self._waiting(w))
            elif cand == "done" and old in ("working", "waiting"):
                finished.append(w)
        states = [self.state[p] for p in live]
        all_quiet = bool(states) and all(s == "done" for s in states)
        if self.busy and all_quiet:                     # 전부 쉬는 중이면 결재 알림은 있을 수 없다 — 개별 완료는 이 한 통으로 합친다
            self.busy = False
            names = ", ".join(w["project"] for w in finished) if finished else ""
            alerts.append({"kind": "all_done", "tag": "ext-all-done", "urgency": "high",
                           "title": f"전부 끝났다 — 창 {len(states)}개",
                           "body": (names + " 완료 · " if names else "") + "다음 지시를 내려 줘"})
        elif finished:
            alerts.extend(self._done(w) for w in finished)
        return alerts

    @staticmethod
    def _waiting(w: dict) -> dict:
        return {"kind": "waiting", "tag": f"ext-wait-{w['pid']}", "urgency": "high",
                "title": f"결재 대기 — {w['project']}", "body": _one_line(w.get("message") or w.get("asked") or "승인을 기다린다", 120)}

    @staticmethod
    def _done(w: dict) -> dict:
        return {"kind": "done", "tag": f"ext-done-{w['pid']}", "urgency": "normal",
                "title": f"완료 — {w['project']}", "body": _one_line(w.get("said") or w.get("asked") or "한 턴이 끝났다", 120)}


async def run(app) -> None:
    tracker = Tracker()
    while True:
        try:
            if not await asyncio.to_thread(webpush.has_subs):
                tracker = Tracker()                     # 구독이 없던 동안의 상태는 낡는다 — 생기면 새로 외운다
                await asyncio.sleep(IDLE_POLL_SECONDS)
                continue
            windows = await asyncio.to_thread(extwin.CACHE.get)
            for alert in tracker.step(windows):
                payload = {k: alert[k] for k in ("title", "body", "tag", "kind")} | {"url": "/"}
                res = await asyncio.to_thread(webpush.send_all, payload, urgency=alert["urgency"])
                log.info("push %s sent=%s removed=%s failed=%s", alert["kind"], res["sent"], res["removed"], res["failed"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("notifier loop")
        await asyncio.sleep(POLL_SECONDS)


def setup(app) -> None:
    async def _start(_app):
        _app["notifier"] = asyncio.create_task(run(_app))

    async def _stop(_app):
        task = _app.get("notifier")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)
