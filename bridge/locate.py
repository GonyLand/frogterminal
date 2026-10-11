"""드래그된 파일·폴더의 '원본' 경로 찾기 — 사본 업로드 대신 진짜 경로를 쓰기 위해.

배경(2026-08-15 대표 지적): 파인더에서 끈 파일이 전부 _uploads 사본으로 갔다 —
다른 폴더의 같은 이름 둘이 '대본.md/대본-1.md' 로 뭉개지고, 큰 파일은 중복으로 쌓였다.
브라우저는 보안상 원본 경로를 안 주므로, 맥 쪽에서 Spotlight(mdfind)로 후보를 좁히고
크기·수정시각(파일) 또는 1단계 자식 목록(폴더)을 stat 으로 실검증한다.
정확히 하나로 특정될 때만 경로를 준다 — 애매하면 웹이 업로드로 물러선다(fail-safe).
셸을 거치지 않고 mdfind 인자로만 이름을 넘긴다. 미색인 위치(외장·숨김 폴더)는 못 찾는다.
한글명은 확장자를 떼고 질의하고 NFC로 비교해 Spotlight의 NFD 경로와 맞춘다.
"""
import json
import os
import subprocess
import time
import unicodedata
from pathlib import Path

from bridge import config

_MAX_CANDIDATES = 200                        # mdfind 가 쏟아내는 흔한 이름(README.md 류) 방어


def _candidates(name: str) -> list[Path]:
    # 점이 든 폴더도 질의만 넓어질 뿐, 아래 이름 완전일치로 다시 걸러진다.
    stem = Path(name).stem or name
    try:
        out = subprocess.run(["mdfind", "-name", stem],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    hits = []
    upload_root = config.upload_root()        # 설정 파일은 후보마다 다시 읽지 않고 검색 한 번에 한 번
    for line in out.stdout.splitlines():
        p = Path(line)
        # 이름 완전일치만 + 업로드 캐시는 제외(사본이 원본 행세를 하면 이 기능의 목적이 죽는다)
        if (line and unicodedata.normalize("NFC", p.name) == unicodedata.normalize("NFC", name)
                and upload_root not in p.parents):
            hits.append(p)
        if len(hits) >= _MAX_CANDIDATES:
            break
    return hits


def find_file(name: str, size: int, mtime_ms: int) -> list[str]:
    """이름+크기+수정시각(±2초 — 파일시스템 정밀도)이 전부 맞는 원본 후보들."""
    hits = []
    for p in _candidates(name):
        try:
            st = p.stat()
        except OSError:
            continue
        if p.is_file() and st.st_size == size and abs(st.st_mtime * 1000 - mtime_ms) < 2000:
            hits.append(str(p))
        if len(hits) > 3:                    # 하나로 특정 실패가 이미 확정 — 더 셀 필요 없다
            break
    return hits


# ---------- 화면 캡처 원본 찾기 ----------
# 대표 지적(2026-08-15): 캡처를 붙여넣을 때마다 _uploads 에 사본이 쌓인다 —
# 맥 캡처는 파일로도 남으니 그 원본 경로를 넣으면 사본이 아예 필요 없다.
CAPTURE_EXTS = {".png", ".jpg", ".jpeg", ".heic", ".tiff", ".gif", ".pdf"}


def screenshot_dir() -> Path:
    """맥 화면 캡처 저장 폴더(시스템 설정값). 못 읽으면 기본값 ~/Desktop.

    plutil 로 plist 를 JSON 으로 바꿔 읽는다 — `defaults read` 는 한글 폴더명을
    \\Uxxxx 로 이스케이프해서 내주기 때문에 경로가 깨진다(실측).
    """
    plist = Path.home() / "Library" / "Preferences" / "com.apple.screencapture.plist"
    try:
        out = subprocess.run(["plutil", "-convert", "json", "-o", "-", str(plist)],
                             capture_output=True, text=True, timeout=5)
        loc = json.loads(out.stdout).get("location") if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
        loc = None
    return Path(os.path.expanduser(loc)) if isinstance(loc, str) and loc else Path.home() / "Desktop"


def _dimensions(path: Path) -> tuple[int, int] | None:
    try:
        out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    size = {}
    for line in out.stdout.splitlines():
        key, _, value = line.strip().partition(": ")
        if key in ("pixelWidth", "pixelHeight") and value.isdigit():
            size[key] = int(value)
    if "pixelWidth" in size and "pixelHeight" in size:
        return size["pixelWidth"], size["pixelHeight"]
    return None


def recent_capture(width: int, height: int, within_s: float = 90) -> str | None:
    """방금 찍은 캡처 파일 — 붙여넣은 이미지와 '픽셀 크기'가 같은 최신 파일 하나.

    시간창만으로는 안 된다: 웹에서 복사한 다른 이미지를 붙여넣는 순간 엉뚱한 캡처 경로가
    들어가 버린다. 가로·세로까지 맞는 것만 통과시킨다(sips 실측, 최신 5개만 검사).
    """
    now = time.time()
    try:
        entries = list(screenshot_dir().iterdir())
    except OSError:
        return None
    cands = []
    for p in entries:
        if p.name.startswith(".") or p.suffix.lower() not in CAPTURE_EXTS:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if p.is_file() and 0 <= now - st.st_mtime <= within_s:
            cands.append((st.st_mtime, p))
    for _, p in sorted(cands, key=lambda c: c[0], reverse=True)[:5]:
        if _dimensions(p) == (width, height):
            return str(p)
    return None


def find_dir(name: str, children: list) -> list[str]:
    """이름이 같고 1단계 자식들이 전부 들어 있는 폴더 후보들."""
    want = {unicodedata.normalize("NFC", c) for c in children if isinstance(c, str) and c}
    if not want:                             # 빈 폴더는 특정할 근거가 없다 — 업로드로 물러서게 한다
        return []
    hits = []
    for p in _candidates(name):
        if not p.is_dir():
            continue
        try:
            have = {unicodedata.normalize("NFC", e.name) for e in p.iterdir()}
        except OSError:
            continue
        if want <= have:
            hits.append(str(p))
        if len(hits) > 3:
            break
    return hits
