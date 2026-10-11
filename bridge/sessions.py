"""세션 = 프로젝트 폴더당 tmux 1개. 다른 세션은 절대 죽이지 않는다(목록·attach만).

예외는 kill_session 하나뿐 — 사용자가 사이드바에서 콕 집어 지목한 그 세션만 끈다(한 번 연
세션이 영영 남아 메모리만 늘어나기 때문). attach·detach·목록은 그대로, 아무것도 죽이지 않는다.
"""
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from bridge import config, settings
from bridge.config import IGNORED_FOLDERS, MONITOR_SESSION

if config.IS_WINDOWS:                            # 윈도우판은 tmux 대신 브릿지 안의 ConPTY 세션 표를 쓴다
    from bridge import winmux

# \w(유니코드) — 이 맥의 프로젝트 폴더에는 한글 이름이 실재한다('보고서양식-시안' 등).
# 영문만 허용하던 옛 규칙은 한글 폴더의 숨기기·터미널 열기를 전부 400/404 로 막았다(실측).
# 막는 것은 그대로 막힌다: 경로 구분자(/), 공백, 개행·제어문자, 선행 점(숨김·상대경로).
_NAME_RE = re.compile(r"^[\w.-]+$")


def is_valid_name(name: str) -> bool:
    """이름 '모양'만 본다 — 경로조작·이상문자 차단. 존재 여부는 묻지 않는다.

    match 가 아니라 fullmatch 인 이유: '$' 는 끝 개행을 허용해 match 로는 'dev\\n' 이
    통과한다(decisions.clear_card 가 같은 함정을 겪고 fullmatch 로 고쳤다).
    """
    return bool(name) and bool(_NAME_RE.fullmatch(name)) and not name.startswith(".")


# ── 병렬 터미널(탭) — 같은 프로젝트에서 터미널을 여러 개 (Zed 처럼)
# 이름 = "<프로젝트>@N" (N 2~9). '@' 는 폴더 이름 규칙(_NAME_RE)에 없어서 진짜 폴더와
# 절대 충돌하지 않고, tmux 세션 이름으로는 유효하다(실측). 탭도 독립 tmux 세션이라
# 브릿지·서버가 죽어도 살아남고, 폰·맥 어디서든 같은 탭 목록이 보인다.
_TAB_RE = re.compile(r"^(?P<base>[\w.-]+)@(?P<n>[2-9])$")


def base_of(name: str) -> str:
    """탭 이름이면 그 프로젝트 이름을, 아니면 그대로 — 파일·업로드·카드가 쓰는 기준."""
    m = _TAB_RE.fullmatch(name or "")
    return m["base"] if m else name


def list_project_folders(root: Path | None = None) -> list[str]:
    root = root if root is not None else config.projects_root()
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    # is_dir()만 보면 루트 밖을 가리키는 심링크도 폴더 행세를 한다. 실제 목적지가 현재 루트
    # 안에 있는 하위 폴더인지까지 확인해 목록과 is_valid_session의 기준을 맞춘다.
    return sorted(p.name for p in children
                  if not p.name.startswith(".") and p.name not in IGNORED_FOLDERS
                  and project_dir(root, p.name) is not None)


def project_dir(root: Path, name: str) -> Path | None:
    """이름의 실제 디렉터리가 realpath 기준으로도 root 안이면 그 목적지를 돌려준다."""
    try:
        real_root = root.resolve(strict=True)
        raw = root / name
        if not raw.is_dir():
            return None
        real = raw.resolve(strict=True)
        if real == real_root or not real.is_relative_to(real_root):
            return None
        return real
    except OSError:
        return None


def is_project_folder(name: str, root: Path | None = None) -> bool:
    """바로 아래에 .git 또는 CLAUDE.md/AGENTS.md 가 있는 안전한 하위 폴더인가.

    마커 파일만 기준으로 삼았더니 실제 프로젝트가 무더기로 '프로젝트 아님'이 됐다
    (2026-08-18 실측: git 저장소 44개 중 마커 보유 22개 — 사내 프로젝트 여럿·
    storymaker 는 물론 frogterminal 자기 자신까지 숨겨졌다). 마커는 있으면 좋은 것이지
    프로젝트의 정의가 아니다. git 저장소인지가 훨씬 정확한 기준이라 이쪽을 먼저 본다.

    .git 은 is_dir 이 아니라 exists 로 본다 — git worktree 에서는 .git 이 디렉터리가
    아니라 gitdir 을 가리키는 파일이라, is_dir 로 보면 워크트리를 통째로 놓친다.

    사이드바 [+ 새 프로젝트] 로 만든 폴더는 .git 도 마커도 없는 빈 폴더라 이 기준에 걸려
    다음 새로고침에 사라졌다(2026-08-22 실사고 — 'demo' 를 만들고 다른 세션에 갔다
    오니 없어짐). 사용자가 만들겠다고 한 것은 그 자체로 프로젝트다 → 명단(created.json)에
    있으면 내용과 무관하게 프로젝트로 친다.
    """
    root = root if root is not None else config.projects_root()
    folder = project_dir(root, name)
    if not folder:
        return False
    if name in load_created():
        return True
    if (folder / ".git").exists():
        return True
    return any((folder / marker).is_file()
               for marker in ("CLAUDE.md", "AGENTS.md"))


def list_tmux_sessions() -> list[str]:
    if config.IS_WINDOWS:
        return winmux.MUX.names()
    try:
        out = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:              # tmux 서버 미기동
        return []
    return [l for l in out.stdout.splitlines() if l]


def session_agents() -> dict[str, str]:
    """세션 이름 → 그 안에서 지금 도는 에이전트("codex"). 사이드바 점 색의 근거(코덱스 = 파랑).

    tmux 가 보는 pane 의 현재 명령(#{pane_current_command})만 읽는다 — 코덱스는 단일 바이너리라
    "codex" 로 보이고, 클로드는 버전 이름("2.1.261")·node 로 보여 특정할 수 없다. 그래서 코덱스만
    표시하고 나머지는 없는 것으로 둔다(클로드가 기본 그림이라 별도 표시가 필요 없다). 탭(base@N)은
    자기 줄이 없으므로 기본 세션의 pane 만 본다. 실패하면 빈 dict(=색 없음) — 목록 자체는 안 막는다.
    """
    if config.IS_WINDOWS:                        # 세션의 자식 프로세스 트리에서 codex 를 찾는다
        found = {}
        for n in winmux.MUX.names():
            s = winmux.MUX.get(n)
            if s is not None and s.agent() == "codex":
                found[n] = "codex"
        return found
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_command}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    agents = {}
    for line in out.stdout.splitlines():
        name, _, cmd = line.partition("\t")
        if name and cmd.strip() == "codex":
            agents[name] = "codex"
    return agents


def process_starts() -> dict[int, str] | None:
    """살아있는 프로세스의 pid → 시작시각 문자열. 한 번의 ps 로 전부. 실패하면 None(=판정불가).

    훅이 카드에 적어 둔 (pid, pidStart) 와 문자열끼리 견주기만 한다 — 시각을 해석하지 않는다.
    pid 번호는 재사용되지만 시작시각까지 같을 수는 없어, 남의 프로세스를 그 세션으로 오인하지 않는다.
    (판정불가를 None 으로 되돌리는 이유: 빈 dict 로 주면 ps 가 한 번 실패한 순간 살아있는 세션이
     전부 '꺼짐'으로 뒤집힌다. 호출자가 옛 기준으로 물러설 수 있게 '모름'을 구분해 준다.)

    LC_ALL=C 고정 필수 — ps 의 lstart 는 로케일을 탄다. 한글 환경의 클로드에서 뜬 훅은
    "2026년 8월 15일 토요일 10시…"를 적고 영어 환경의 브릿지는 "Sat Aug 15 …"를 읽어,
    살아있는 세션이 전부 '꺼짐'으로 판정된 실사고(2026-08-15)가 있다. 양쪽 다 C 로 못박는다.
    """
    if config.IS_WINDOWS:                        # psutil 의 create_time — 훅도 같은 포맷(winmux.format_start)으로 적는다
        return winmux.process_starts()
    try:
        out = subprocess.run(["ps", "-eo", "pid=,lstart="],
                             capture_output=True, text=True, timeout=5,
                             env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    starts = {}
    for line in out.stdout.splitlines():
        pid, _, start = line.strip().partition(" ")
        try:
            starts[int(pid)] = " ".join(start.split())    # 자릿수 맞춤 공백을 접어 훅 쪽과 같은 모양으로
        except ValueError:
            continue
    return starts


def merged_sessions(root: Path | None = None) -> list[dict]:
    root = root if root is not None else config.projects_root()
    alive = set(list_tmux_sessions())
    alive.discard(MONITOR_SESSION)           # CPU 팝업(htop) 세션은 프로젝트가 아니다 — 목록에서 뺀다
    hidden = load_hidden()
    # 즐겨찾기는 '이름 → 누른 순번' 으로 실어보낸다 — 웹이 그 순번만 보고 맨 위로 세운다.
    # 탭(base@N)은 여기 들어올 수 없다: 파일을 읽을 때 is_valid_name 이 '@' 를 걸러낸다.
    fav_rank = {n: i for i, n in enumerate(load_favorites())}
    agents = session_agents() if alive else {}
    folders = set(list_project_folders(root))
    # 탭 세션(base@N)은 사이드바 줄이 아니라 그 프로젝트 항목의 tabs 로 접는다.
    # 프로젝트 폴더가 사라진 고아 탭은 접을 곳이 없으니 그냥 줄로 남긴다(보이고 끌 수 있게).
    tabs: dict[str, list[int]] = {}
    for s in sorted(alive):
        m = _TAB_RE.fullmatch(s)
        if m and m["base"] in (folders | alive - {s}):
            alive.discard(s)
            tabs.setdefault(m["base"], []).append(int(m["n"]))
    names = sorted(folders | alive)
    items = [
        {
            "name": n,
            "path": str(root / n) if n in folders else None,
            # 기본 세션이 꺼져 있어도 탭이 살아 있으면 이 프로젝트는 켜진 것이다
            "alive": n in alive or bool(tabs.get(n)),
            # 숨김은 '표시'일 뿐 — 목록에서 빼지 않는다. 웹이 접어 두고 "숨긴 항목 N개 보기"로
            # 다시 펴야 하고, 결재 수(api_summary)도 숨김과 무관하게 세야 하기 때문이다.
            "hidden": n in hidden,
            # 폴더가 없는 살아있는 tmux 세션은 기존 규칙대로 보이고 끌 수 있어야 한다. 따라서
            # marker 판별은 실제 폴더 항목에만 적용하고, 고아 세션은 project=True로 둔다.
            "project": is_project_folder(n, root) if n in folders else True,
            # 즐겨찾기 순번(0부터) — 즐겨찾기가 아니면 None(JSON null). 숨김과 마찬가지로 '표시'일
            # 뿐이라 목록을 바꾸지 않는다: 맨 위로 올리는 것은 화면의 몫이다.
            "fav": fav_rank.get(n),
            "tabs": sorted(tabs.get(n, [])),
            # 지금 코덱스가 도는 세션이면 "codex" — 웹이 점을 파랑으로 칠한다.
            "agent": agents.get(n),
        }
        for n in names
    ]
    return sorted(items, key=lambda s: (not s["alive"], s["name"]))


def is_valid_session(name: str, root: Path | None = None) -> bool:
    # 파일 API·attach·업로드·끄기가 모두 이 한 관문을 탄다 → 목록에서 뺀 폴더는 여기서도 빼야 한다.
    # 안 그러면 _uploads(append-only 감사로그)·_frogterminal(결재 카드)이 그대로 열린다.
    # 같은 이름의 tmux 세션이 있어도 거부한다 — fail-closed.
    root = root if root is not None else config.projects_root()
    if name == MONITOR_SESSION:              # CPU 팝업(htop) 전용 — 폴더 없이 항상 열 수 있다
        return True
    if m := _TAB_RE.fullmatch(name or ""):
        # 탭은 실제 프로젝트 폴더에만 새로 연다 — _uploads@2 같은 우회는 여기서 같이 죽는다.
        # 이미 살아있는 탭은 폴더가 사라졌어도(고아) 통과 — 붙어서 확인하고 끌 수 있어야 한다.
        base = m["base"]
        if base in IGNORED_FOLDERS or not is_valid_name(base):
            return False
        if project_dir(root, base) is not None:
            return True
        # 루트 밖 심링크가 '폴더 없는 세션' 예외로 되살아나면 안 된다.
        return not (root / base).is_dir() and name in list_tmux_sessions()
    if not is_valid_name(name) or name in IGNORED_FOLDERS:
        return False
    if project_dir(root, name) is not None:
        return True
    return not (root / name).is_dir() and name in list_tmux_sessions()


COMPACT_OPT = "@frog_compact"


def set_compact(name: str, on: bool) -> None:
    """'여러 칸으로 보는 중' 표시를 그 세션에 달거나 뗀다 (멀티 칸 HUD).

    ~/.claude/scripts/statusline.py 가 자기 세션($TMUX_PANE)의 이 표시를 보고 HUD 를 첫 줄만 찍는다 —
    좁은 칸마다 계정 사용량 4줄이 반복되던 것을 웹 하단 한 세트로 모은다. 세션 옵션(-g 아님)이라
    다른 세션·프로그터미널 밖 터미널은 영향이 없다. 실패는 조용히 삼킨다(HUD 가 길게 남을 뿐이다).
    """
    if config.IS_WINDOWS:
        if (s := winmux.MUX.get(name)) is not None:
            s.compact = on
        return
    args = ["tmux", "set-option", "-q", "-t", name]
    args += [COMPACT_OPT, "1"] if on else ["-u", COMPACT_OPT]
    try:
        subprocess.run(args, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def clear_compact_all() -> None:
    """브릿지가 뜰 때 — 지난 실행이 남긴 표시를 전부 뗀다(누가 보고 있는지 기억은 메모리라 재시작에 사라진다)."""
    for name in list_tmux_sessions():
        set_compact(name, False)


def leave_copy_mode(name: str) -> None:
    """그 세션이 tmux 복사 모드(휠 스크롤·드래그로 들어간 상태)면 빠져나온다 — 아니면 아무 일도 없다.

    복사 모드에선 키 입력이 앱(클로드)에 안 가고 화면 안을 돌아다닌다. "위로 스크롤은 조회일 뿐,
    커서는 입력창에 있어야 한다" — 웹이 스크롤 뒤 첫 키 앞에 이걸 부른다.
    if-shell -F 로 tmux 안에서 판단·실행하므로(서버 왕복 1회) 모드 밖에서 Esc 같은 잉여 키가
    앱으로 새지 않는다(Esc 는 클로드의 작업 중단 키라 함부로 못 보낸다). 실패는 조용히 삼킨다.
    """
    if config.IS_WINDOWS:                        # 복사 모드가 없다 — 스크롤백은 브라우저 xterm 이 들고 있다
        return
    try:
        subprocess.run(["tmux", "if-shell", "-F", "-t", name, "#{pane_in_mode}",
                        f"send-keys -t {shlex.quote(name)} -X cancel"],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def _kill_tmux(name: str) -> None:                   # 여기 두는 이유: 테스트 monkeypatch 지점
    if config.IS_WINDOWS:
        winmux.MUX.kill(name)
        return
    subprocess.run(["tmux", "kill-session", "-t", name],
                   capture_output=True, timeout=5, check=True)


def kill_session(name: str) -> bool:
    """지목한 세션 하나를 끈다. 껐으면 True, 이미 꺼져 있었으면 False(멱등).

    관문(is_valid_session)은 호출자가 먼저 통과시킨다. 여기서는 '-t 이름' 하나만 다룬다 —
    kill-server 처럼 전체를 쓸어버리는 명령은 이 파일 어디에도 없어야 한다.
    """
    if name not in list_tmux_sessions():
        return False                                 # 두 번 눌러도 에러가 아니라 조용한 성공
    try:
        _kill_tmux(name)
    except (OSError, subprocess.SubprocessError) as e:
        # 검사와 kill 사이에 세션이 죽는 창(TOCTOU)이 실재한다. 그대로 두면 핸들러가 못 잡아
        # 500 — decisions.send_answer 와 같은 잣대로 LookupError(=404 세션 없음)에 맞춘다.
        # 다만 결과가 '꺼짐'이면 원하던 그대로이므로 한 번 더 실측해 멱등하게 닫는다.
        if name in list_tmux_sessions():
            raise LookupError(name) from e
        return False
    return True


MAX_COPY_BYTES = 100 * 1024                  # 화면 한가득 드래그해도 이 안이다(폭주 방어)


def copy_buffer() -> str:
    """tmux 의 최신 복사 버퍼(드래그로 방금 고른 글자). 없으면 빈 문자열.

    왜 필요한가: tmux 가 mouse on 이라 드래그는 브라우저 선택이 아니라 tmux 선택이 되고,
    이 맥의 ~/.tmux.conf 는 그 결과를 pbcopy(맥 클립보드)로 보낸다. 폰에서 드래그하면
    복사물이 폰이 아니라 맥으로 가 버려 붙여넣기가 영영 안 됐다(2026-08-15 실사고).
    그래서 브라우저가 이 버퍼를 읽어 자기 클립보드에도 넣는다.
    """
    if config.IS_WINDOWS:                        # 복사 버퍼가 없다 — 드래그 선택은 브라우저(xterm)가 직접 복사한다
        return ""
    try:
        out = subprocess.run(["tmux", "show-buffer"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:                  # 버퍼가 하나도 없을 때 — 에러가 아니라 '빈 것'
        return ""
    return out.stdout[:MAX_COPY_BYTES]


def load_hidden() -> set[str]:
    """사이드바에서 접어둔 프로젝트 이름들. 파일이 없거나 깨졌으면 빈 목록으로 시작한다."""
    try:
        data = json.loads(config.hidden_file().read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    # 손으로 고친 파일이 이상한 이름을 API 응답에 그대로 실어보내지 않게 읽을 때도 다시 거른다
    return {n for n in data if isinstance(n, str) and is_valid_name(n)}


def _save_names(path: Path, names: list[str]) -> list[str]:
    """이름 목록을 그 파일에 원자적으로 쓴다 — 숨김·즐겨찾기가 함께 쓰는 한 가지 저장법."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".frogtmp")
    try:
        tmp.write_text(json.dumps(names))
        os.replace(tmp, path)                        # 같은 폴더 안에서만 이동 — 원자적 교체
    except OSError:
        tmp.unlink(missing_ok=True)                  # 실패 잔재를 남기지 않는다
        raise
    return names


def set_hidden(name: str, hidden: bool) -> list[str]:
    """숨김 목록에 넣거나 뺀다 — 갱신된 전체 목록(이름순)을 돌려준다.

    기기(localStorage)가 아니라 서버 파일에 둔다: 폰과 맥이 같은 목록을 봐야 한다.
    """
    names = load_hidden()
    if hidden:
        names.add(name)
    else:
        names.discard(name)
    return _save_names(config.hidden_file(), sorted(names))


def load_favorites() -> list[str]:
    """맨 위로 올린 프로젝트 이름들 — 사용자가 누른 순서 그대로(정렬하지 않는다).

    숨김(set)과 달리 list 인 이유가 이것이다: 순서가 곧 사이드바 맨 위의 줄 순서다.
    손으로 고친 파일이 이상한 이름을 API 응답에 실어보내지 않게 읽을 때도 다시 거른다 —
    is_valid_name 이 '@' 를 막으므로 탭 이름(base@N)은 여기서 통째로 걸린다(즐겨찾기는
    프로젝트 단위다). 같은 이름이 두 번 적혀 있으면 첫 자리만 남긴다(순번이 흔들리지 않게).
    """
    try:
        data = json.loads(config.favorites_file().read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for n in data:
        if isinstance(n, str) and is_valid_name(n) and n not in names:
            names.append(n)
    return names


def set_favorite(name: str, fav: bool) -> list[str]:
    """즐겨찾기에 넣거나 뺀다 — 갱신된 전체 목록(누른 순서)을 돌려준다.

    이름순으로 정렬하지 않는다. 새로 누른 것은 맨 뒤에 붙고, 이미 들어 있으면 자리를 옮기지
    않는다 — 7초 폴링이 다시 그릴 때마다 줄이 뛰면 사용자가 누르려던 줄을 놓친다.
    """
    names = load_favorites()
    if fav:
        if name not in names:
            names.append(name)
    else:
        names = [n for n in names if n != name]
    return _save_names(config.favorites_file(), names)


def load_created() -> set[str]:
    """[+ 새 프로젝트] 로 만든 폴더 이름들 — 빈 폴더여도 사이드바에 남아야 하는 것들.

    숨김과 같은 모양(set)이다: 순서는 뜻이 없고 '명단에 있나'만 본다. 폴더가 지워지면
    목록(list_project_folders)에 안 잡히므로 명단에 이름이 남아 있어도 유령이 뜨지 않는다.
    """
    try:
        data = json.loads(config.created_file().read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {n for n in data if isinstance(n, str) and is_valid_name(n)}


def mark_created(name: str) -> list[str]:
    """새 프로젝트 명단에 넣는다 — 갱신된 전체 목록(이름순)을 돌려준다. 이미 있으면 그대로."""
    names = load_created()
    names.add(name)
    return _save_names(config.created_file(), sorted(names))


# 드래그 복사 뒤 어디로 가나 — "위로 올려 보던 중이면 그 자리에 남고, 맨 아래였으면 바로 실시간으로".
# 기본(tmux-yank 도 같다)은 copy-pipe-and-cancel 이라 손을 떼는 순간 화면이 맨 아래로 튀어 "위로 올려
# 보다가 필요한 부분만 복사"가 안 됐다. 그래서 취소를 뺐더니 이번엔 반대 사고 —
# 드래그로 들어간 복사 모드는 휠을 내려도 자동으로 안 빠져나와(휠로 들어간 것만 -e 로 빠진다) 맨 아래에서
# 복사 한 번 하면 그 뒤 새 출력이 화면에 안 그려졌다. 키를 쳐야 unmode 로 풀리며 밀린 출력이 한꺼번에
# 튀어나왔다(2026-09-20 실측: 5개 세션이 in_mode=1·scroll=0 에 갇혀 있었다 — 윈도우 "갱신 안 됨"도 이것).
# 그래서 ①복사 뒤 scroll_position 이 0(맨 아래)이면 취소, 아니면 남는다 ②복사 모드에서 휠을 내려 맨 아래에
# 닿으면(선택 중이 아닐 때) 빠져나온다. 키 테이블은 tmux 에 세션 단위가 없어 전역 바인딩이다
# (이 맥의 tmux 는 사실상 프로그터미널 전용). 명령 안의 `\;` 는 바인딩 내부 구분자(argv 로는 백슬래시+세미콜론).
_AT_BOTTOM = "#{==:#{scroll_position},0}"
_AT_BOTTOM_NO_SEL = "#{&&:#{==:#{scroll_position},0},#{==:#{selection_present},0}}"
_COPY_BINDS = []
for _table in ("copy-mode", "copy-mode-vi"):
    _COPY_BINDS += [";", "bind-key", "-T", _table, "MouseDragEnd1Pane",
                    "send-keys", "-X", "copy-pipe", "pbcopy", "\\;",
                    "if-shell", "-F", _AT_BOTTOM, "send-keys -X cancel"]
    _COPY_BINDS += [";", "bind-key", "-T", _table, "WheelDownPane",
                    "select-pane", "\\;", "send-keys", "-X", "-N", "5", "scroll-down", "\\;",
                    "if-shell", "-F", _AT_BOTTOM_NO_SEL, "send-keys -X cancel"]


def _session_opts(name: str) -> list[str]:
    """attach 때마다 그 세션에만 못박는 tmux 옵션 — 사유는 attach_cmd 본문 주석 참조."""
    return [";", "set-option", "-t", name, "mouse", "on",
            ";", "set-option", "-t", name, "status", "off",
            ";", "set-option", "-w", "-t", name, "pane-border-status", "off"] + _COPY_BINDS


def attach_cmd(name: str, root: Path | None = None, agent: str = "shell") -> list[str]:
    root = root if root is not None else config.projects_root()
    cmd = ["tmux", "new-session", "-A", "-s", name]
    if name == MONITOR_SESSION:
        # CPU 팝업 — 설정 에이전트 대신 htop 을 곧바로. zsh -lc(로그인 셸 PATH)라 brew htop 이
        # 잡히고, 없으면 top 으로 물러선다. htop 을 q 로 끝내면 pane 과 세션도 같이 닫힌다.
        return cmd + ["zsh -lc 'htop || top'"] + _session_opts(name)
    base = base_of(name)                    # 탭(base@N)도 그 프로젝트 폴더에서 시작한다
    work = project_dir(root, base)
    if work:
        cmd += ["-c", str(work)]
    # 새로 만드는 세션이면 설정한 에이전트(claude·codex)를 곧바로 띄운다 — 셸에 이름을 치는
    # 한 단계를 없앤다(Orca 차용). 이미 있는 세션에는 -A 가 명령을 무시하고 붙기만 한다(실측).
    # zsh -lic: 브릿지(launchd)의 앙상한 PATH 대신 로그인 셸 PATH 를 타고, -i 로 .zshrc 의
    # alias(cl 같은 약칭)까지 살린다. shlex.quote — 플래그 붙은 명령이 통째로 -c 한 인자가 되게.
    # 에이전트가 끝나면 pane 도 닫힌다 = 빈 셸 좀비 세션이 남지 않는다(의도). 그래서 exit 한 번에
    # 세션이 통째로 사라지고 다시 만들어진다 — 그때 맥락을 잇는 것이 settings 의 '이어서 시작'이다.
    if launch := settings.launch_command(agent, session=name, cwd=work):
        cmd += [f"zsh -lic {shlex.quote(launch)}"]
    # 붙는 그 세션에만 mouse on. 폰 스와이프는 웹에서 휠 리포트로 바뀌어 오는데,
    # 이걸 tmux 가 받아 copy-mode 스크롤로 이어주려면 이 옵션이 필요하다.
    # 지금은 ~/.tmux.conf 의 `set -g mouse on` 덕에 이미 켜져 있지만, 그 파일에 기대다
    # 조용히 깨지지 않도록 붙을 때마다 못박는다. 전역(-g)·다른 세션은 건드리지 않는다.
    # (`;` 는 tmux 의 명령 구분자 — 셸을 안 거치므로 그대로 인자로 넘긴다.)
    # status off — 프로그터미널 세션은 창이 하나뿐이라 tmux 상태줄이 패널 헤더의 세션 이름을
    # 세 번째로 반복할 뿐이고(디자이너 지적), 파란 기본색이 팔레트 밖이다. 그 세션만 끈다.
    # pane-border-status 도 함께 — ~/.tmux.conf 가 pane 상단에 이름표를 또 그린다(-w = 창 옵션).
    return cmd + _session_opts(name)


def win_shell() -> str:
    """윈도우 세션의 셸. pwsh(7)가 있어야 `a || b` 가 되므로 우선하고, 없으면 윈도우 PowerShell."""
    import shutil
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"


def win_launch(name: str, root: Path | None = None, agent: str = "shell") -> tuple[list[str], str | None]:
    """윈도우판 attach_cmd — (argv, 시작 폴더). 에이전트가 끝나면 세션도 끝난다(맥판과 같은 약속).

    셸은 pwsh 다. 프로파일을 그대로 타서 PATH·별칭이 평소 터미널과 같다. 설정한 에이전트는 `-Command`
    한 줄(settings.launch_command — `claude … --continue || claude …`)로 곧바로 띄운다.
    """
    import sys
    root = root if root is not None else config.projects_root()
    shell = win_shell()
    if name == MONITOR_SESSION:                      # CPU 팝업 — htop 자리. q 로 끝내면 세션도 닫힌다
        monitor = Path(__file__).with_name("monitor_win.py")
        return [shell, "-NoLogo", "-NoProfile", "-Command",
                f"& '{sys.executable}' '{monitor}'"], None
    base = base_of(name)                             # 탭(base@N)도 그 프로젝트 폴더에서 시작한다
    work = project_dir(root, base)
    argv = [shell, "-NoLogo"]
    if launch := settings.launch_command(agent, session=name, cwd=work):
        argv += ["-Command", launch]
    return argv, (str(work) if work else None)
