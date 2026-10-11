"""파일 패널 — 프로젝트 폴더 안 전용 목록·읽기·쓰기.

v1 은 "파일 브라우저 없음"이 보안 원칙이었다. v2 에서 방침 전환하며 대신:
경로 화이트리스트(프로젝트 루트 안만) + 비밀파일 denylist + 원자적 쓰기 + 감사로그(서버층)로 잠근다.
"""
import codecs
import os
from pathlib import Path
from urllib.parse import unquote

from bridge import config, sessions

# 차단 규칙은 여기 셋뿐이고 판정은 _denied() 하나만 한다 — 규칙이 흩어지면 서로 어긋나 구멍이 난다.
# 경계 매치(".env" 뒤에 점이나 끝) 대신 접두 매치로 넓힌다: 경계 방식은 .envrc 를 그냥 통과시켰다.
# 넓게 막아 .gitignore 를 못 보는 손해 < 자격증명 한 번 새는 손해 — fail-closed.
DENY_PREFIXES = (".env",                     # .env / .env.local / .env.bak / .envrc(direnv)
                 ".git",                     # .git 내부물·.git-credentials (remote url 토큰)
                 ".npmrc", ".netrc", ".aws", ".ssh", ".pgpass",
                 "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
DENY_PARTS = ("credential", "secret",        # 위치 무관 — credentials-backup.json / secrets-2024.txt
              "frogterminal-audit")          # 감사로그 — _uploads 차단(sessions)에 더한 이중 방어
DENY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks",
                 "auth.json", "token.json",  # ~/.codex/auth.json · *_token.json 류(코덱스 교차점검 2026-10-11)
                 ".frogtmp")                 # write_text 가 SIGKILL 로 남긴 잔재 — 목록·읽기에서 뺀다


class NotAllowed(Exception): pass            # 밖·비밀·없음 — 전부 404 로 뭉개서 존재를 안 알린다
class TooLarge(Exception): pass
class IsBinary(Exception): pass
class UnsupportedImage(Exception): pass
class Conflict(Exception): pass


IMAGE_TYPES = {                              # 확장자는 후보일 뿐 — 아래 매직 판정과 같아야 나간다
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "bmp": "image/bmp",
}


def _denied(name: str) -> bool:
    # strip(): 앞뒤 공백도 POSIX 에선 legal 한 파일명이다. ".env " 는 접두 매치에 걸리는데
    # " .env" 는 안 걸리던 비대칭이 곧 구멍이었다 — 판정 전에 다듬어 양쪽을 같게 만든다.
    n = name.strip().casefold()              # macOS(APFS)는 대소문자 무시라 .GIT 이 곧 .git —
    return (n.startswith(DENY_PREFIXES)      # resolve() 는 요청한 대소문자를 그대로 돌려주므로
            or n.endswith(DENY_SUFFIXES)     # 여기서 접어주지 않으면 .GIT/config 로 통째 우회된다.
            or any(w in n for w in DENY_PARTS))


def resolve_openable(raw: str) -> Path:
    """터미널에 찍힌 절대경로를 '맥에서 열기'용으로 검증한다 — 실재하는 절대경로면 어디든 통과.

    ★의도된 예외(프로젝트 밖에 있는 파일도 무조건 열리게 한다). 폴더 제한을 두지
    않는 근거: 이 화면은 그 자체가 터미널이라 사용자가 `open <경로>` 를 직접 칠 수 있다 —
    여기서 막아도 우회가 아니라 '불편'만 남는다(새로 열리는 문이 없다). 게다가 파일 내용은
    브라우저로 넘어오지 않고 맥 화면에 뜰 뿐이고, 요청 자체가 테일스케일 신원 검사를 통과한
    사용자의 것이다. 반대로 파일을 '읽어서 브라우저로 보내는' API(read_text·list_dir)는
    폰에서도 열리므로 프로젝트 폴더·비밀파일 제한을 그대로 유지한다 — 둘을 섞지 말 것.
    여는 것은 감사로그에 남긴다(server.api_open).
    """
    if not (raw.startswith("/") or (config.IS_WINDOWS and Path(raw).is_absolute())) or "\x00" in raw:
        raise NotAllowed(raw)                # 상대경로·NUL 은 애초에 우리가 만든 값이 아니다
    path = Path(raw).resolve()               # 심링크는 진짜 목적지로
    if not path.exists():
        raise NotAllowed(raw)                # 없는 파일 — 404 로 "왜 안 열리는지"를 알려 준다
    return path


def _root(project: str) -> Path:
    if not sessions.is_valid_session(project):
        raise NotAllowed(project)
    root = sessions.project_dir(config.projects_root(), project)
    if root is None:
        raise NotAllowed(project)
    return root


def _guard(root: Path, path: Path, rel: str) -> Path:
    """심링크를 따라간 '진짜 목적지'로 루트 포함·비밀파일을 재검사 — 모든 API 가 이 한 겹을 탄다."""
    path = path.resolve()                    # 심링크 추적 — 진짜 목적지로 판정
    if path != root:
        if not path.is_relative_to(root):
            raise NotAllowed(rel)
        if any(p == ".." or _denied(p) for p in path.relative_to(root).parts):
            raise NotAllowed(rel)            # 심링크가 루트 안 비밀파일을 가리키는 경우
    return path


def safe_path(project: str, rel: str) -> Path:
    root = _root(project)
    # aiohttp 가 이미 디코딩한 값을 한 번 더 푸는 이중 디코딩이다. 알려진 대가: 이름에 리터럴
    # '%2F' 가 든 정상 파일은 목록엔 뜨는데 열면 NotAllowed. 그래도 그대로 두는 이유는 여기서
    # 푼 결과가 아래 parts 검사를 다시 타기 때문 — 방향이 fail-closed 라 안전한 쪽으로만 틀린다.
    # (safe_path 를 직접 부르는 테스트·호출자에게는 이 한 겹이 유일한 인코딩 방어이기도 하다.)
    rel = unquote(rel or "")
    if rel.startswith(("/", "\\")) or Path(rel).anchor:
        raise NotAllowed(rel)                # 절대경로(윈도우: 드라이브·UNC 포함)는 조용히 relative化하지 않고 바로 차단
    parts = Path(rel).parts
    if any(p == ".." or _denied(p) for p in parts):
        raise NotAllowed(rel)
    return _guard(root, root / rel, rel)


def resolve_existing(project: str, rel: str) -> Path:
    """'맥에서 열기(open)'용 — 텍스트 여부·크기는 안 따진다(이미지·PDF 가 대상이다).

    검증은 읽기와 완전히 같은 잣대(safe_path: 루트 안·비밀파일 denylist·심링크 목적지 재검사).
    없는 경로는 같은 404 로 뭉갠다 — 열기 API 가 존재 탐지기로 쓰이면 안 된다.
    """
    p = safe_path(project, rel)
    if not p.exists():
        raise NotAllowed(rel)
    return p


def make_dir(project: str, rel: str) -> None:
    """새 폴더(파일 패널의 📁+). 검증은 읽기와 완전히 같은 잣대(safe_path).

    루트 자체·빈 이름은 거부. 부모가 없으면 FileNotFoundError, 이미 있으면 FileExistsError —
    호출자(server)가 각각 404·409 로 옮긴다. 한 번에 한 단계만 만든다(parents=False) —
    오타로 깊은 트리가 통째로 생기는 사고를 막는다.
    """
    if not (rel or "").strip().strip("/"):
        raise NotAllowed(rel)
    safe_path(project, rel).mkdir()


def list_dir(project: str, rel: str = "") -> list[dict]:
    root = _root(project)
    d = safe_path(project, rel)
    if not d.is_dir():
        raise NotAllowed(rel)
    entries = []
    for p in d.iterdir():
        if _denied(p.name):
            continue
        try:
            target = _guard(root, p, p.name) # 이름만 보면 심링크 대상의 크기·종류가 새어나간다 —
            size = target.stat().st_size if target.is_file() else None
        except (NotAllowed, OSError):        # read_text 와 같은 잣대로 목적지까지 본 뒤 통째로 뺀다
            continue
        entries.append({"name": p.name, "dir": target.is_dir(), "size": size})
    entries.sort(key=lambda e: (not e["dir"], e["name"]))   # 폴더 먼저 — 판정 끝난 뒤 정렬
    return entries


def version_of(st: os.stat_result) -> str:
    """파일 버전 토큰 — 반드시 '문자열'이다.

    st_mtime_ns 는 19자리(약 1.75e18)라 JS Number.MAX_SAFE_INTEGER(9.007e15)를 두 자릿수 넘는다.
    JSON 숫자로 내보내면 브라우저가 JSON.parse 하는 순간 하위 자릿수를 조용히 반올림하고,
    그 값이 되돌아오면 비교가 영영 어긋나 저장이 매번 409 가 된다. 문자열은 JS 가 손대지 않는다.
    """
    return str(st.st_mtime_ns)


def _sniff_text(path: Path) -> None:
    """앞 8KB 로 '여기서 고칠 수 있는 파일인가'를 본다 — 파일이 커도 비용이 일정하다."""
    with open(path, "rb") as f:
        head = f.read(8192)
    if b"\x00" in head:
        raise IsBinary(path.name)
    try:
        # UTF-8 이 아닌 파일(CP949 엑셀 CSV·옛 .txt)은 NUL 이 없어 위 검사를 그냥 통과한다.
        # final=False → 8KB 경계에서 잘린 멀티바이트 꼬리는 오류로 보지 않는다.
        codecs.getincrementaldecoder("utf-8")().decode(head)
    except UnicodeDecodeError as e:
        raise IsBinary(path.name) from e


def _sniff_image(data: bytes) -> str | None:
    """파일 머리의 형식 표식만 판정한다 — 확장자나 브라우저의 MIME 추측은 쓰지 않는다."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    # AVIF는 ISO BMFF라 ftyp 상자의 major/compatible brand 중 avif/avis가 있어야 한다.
    # HEIC도 같은 그릇을 쓰므로 단순히 ftyp만 보고 AVIF로 내보내면 안 된다.
    if len(data) >= 16 and data[4:8] == b"ftyp":
        box_size = int.from_bytes(data[:4], "big")
        end = min(box_size, len(data)) if box_size >= 16 else 0
        brands = [data[8:12], *(data[i:i + 4] for i in range(16, end, 4))]
        if b"avif" in brands or b"avis" in brands:
            return "image/avif"
    return None


def read_image(project: str, rel: str) -> dict:
    """브라우저가 직접 그릴 수 있는 이미지 하나를 읽는다(쓰기 경로와 완전히 분리)."""
    p = safe_path(project, rel)               # ★텍스트와 같은 세션·루트·denylist·심링크 관문
    if not p.is_file():
        raise NotAllowed(rel)
    requested = Path(unquote(rel or ""))      # safe_path와 같은 이중 디코딩 뒤 '요청한 이름'을 본다
    ext = requested.suffix.removeprefix(".").casefold()
    # SVG는 XML 안에 스크립트·외부 참조를 품을 수 있다. 격리·정화 정책이 없는 이번 작업에서는
    # 이미지 응답으로 열지 않는다(HEIC는 주요 브라우저가 그대로 그리지 못한다).
    if ext == "svg":
        raise UnsupportedImage("SVG는 안전 문제 때문에 파일 패널에서 이미지로 열 수 없습니다 — 맥에서 열기를 사용하세요")
    if ext in {"heic", "heif"}:
        raise UnsupportedImage("HEIC는 브라우저에서 바로 표시할 수 없습니다 — 맥에서 열기를 사용하세요")
    expected = IMAGE_TYPES.get(ext)
    if expected is None:
        raise UnsupportedImage("파일 패널에서 지원하는 이미지 형식이 아닙니다")
    st = p.stat()
    if st.st_size > config.MAX_IMAGE_FILE_BYTES:
        raise TooLarge(rel)
    data = p.read_bytes()                     # 검증한 경로의 바이트만 응답 본문으로 고정(최대 20MB)
    if len(data) > config.MAX_IMAGE_FILE_BYTES:   # stat 뒤 파일이 커진 경합도 같은 상한으로 막는다
        raise TooLarge(rel)
    actual = _sniff_image(data[:65536])       # AVIF compatible brand까지 보되 머리 64KB 이상은 안 읽는다
    if actual != expected:
        raise UnsupportedImage("확장자와 실제 파일 형식이 맞지 않아 이미지로 열 수 없습니다")
    return {"content": data, "contentType": actual, "size": len(data), "name": requested.name}


def read_text(project: str, rel: str) -> dict:
    p = safe_path(project, rel)
    if not p.is_file():
        raise NotAllowed(rel)
    st = p.stat()
    if st.st_size > config.MAX_TEXT_FILE_BYTES:
        raise TooLarge(rel)
    _sniff_text(p)
    try:
        # errors="replace" 였다 — 못 읽은 바이트가 U+FFFD 로 바뀌어 보이고, 그 화면을 저장하면
        # 원본이 복구 불가로 망가진다(실측: CP949 40바이트 → 저장 후 77바이트, cp949 디코딩도 실패).
        # "UTF-8 이 아니라 여기선 못 고칩니다"(415)가 말없이 파괴하는 것보다 낫다.
        content = p.read_text()                  # errors="strict" (기본값)
    except UnicodeDecodeError as e:
        raise IsBinary(rel) from e               # 8KB 뒤에 숨은 한 글자도 여기서 걸린다
    return {"content": content, "mtimeNs": version_of(st), "size": st.st_size}


def write_text(project: str, rel: str, content: str, base_mtime_ns: str) -> str:
    p = safe_path(project, rel)
    if not p.is_file():
        raise NotAllowed(rel)                # 신규 생성은 v2 범위 밖 — 기존 파일만 고친다
    data = content.encode()
    if len(data) > config.MAX_TEXT_FILE_BYTES:
        raise TooLarge(rel)
    _sniff_text(p)                           # 빈 파일은 \x00 이 없어 텍스트로 통과 — 새 내용 작성 가능
    if version_of(p.stat()) != base_mtime_ns:
        raise Conflict(rel)                  # 열어본 뒤 다른 데서 바뀜 — 덮어쓰기 금지
                                             # 문자열끼리 비교 — 정수가 와도 안 맞아 Conflict(터지진 않음)
    tmp = p.with_name(p.name + ".frogtmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, p)                   # 같은 디렉터리 안에서만 이동 — 원자적 교체
    except OSError:
        tmp.unlink(missing_ok=True)          # 실패 잔재가 git status 를 더럽히지 않게 정리
        raise                                # SIGKILL 로 못 지운 잔재는 .gitignore + denylist 가 받는다
    return version_of(p.stat())
