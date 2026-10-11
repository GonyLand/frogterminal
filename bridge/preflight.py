"""재시작 전 점검 — '지금 내리면 다시 뜨는가'를 내리기 전에 본다.

왜 있나 (2026-08-28 실사고): 다른 프로젝트(DGagent)가 18:32 에 자기 포트에 Tailscale
Funnel 을 켰다. 브릿지의 Funnel 게이트는 포트를 가리지 않고 '켜져 있으면 거부'였고, 그 검사는
**기동할 때만** 한다 — 그래서 이미 돌던 브릿지는 멀쩡했고, 두 시간 뒤 재시작을 누르는 순간에야
드러나 대표 터미널이 3분 끊겼다. 기동 전제는 이렇게 **내리기 전에** 확인한다.

같은 이유로 import 도 본다. 이 저장소는 세션 열댓 개가 동시에 고치는 곳이라, 남의 편집이
반쯤 저장된 순간에 재시작하면 브릿지가 영영 안 뜬다(launchd 는 30초마다 실패를 반복할 뿐이다).

    .venv/bin/python -m bridge.preflight     # 종료코드 0 = 지금 재시작해도 된다
"""
import importlib
import sys

from bridge import config, security


def import_problem() -> str | None:
    """서버 모듈이 지금 그대로 읽히는가 — 남의 미완성 편집·문법오류를 여기서 잡는다."""
    try:
        importlib.import_module("bridge.server")
    except Exception as e:                       # ImportError·SyntaxError 뿐 아니라 무엇이든
        return f"bridge.server 를 못 읽는다 — {type(e).__name__}: {e}"
    return None


def funnel_problem() -> str | None:
    """기동 게이트(security.assert_safe_to_start)와 같은 판정을, 죽이기 전에 미리 본다."""
    if not security.tailscale_installed():
        if security.tailscale_required():
            return (f"{config.TAILSCALE_BIN} 이 없는데 bridge.json 의 allowedLogins 가 Tailscale 신원을 요구한다. "
                    "이 상태로 재시작하면 브릿지가 기동을 거부한다.")
        return None                                   # 토큰만 쓰는 설치 — 게이트 생략(기동 쪽과 같은 규칙)
    state = security.funnel_active()
    if state is False:
        return None
    if state is None:
        return (f"Funnel 판정불가 — '{config.TAILSCALE_BIN} serve status --json' 이 안 읽힌다. "
                "이 상태로 재시작하면 브릿지가 기동을 거부한다(fail-closed).")
    return (f"{config.PORT} 포트가 Funnel 로 공개 인터넷에 나가 있다. "
            f"'{config.TAILSCALE_BIN} funnel --https=443 off' 로 끄고 재시작한다.")


def problems() -> list[str]:
    """빈 목록 = 지금 내렸다 올려도 다시 뜬다. 한 줄이라도 있으면 내리지 마라."""
    found = []
    # 목록을 여기서 짓는다 — 모듈 상수로 굳히면 점검 함수를 갈아끼운 테스트가 옛 함수를 부른다.
    for name, check in (("모듈", import_problem), ("Funnel", funnel_problem)):
        try:
            hit = check()
        except Exception as e:                   # 점검이 터졌다고 재시작을 막진 않되, 알린다
            hit = f"점검 자체가 실패했다 — {type(e).__name__}: {e}"
        if hit:
            found.append(f"[{name}] {hit}")
    return found


def main() -> int:
    found = problems()
    for line in found:
        print(f"✗ {line}")
    if found:
        print("→ 재시작하지 마라. 지금 내리면 브릿지가 다시 안 뜬다.")
        return 1
    print(f"✓ 재시작해도 된다 (모듈 정상 · {config.PORT} 포트 Funnel 노출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
