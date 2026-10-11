#!/usr/bin/env python3
"""Claude Code 훅 수신기 — 세션 상태 카드 1파일을 쓰거나 지운다.

계약: 어떤 입력·오류에도 조용히 exit 0 (본 세션 작업에 절대 영향 금지), stdout 출력 없음.
브릿지와 프로세스가 분리돼 있어 bridge 모듈을 import 하지 않는다(표준 라이브러리만).
"""
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), "projects")
APP_SETTINGS = os.path.join(os.path.expanduser("~"), ".frogterminal", "settings.json")


def configured_root(settings_file=APP_SETTINGS, home=None):
    """브릿지와 분리된 훅도 현재 단일 루트를 찾는다. 손으로 고친 위험 값은 기본값으로 닫는다.

    훅은 이벤트마다 새 프로세스로 실행되므로 설정을 바꾼 뒤 이미 살아 있던 Claude 세션의 다음
    이벤트도 새 루트에 카드를 쓴다. bridge를 import하지 않는 계약 때문에 같은 최소 검증을 둔다.
    """
    home = os.path.realpath(home or os.path.expanduser("~"))
    fallback = os.path.join(home, "projects")      # bridge.config.DEFAULT_PROJECTS_ROOT 의 기본과 같다
    # bridge.json 의 defaultProjectsRoot(셸에서 사람이 정한 값)가 있으면 그것이 기본 루트다 — 윈도우의 Y:\ 처럼 홈 밖일 수 있다.
    default_root = None
    try:
        with open(os.path.join(home, ".frogterminal", "bridge.json"), encoding="utf-8") as f:
            cfg_root = json.load(f).get("defaultProjectsRoot")
        if isinstance(cfg_root, str) and cfg_root.strip():
            default_root = os.path.realpath(os.path.expanduser(cfg_root))
            fallback = default_root
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(settings_file, encoding="utf-8") as f:
            raw = json.load(f).get("projectsRoot")
        if not isinstance(raw, str) or not raw.strip():
            return fallback
        if "\x00" in raw or "\n" in raw or "\r" in raw or ".." in raw.split(os.sep):
            return fallback
        if not os.path.isabs(raw) or not os.path.isdir(raw):
            return fallback
        resolved = os.path.realpath(raw)
        if default_root and (resolved == default_root or os.path.commonpath((default_root, resolved)) == default_root):
            return resolved
        if resolved == home or os.path.commonpath((home, resolved)) != home:
            return fallback
        return resolved
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        return fallback


ROOT = os.environ.get("FROG_ROOT") or configured_root()
STATE_DIR = os.environ.get("FROG_STATE_DIR") or os.path.join(ROOT, "_frogterminal", "decisions")
SID_RE = re.compile(r"^[A-Za-z0-9._-]+$")     # 경로조작 차단 — 벗어나면 그냥 버린다


def project_of(cwd: str):
    try:
        rel = os.path.relpath(cwd or "/", ROOT)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    head = rel.split(os.sep)[0]
    return head if head not in (".", "") else None


# ── 이 세션의 클로드 프로세스 찾기 ───────────────────────────────────────────
# 브릿지는 "이 카드가 아직 살아있나"를 알아야 한다. 예전엔 같은 이름의 tmux 세션이 있는지로
# 판정했는데, 클로드는 대부분 tmux 밖(Zed·iTerm)에서 돌기 때문에 실측이 정반대로 나왔다:
# 지금 일하는 세션이 '꺼짐'으로, [터미널 열기]가 만든 빈 셸이 '살아있음'으로 잡혔다.
# 훅은 클로드가 부르므로 그 프로세스가 곧 내 조상이다 — 여기서 잡아 카드에 적어 둔다.
# (pid 만으로는 재사용된 번호에 속을 수 있어 시작시각까지 함께 적는다. ps 의 lstart 문자열을
#  해석 없이 그대로 쓴다 — 브릿지도 같은 명령으로 읽어 문자열끼리 견주기만 하면 된다.)

def is_claude(comm: str) -> bool:
    """ps 의 실행파일 경로가 클로드인가. 버전 바이너리(…/claude/versions/2.1.232)도 포함."""
    return "claude" in comm.lower()


def parse_ps(text: str) -> dict:
    """'pid ppid <lstart 5토막> comm' → {pid: (ppid, lstart, comm)}."""
    table = {}
    for line in text.splitlines():
        parts = line.split(None, 7)                # lstart = 'Fri Aug 14 23:02:42 2026' (5토막)
        if len(parts) < 8:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        table[pid] = (ppid, " ".join(parts[2:7]), parts[7])
    return table


def owner_process(table: dict, start_pid: int) -> tuple:
    """조상을 거슬러 올라가 처음 만나는 클로드 프로세스의 (pid, 시작시각). 못 찾으면 (None, None).

    훅은 보통 클로드 → sh → python 이라 두세 칸 위에 있다. 셸 재실행 등으로 더 깊어질 수 있어
    여유를 두되, 순환(있을 수 없지만)에 갇히지 않게 횟수로 끊는다.
    """
    pid = start_pid
    for _ in range(10):
        row = table.get(pid)
        if not row:
            return None, None
        ppid, start, comm = row
        if is_claude(comm):
            return pid, start
        if ppid <= 1:
            return None, None
        pid = ppid
    return None, None


def owner_of_this_hook_win() -> tuple:
    """윈도우: psutil 로 조상을 거슬러 올라가 클로드를 찾는다. 시작시각은 브릿지(winmux.format_start)와 같은
    '%Y-%m-%dT%H:%M:%S'(로컬) — 로케일을 타지 않는다. psutil 이 없거나 실패하면 (None, None)."""
    try:
        import psutil
        import time
        proc = psutil.Process(os.getpid())
        for _ in range(10):
            if is_claude(proc.name()):
                return proc.pid, time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(proc.create_time()))
            parent = proc.parent()
            if parent is None:
                return None, None
            proc = parent
    except Exception:
        pass
    return None, None


def owner_of_this_hook() -> tuple:
    """실패는 전부 (None, None) — 카드에 pid 가 없으면 브릿지가 예전 기준으로 판정한다.

    LC_ALL=C 고정 필수 — 훅은 클로드의 환경을 물려받아, 한글 로케일 세션에선 lstart 가
    "2026년 8월 15일 토요일 10시 11분 24초"(7토막)로 나와 5토막 파싱이 잘리고 영어 환경의
    브릿지와 문자열이 절대 안 맞아, 살아있는 세션이 '꺼짐'으로 판정된 실사고(2026-08-15)가 있다.
    """
    if sys.platform == "win32":
        return owner_of_this_hook_win()
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,lstart=,comm="],
                             capture_output=True, text=True, timeout=5,
                             env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None, None
    if out.returncode != 0:
        return None, None
    return owner_process(parse_ps(out.stdout), os.getpid())


# ── 알림을 카드 종류로 가르기 ────────────────────────────────────────────────
# 클로드 코드는 Notification 훅 payload 에 notification_type 을 함께 준다(실측 2.1.252 —
# `hook_event_name:"Notification",message:d,title:_,notification_type:C`).
# 예전엔 문구만 보고 "waiting 이 없으면 전부 결재"로 갈랐는데, 클로드 코드가 백그라운드 작업
# 알림을 내면서 `"<작업 이름> failed"`(agent_completed)까지 '결재 필요'로 올라갔다. 아무도
# 기다리지 않는 빨간 결재가 며칠씩 남았다(2026-09-01 실사고: "채널소개 영상 PiP
# 파이프라인 렌더링 및 검증 failed" 가 결재 1건으로 붙박이). 진짜 결재는 '사람이 답해야
# 클로드가 한 발짝이라도 나가는 것'뿐이다.
DECISION_NOTIFICATIONS = {"permission_prompt", "worker_permission_prompt", "agent_needs_input"}
IDLE_NOTIFICATIONS = {"idle_prompt"}


def notification_kind(ntype: str, message: str) -> str:
    """알림 하나 → 카드 종류(permission/idle). 결재로 올릴 것만 좁게 고른다."""
    if ntype in DECISION_NOTIFICATIONS:
        return "permission"
    if ntype in IDLE_NOTIFICATIONS:
        return "idle"
    # 모르는 종류(종류가 아예 없던 옛 버전 포함)만 문구로 한 번 더 본다 — 다음 버전이 새로운
    # 결재성 알림을 더할 수 있으므로, 클로드가 실제로 쓰는 두 문구는 여기서도 건진다.
    low = message.lower()
    if "needs your permission" in low or "needs your input" in low:
        return "permission"
    return "idle"                              # 작업 완료·실패·인증 등은 알림일 뿐 결재가 아니다


def prior_card(path: str) -> dict:
    """지금 놓여 있는 카드 — SessionStart 가 '방금 압축이 자동이었나'를 알아보는 유일한 단서."""
    try:
        with open(path, encoding="utf-8") as f:
            card = json.load(f)
        return card if isinstance(card, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> None:
    # 클로드는 UTF-8 JSON 을 stdin 으로 준다 — 윈도우의 기본 인코딩(cp949)으로 읽으면 한글이 깨진다.
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    event = data.get("hook_event_name", "")
    sid = str(data.get("session_id") or "")
    if not SID_RE.match(sid):
        return
    path = os.path.join(STATE_DIR, sid + ".json")
    extra = {}
    if event == "SessionEnd":
        try:
            os.remove(path)
        except OSError:
            pass
        return
    if event == "Notification":
        msg = str(data.get("message", ""))
        kind = notification_kind(str(data.get("notification_type") or ""), msg)
    elif event == "Stop":
        kind, msg = "done", ""
    elif event == "UserPromptSubmit":
        kind, msg = "working", ""
    elif event == "PreCompact":
        # 대화 압축도 일이다(실측 2m19s) — 그동안은 '진행중'이 맞다. 압축엔 Stop 이 안 울리므로
        # 끝났다는 말은 SessionStart(source=compact)가 대신 해준다. 자동 압축은 그 뒤로 하던 일이
        # 이어지니 손대지 않게, 어느 쪽이었는지를 카드에 적어 둔다.
        kind, msg = "working", ""
        extra = {"compactTrigger": str(data.get("trigger") or "")}
    elif event == "SessionStart":
        # 세션이 프롬프트 앞에 섰다 = 다음 지시 대기. 단 자동 압축은 턴 한가운데서 일어나 압축이
        # 끝나면 하던 일을 계속하므로, 그때는 '진행중' 카드를 그대로 둔다(끝은 Stop 이 알린다).
        if (str(data.get("source") or "") == "compact"
                and prior_card(path).get("compactTrigger") != "manual"):
            return
        kind, msg = "done", ""
    else:
        return
    card = {
        "sessionId": sid, "kind": kind, "message": msg, **extra,
        "cwd": data.get("cwd", ""), "project": project_of(data.get("cwd", "")),
        # 세션 대화 JSONL 경로 — 브릿지가 카드에 "무엇을 시켰고 뭐라 했는지"를 얹는 재료.
        # cwd+session_id 로 계산해 낼 수도 있지만 훅이 준 실제 경로가 더 정확하다(실측: 온다).
        "transcriptPath": str(data.get("transcript_path") or ""),
        "machine": socket.gethostname(),
        # 이 클로드가 tmux 안에서 도는가 — TMUX 환경변수는 tmux 안에서 시작된 프로세스에만 있고,
        # 훅은 클로드의 자식이라 그대로 물려받는다. 프로그터미널의 세션 = tmux 세션이므로,
        # 브릿지는 이 값으로 "프로그터미널이 연 세션의 결재만" 올린다(Zed·iTerm 세션 제외).
        # 윈도우판 세션은 tmux 가 아니라 브릿지의 ConPTY 라서 FROGTERMINAL_SESSION(winmux 가 자식에 심는다)으로 본다.
        "inTmux": bool(os.environ.get("TMUX") or os.environ.get("FROGTERMINAL_SESSION")),
        "updatedAt": datetime.now(KST).isoformat(),
    }
    pid, started = owner_of_this_hook()          # 이 카드의 주인 = 살아있음 판정의 근거
    if pid:
        card["pid"], card["pidStart"] = pid, started
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:     # 브릿지는 UTF-8 로 읽는다 — 윈도우 기본(cp949)으로 쓰면 한글이 깨진다
        json.dump(card, f, ensure_ascii=False)
    os.replace(tmp, path)                     # 원자적 — 브릿지가 반쪽 JSON 을 읽지 않게


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
