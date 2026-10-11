#!/usr/bin/env python3
"""frogterminal 훅 6개를 ~/.claude/settings.json 에 멱등 등록·해제한다.

    python3 hooks/install_hooks.py              # 등록(이미 있으면 변경 없음)
    python3 hooks/install_hooks.py --dry-run    # 무엇을 바꿀지 보여만 준다 — 파일은 그대로
    python3 hooks/install_hooks.py --uninstall  # 우리가 넣은 항목만 뺀다(남의 훅은 그대로)

남의 전역 설정을 건드리는 단계라(공개 배포 점검 S7) 미리보기와 되돌리기를 같이 둔다.
첫 변경 전에 settings.json.bak 을 한 번만 남긴다(매번 덮으면 두 번째 실행이 원본을 지운다).
"""
import json
import os
import shutil
import sys

# PreCompact·SessionStart 는 /compact 를 위해 있다 — 압축엔 Stop 이 안 울려서,
# 이 둘이 없으면 압축이 끝난 세션이 계속 '진행중…'으로 남는다(2026-08-28 실측).
EVENTS = ["Notification", "Stop", "UserPromptSubmit", "SessionEnd",
          "PreCompact", "SessionStart"]
DEFAULT_SETTINGS = os.path.expanduser("~/.claude/settings.json")
_HOOK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frog_hook.py")
if sys.platform == "win32":
    # 윈도우: 클로드는 훅 명령을 bash 로 실행하므로 역슬래시가 사라진다 → 슬래시 경로 + 따옴표.
    # 브릿지 가상환경의 python 으로 돌려야 psutil 이 있다(이 스크립트를 그 python 으로 돌렸을 때 sys.executable).
    DEFAULT_CMD = f'"{sys.executable.replace(chr(92), "/")}" "{_HOOK_FILE.replace(chr(92), "/")}"'
else:
    DEFAULT_CMD = "python3 " + _HOOK_FILE


def _load(settings_path: str) -> dict:
    if not os.path.exists(settings_path):
        return {}
    with open(settings_path, encoding="utf-8") as f:
        return json.load(f)


def _write(settings_path: str, data: dict) -> None:
    if os.path.exists(settings_path):
        backup = settings_path + ".bak"
        if not os.path.exists(backup):                        # 만지기 전 백업 — 처음 한 번만.
            shutil.copy2(settings_path, backup)
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    tmp = settings_path + ".tmp"
    # newline="\n" — 윈도우 텍스트 모드는 줄끝을 CRLF 로 바꿔 사용자가 LF 로 맞춰 둔 settings.json 전체가 diff 가 된다.
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, settings_path)


def _ours(entry: dict, hook_cmd: str) -> bool:
    return any(h.get("command") == hook_cmd for h in entry.get("hooks", []))


def plan_install(data: dict, hook_cmd: str = DEFAULT_CMD) -> list[str]:
    """등록이 필요한 이벤트 목록(비면 변경 없음). 파일은 건드리지 않는다."""
    hooks = data.get("hooks", {})
    return [ev for ev in EVENTS if not any(_ours(e, hook_cmd) for e in hooks.get(ev, []))]


def install(settings_path: str = DEFAULT_SETTINGS, hook_cmd: str = DEFAULT_CMD, dry_run: bool = False) -> bool:
    data = _load(settings_path)
    missing = plan_install(data, hook_cmd)
    if dry_run or not missing:
        return bool(missing)
    hooks = data.setdefault("hooks", {})
    for ev in missing:
        hooks.setdefault(ev, []).append({"hooks": [{"type": "command", "command": hook_cmd, "timeout": 5}]})
    _write(settings_path, data)
    return True


def uninstall(settings_path: str = DEFAULT_SETTINGS, hook_cmd: str = DEFAULT_CMD, dry_run: bool = False) -> bool:
    """우리 명령이 든 항목만 뺀다. 같은 이벤트의 다른 훅은 그대로, 빈 이벤트 키는 지운다."""
    data = _load(settings_path)
    hooks = data.get("hooks", {})
    changed = False
    for ev in EVENTS:
        kept = [e for e in hooks.get(ev, []) if not _ours(e, hook_cmd)]
        if len(kept) != len(hooks.get(ev, [])):
            changed = True
            if kept:
                hooks[ev] = kept
            else:
                hooks.pop(ev, None)
    if changed and not dry_run:
        _write(settings_path, data)
    return changed


if __name__ == "__main__":
    args = set(sys.argv[1:])
    dry = "--dry-run" in args
    if "--uninstall" in args:
        changed = uninstall(dry_run=dry)
        print(("해제 예정" if dry else "해제됨") if changed else "등록된 것이 없음 (변경 없음)")
    else:
        if dry:
            missing = plan_install(_load(DEFAULT_SETTINGS))
            print(f"등록 예정: {', '.join(missing)}" if missing else "이미 등록돼 있음 (변경 없음)")
        else:
            print("등록됨" if install() else "이미 등록돼 있음 (변경 없음)")
    print(f"대상: {DEFAULT_SETTINGS}")
