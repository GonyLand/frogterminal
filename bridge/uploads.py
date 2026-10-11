"""붙여넣은 파일을 KST 날짜 폴더에 저장할 경로를 만든다. 저장만 하고 실행은 없다."""
import re
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath

from bridge import config
from bridge.config import KST

_SAFE_RE = re.compile(r"[^\w.\-가-힣]", re.UNICODE)  # 한글 허용


def sanitize_filename(raw: str) -> str:
    name = PurePosixPath(raw.replace("\\", "/")).name   # 경로 성분 제거
    name = _SAFE_RE.sub("_", name).strip("._")
    return name or "file"


def sanitize_subdir(raw: str) -> str:
    """파인더에서 끌어온 '폴더'의 상대경로 — 성분별로 파일명과 같은 소독을 거친다.

    빈 성분·`.`·`..` 은 소멸하므로 상위 이동이 구조적으로 불가능하고, 깊이는 10으로 자른다.
    """
    parts = [sanitize_filename(p) for p in raw.replace("\\", "/").split("/")
             if p not in ("", ".", "..")]
    return "/".join(parts[:10])


def build_upload_path(project: str, filename: str,
                      root: Path | None = None, now: datetime | None = None,
                      subdir: str = "") -> Path:
    root = root if root is not None else config.upload_root()
    now = now or datetime.now(KST)                      # 전역 규칙: 명시적 KST
    day_dir = root / project / now.strftime("%Y-%m-%d")
    if subdir:                                          # 폴더 드롭 — 하위 구조를 그대로 살린다
        day_dir = day_dir / sanitize_subdir(subdir)
    day_dir.mkdir(parents=True, exist_ok=True)
    base = sanitize_filename(filename)
    candidate, n = day_dir / base, 0
    while candidate.exists():
        n += 1
        stem, dot, ext = base.partition(".")
        candidate = day_dir / (f"{stem}-{n}{dot}{ext}" if dot else f"{base}-{n}")
    return candidate


# ---------- 캐시(붙여넣기 사본) 조회·비우기 ----------
# 대표 지시(2026-08-15): "기존 캐시 삭제해라, 앞으로도 설정에 비우기 버튼을 넣어라".
# 지울 수 있는 것은 <프로젝트>/<YYYY-MM-DD> 폴더뿐이다 — 같은 자리에 사는 감사로그
# (frogterminal-audit.log)와 낯선 파일은 어떤 경우에도 건드리지 않는다.
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day_dirs(root: Path):
    if not root.is_dir():
        return
    for proj in sorted(root.iterdir()):
        if not proj.is_dir() or proj.is_symlink():   # 파일(감사로그)·심링크는 건너뛴다
            continue
        for day in sorted(proj.iterdir()):
            if day.is_dir() and not day.is_symlink() and _DAY_RE.fullmatch(day.name):
                yield day


def usage(root: Path | None = None) -> dict:
    """캐시가 지금 얼마나 쌓였나 — 지울 수 있는 것만 센다(감사로그 제외)."""
    root = root if root is not None else config.upload_root()
    total = count = 0
    for day in _day_dirs(root):
        for p in day.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
                    count += 1
            except OSError:
                continue
    return {"bytes": total, "files": count}


def clear(root: Path | None = None) -> dict:
    """캐시를 비운다 — 지운 용량·파일 수를 돌려준다. 감사로그는 그대로 남는다."""
    root = root if root is not None else config.upload_root()
    before = usage(root)
    for day in list(_day_dirs(root)):
        shutil.rmtree(day, ignore_errors=True)
    for proj in sorted(root.iterdir()) if root.is_dir() else []:
        if proj.is_dir() and not proj.is_symlink() and not any(proj.iterdir()):
            proj.rmdir()                             # 빈 프로젝트 폴더까지 정리
    after = usage(root)
    return {"bytes": before["bytes"] - after["bytes"],
            "files": before["files"] - after["files"], "left": after}
