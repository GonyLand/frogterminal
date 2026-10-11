"""frogterminal 브릿지 상수와 런타임 경로.

프로젝트 루트는 설정 저장 직후 바뀌어야 하므로 모듈 상수가 아니다. 경로를 쓰는 곳은 아래
getter를 호출해 매번 현재 settings.json 값을 읽는다(import 시점에 굳히지 않는다).

기기·사람에 묶인 값(허용 계정·허용 Origin·Tailscale 경로·열기 범위·HUD 스크립트)은 코드가 아니라
``~/.frogterminal/bridge.json`` 에서 기동 때 한 번 읽는다 — 같은 코드가 어느 맥에서든 그대로 돈다
(공개 배포 설계 2026-10-11). 웹 UI 로는 바꿀 수 없다(인증을 느슨하게 만드는 값이라 셸에서만).
"""
import json
import os
import sys
from datetime import timedelta, timezone
from pathlib import Path

PORT = 7788
BIND_HOST = "127.0.0.1"                      # 절대 0.0.0.0 금지
WEB_DIR = Path(__file__).parent.parent / "web"   # 프론트를 같은 출처에서 서빙 (LNA 차단 회피)
APP_DIR = Path.home() / ".frogterminal"           # 앱 설정·브릿지 설정·토큰이 사는 고정 자리
BRIDGE_CONFIG_FILE = APP_DIR / "bridge.json"
TOKEN_FILE = APP_DIR / "token"                        # 자체 인증 토큰(0600). 없으면 첫 기동 때 만든다
_DEFAULT_TAILSCALE_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"  # PATH에 없음 — 절대경로 필수


def load_bridge_config(path: Path = BRIDGE_CONFIG_FILE) -> dict:
    """bridge.json 을 읽어 검증한다. 없으면 {} — 깨졌거나 타입이 틀리면 기동 거부.

    조용히 기본값으로 물러서면 "Tailscale 검사가 빠진 채" 뜰 수 있다 — 인증 설정은 fail-closed.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        sys.exit(f"[frogterminal] {path} 을 읽을 수 없다 — {e}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        sys.exit(f"[frogterminal] {path} 이 올바른 JSON 이 아니다 — {e}")
    if not isinstance(data, dict):
        sys.exit(f"[frogterminal] {path} 은 객체({{...}})여야 한다")
    for key in ("allowedLogins", "allowedOrigins"):
        v = data.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            sys.exit(f"[frogterminal] {path} 의 {key} 는 문자열 목록이어야 한다")
    for key in ("tailscaleBin", "hudScript", "defaultProjectsRoot"):
        if data.get(key) is not None and not isinstance(data[key], str):
            sys.exit(f"[frogterminal] {path} 의 {key} 는 문자열이어야 한다")
    if not isinstance(data.get("openAnywhere", False), bool):
        sys.exit(f"[frogterminal] {path} 의 openAnywhere 는 true/false 여야 한다")
    unknown = set(data) - {"allowedLogins", "allowedOrigins", "tailscaleBin", "hudScript",
                           "openAnywhere", "defaultProjectsRoot"}
    if unknown:
        sys.exit(f"[frogterminal] {path} 에 모르는 키가 있다 — {sorted(unknown)}")
    return data


_BRIDGE = load_bridge_config()
# Tailscale 신원. 비어 있으면 신원 헤더를 보지 않는다(토큰만) — Tailscale 없이 쓰는 설치용.
ALLOWED_LOGINS = frozenset(_BRIDGE.get("allowedLogins", []))
ALLOWED_ORIGINS = frozenset(_BRIDGE.get("allowedOrigins", []))   # 고정 허용 Origin. Host 와 같은 출처는 늘 허용(security.same_origin)
TAILSCALE_BIN = _BRIDGE.get("tailscaleBin") or _DEFAULT_TAILSCALE_BIN
OPEN_ANYWHERE = bool(_BRIDGE.get("openAnywhere", False))         # /api/open 이 절대경로 어디든 — 켠 사람만
HUD_SCRIPT = Path(os.path.expanduser(_BRIDGE["hudScript"])) if _BRIDGE.get("hudScript") else None
DEFAULT_PROJECTS_ROOT = Path(os.path.expanduser(_BRIDGE.get("defaultProjectsRoot") or "~/projects"))
# 루트를 루트 안 settings.json에만 적으면 재시작 뒤 그 파일을 찾을 수 없는 순환이 생긴다.
# 그래서 앱 설정 하나만 고정 위치에 두고, 작업공간 데이터(숨김·결재·업로드)는 선택한 루트를 따른다.
DEFAULT_SETTINGS_FILE = APP_DIR / "settings.json"
SETTINGS_FILE = DEFAULT_SETTINGS_FILE
LEGACY_SETTINGS_FILE = DEFAULT_PROJECTS_ROOT / "_frogterminal" / "settings.json"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
KST = timezone(timedelta(hours=9))
IGNORED_FOLDERS = {"_uploads",           # 감사로그가 사는 곳 — 파일 API 로도 못 열게(sessions 관문)
                   "_frogterminal"}      # 결재 카드가 사는 곳 — 사이드바 유령 프로젝트이기도 했다

MONITOR_SESSION = "_frogtop"             # CPU 팝업(htop) 전용 tmux 세션 — 사이드바 목록엔 안 나온다

CLAUDE_CONFIG = Path.home() / ".claude.json"     # 클로드 로그인 계정 표시용(읽기 전용)
CODEX_AUTH = Path.home() / ".codex" / "auth.json"   # 코덱스 로그인 여부 판단용(내용은 안 읽는다)
AGENT_STATUS_TTL = 60                        # 에이전트 연결 상태 캐시(초) — --version 호출 절약
                                             # (기기 아닌 서버 보관 — 폰·맥이 같은 목록을 봐야 한다)
AGENT_LATEST_TTL = 6 * 60 * 60               # npm·GitHub 최신판 조회는 6시간 보관 — 설정을 열 때마다
                                             # 두 공개 API를 두드리지 않되 하루 안에는 새 판을 알린다.
AGENT_LATEST_TIMEOUT = 4                      # 연결 불량이 설정 화면 전체를 오래 붙들지 않게 짧게 실패
AGENT_DOCTOR_TIMEOUT = 15                    # claude doctor는 로컬 읽기지만 플러그인 점검 지연을 제한
AGENT_UPDATE_TIMEOUT = 180                    # 설치는 내려받기가 있어 상태 조회보다 충분히 길게 기다린다
DECISION_TTL_HOURS = 72                      # 이보다 낡은 카드는 스캔 때 지운다 (유령 방지)
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024        # 파일 패널 읽기·쓰기 상한
# 휴대폰 사진·긴 스크린샷은 대개 수 MB라 20MB까지 보되, 압축 해제 뒤 메모리가 몇 배로
# 불어나는 이미지를 50MB 업로드 상한 그대로 브라우저에 밀어 넣지는 않는다.
MAX_IMAGE_FILE_BYTES = 20 * 1024 * 1024       # 파일 패널 이미지 미리보기 전용 상한(읽기만)
MAX_ANSWER_CHARS = 4000                      # 결재 텍스트 답 상한
OVERVIEW_TTL_SECONDS = 1.5                   # 결재 집계 캐시 — 한 폴링 주기(7초)의 3연타를 1회로

# ── 카드 대화 맥락 (asked/said) — 클로드 코드가 남기는 세션 대화 JSONL 에서 뽑는다
TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"   # <cwd 인코딩>/<session_id>.jsonl


def encode_cwd(cwd: str) -> str:
    """cwd → ~/.claude/projects 아래 폴더명. 실측 규칙: '/' 와 '.' 를 모두 '-' 로.

    (상수 파일에 함수가 하나 있는 이유: 이건 '클로드가 대화 파일을 어디 두나'라는 규칙이라
     TRANSCRIPT_ROOT 바로 옆이 제자리다. decisions·settings 두 곳이 같은 규칙을 쓴다.)
    """
    return cwd.replace("/", "-").replace(".", "-")
TRANSCRIPT_TAIL_BYTES = 1024 * 1024          # 꼬리만 읽는다 — 실측 165MB 파일이 존재한다.
                                             # 살아있는 카드 9장의 '마지막 대표 발화까지 거리'를 재니
                                             # 8장은 210KB 안이었고 한 장(스크린샷 작업)만 992KB 였다.
                                             # 400KB=7/9 · 1MB=8/9 · 2MB=8/9(더 늘려도 안 는다) →
                                             # 1MB. 9장 전부 훑어 20ms, 그마저 mtime 캐시로 1회뿐.
CARD_CONTEXT_CHARS = 400                     # asked/said 각각의 상한 — 넘으면 잘라내고 '…'를 붙인다.
                                             # 카드 2줄이 목적이라 길면 목록이 못 쓰게 된다
CONTEXT_CACHE_MAX = 64                       # mtime 캐시 상한 (동시 세션 15개 기준 넉넉)


def projects_root() -> Path:
    """현재 프로젝트 루트. 지연 import로 settings→config의 상수 참조와 순환하지 않게 한다."""
    from bridge import settings
    return settings.projects_root()


def upload_root() -> Path:
    return projects_root() / "_uploads"


def decisions_dir() -> Path:
    return projects_root() / "_frogterminal" / "decisions"


def hidden_file() -> Path:
    return projects_root() / "_frogterminal" / "hidden.json"


def favorites_file() -> Path:
    return projects_root() / "_frogterminal" / "favorites.json"


def created_file() -> Path:
    """사이드바 [+ 새 프로젝트] 로 만든 폴더 이름들 — 빈 폴더여도 프로젝트로 보이게 하는 명단."""
    return projects_root() / "_frogterminal" / "created.json"


def audit_file() -> Path:
    return upload_root() / "frogterminal-audit.log"
